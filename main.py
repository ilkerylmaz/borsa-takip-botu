"""
BIST Haber Botu — ana döngü.

Akış (POLL_INTERVAL_SECONDS'te bir):
  kaynaklardan haber çek  ->  puanla/filtrele  ->  tekilleştir
  ->  (opsiyonel) fiyatla zenginleştir  ->  Discord'a gönder
"""

from __future__ import annotations
import asyncio
import logging
import os

import aiohttp
from dotenv import load_dotenv

import sources
import notifier
from filters import evaluate, passes
from inference import infer
from store import SeenStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def _cfg():
    load_dotenv()
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise SystemExit("DISCORD_WEBHOOK_URL boş. .env dosyasını doldur.")
    feeds = sources.feeds_from_env()
    return {
        "webhook": webhook,
        "interval": int(os.getenv("POLL_INTERVAL_SECONDS", "120")),
        "min_score": int(os.getenv("MIN_RELEVANCE_SCORE", "3")),
        "enable_kap": os.getenv("ENABLE_KAP", "1") == "1",
        "enable_price": os.getenv("ENABLE_PRICE", "1") == "1",
        # RUN_ONCE=1: tek tur çalışıp çık (GitHub Actions gibi zamanlanmış ortamlar için)
        "run_once": os.getenv("RUN_ONCE", "0") == "1",
        "db_path": os.getenv("SEEN_DB_PATH", "seen.db"),
        "feeds": feeds,
    }


async def run_once(session, cfg, store: SeenStore, silent: bool = False) -> int:
    """Bir tur: çek, puanla, gönder. silent=True ise sadece 'görüldü' işaretler, göndermez."""
    items = await sources.fetch_rss(session, cfg["feeds"])
    if cfg["enable_kap"]:
        items += await sources.fetch_kap(session)

    sent = 0
    for item in items:
        if store.is_seen(item.uid):
            continue
        evaluate(item)
        if silent or not passes(item, cfg["min_score"]):
            store.mark(item.uid, item.source, item.title, item.score)  # gürültüyü de işaretle
            continue

        prices = None
        if cfg["enable_price"] and item.tickers:
            prices = await notifier.enrich_prices(item.tickers)

        embed = notifier.build_embed(item, prices, infer(item))
        ok = await notifier.send(session, cfg["webhook"], embed)
        store.mark(item.uid, item.source, item.title, item.score)
        if ok:
            sent += 1
            log.info("Gönderildi [skor %d] %s", item.score, item.title[:80])
        await asyncio.sleep(0.4)  # webhook'u yormamak için nazik aralık
    return sent


async def main():
    cfg = _cfg()
    store = SeenStore(cfg["db_path"])
    log.info("Bot başladı. Aralık=%ss, eşik=%s, KAP=%s, fiyat=%s, %d RSS kaynağı%s",
             cfg["interval"], cfg["min_score"], cfg["enable_kap"],
             cfg["enable_price"], len(cfg["feeds"]),
             " (tek tur)" if cfg["run_once"] else "")

    # Taze veritabanı: ilk tur sessiz geçilir ki akıştaki mevcut haberler
    # toplu spam olarak gönderilmesin (sadece 'görüldü' işaretlenir).
    silent = store.is_empty()
    if silent:
        log.info("seen.db boş: ilk tur sessiz — mevcut haberler işaretlenecek, gönderilmeyecek.")

    async with aiohttp.ClientSession() as session:
        if cfg["run_once"]:
            n = await run_once(session, cfg, store, silent=silent)
            log.info("Tek tur bitti, %d haber gönderildi.", n)
            return
        while True:
            try:
                n = await run_once(session, cfg, store, silent=silent)
                silent = False
                if n:
                    log.info("Bu turda %d haber gönderildi.", n)
            except Exception as ex:
                log.exception("Tur hatası: %s", ex)
            await asyncio.sleep(cfg["interval"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKapatıldı.")
