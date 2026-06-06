"""
Discord bildirici + (opsiyonel) anlık fiyat zenginleştirme.

- Discord webhook'una embed gönderir, 429 (rate limit) durumunda bekler.
- ENABLE_PRICE açıksa eşleşen hisselerin son fiyatı/günlük değişimi embed'e eklenir
  (yfinance, '.IS' eki ile). yfinance bloklayıcı olduğundan ayrı thread'de çağrılır.
"""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from filters import NewsItem, CATEGORY_COLORS
from inference import Inference

log = logging.getLogger("notifier")

_TRT = timezone(timedelta(hours=3))  # Türkiye saati (tz'siz tarihler için varsayım)

_YON_GORUNUM = {
    "pozitif": "🟢 **Pozitif**",
    "negatif": "🔴 **Negatif**",
    "karisik": "🟡 **Karışık**",
    "belirsiz": "⚪ **Belirsiz**",
}

_price_cache: dict[str, tuple[float, str]] = {}  # ticker -> (zaman, metin)
_PRICE_TTL = 60.0


async def enrich_prices(tickers: set[str]) -> dict[str, str]:
    """Her hisse için '12.34 TL (%1.2)' gibi kısa metin döndürür (best-effort)."""
    out: dict[str, str] = {}
    now = time.time()
    to_fetch = []
    for t in tickers:
        c = _price_cache.get(t)
        if c and now - c[0] < _PRICE_TTL:
            out[t] = c[1]
        else:
            to_fetch.append(t)
    if to_fetch:
        fetched = await asyncio.to_thread(_yf_prices, to_fetch)
        for t, txt in fetched.items():
            _price_cache[t] = (now, txt)
            out[t] = txt
    return out


def _yf_prices(tickers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        import yfinance as yf
    except Exception:
        return out
    for t in tickers:
        try:
            tk = yf.Ticker(f"{t}.IS")
            fi = getattr(tk, "fast_info", {})
            last = fi.get("last_price") or fi.get("lastPrice")
            prev = fi.get("previous_close") or fi.get("previousClose")
            if last:
                if prev:
                    chg = (last - prev) / prev * 100
                    out[t] = f"{last:.2f} TL ({chg:+.2f}%)"
                else:
                    out[t] = f"{last:.2f} TL"
        except Exception as ex:
            log.debug("Fiyat alınamadı %s: %s", t, ex)
    return out


def _parse_published(published: str) -> datetime | None:
    """RSS/KAP tarih metnini datetime'a çevirir (Discord timestamp için); olmazsa None."""
    if not published:
        return None
    s = published.strip()
    # KAP bazen epoch milisaniye döndürür
    if s.isdigit():
        try:
            ts = int(s)
            if ts > 10**12:  # milisaniye
                ts //= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OverflowError):
            return None
    # RFC 2822 (çoğu RSS): "Fri, 06 Jun 2026 14:30:00 +0300"
    try:
        dt = parsedate_to_datetime(s)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=_TRT)
    except Exception:
        pass
    # ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=_TRT)
    except ValueError:
        pass
    # KAP biçimleri: "06.06.2026 14:30(:00)"
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=_TRT)
        except ValueError:
            continue
    return None


def build_embed(item: NewsItem, prices: dict[str, str] | None = None,
                inference: Inference | None = None) -> dict:
    color = CATEGORY_COLORS.get(item.category, CATEGORY_COLORS["default"])
    fields = []

    # Çıkarım: etki yönü + nedenler + etkilenmesi beklenen hedefler
    if inference:
        satirlar = [_YON_GORUNUM.get(inference.yon, inference.yon)]
        if inference.nedenler:
            satirlar[0] += " — " + ", ".join(inference.nedenler[:4])
        if inference.hedefler:
            hedef = ", ".join(f"`{h}`" for h in inference.hedefler)
            satirlar.append(f"**Etkilenmesi beklenen:** {hedef}")
        fields.append({"name": "📌 Olası Etki (tahmini)",
                       "value": "\n".join(satirlar)[:1024], "inline": False})

    # Fiyat varsa ayrı alanda göster (hisse kodları zaten çıkarımda listeleniyor)
    if item.tickers and prices:
        val = "\n".join(f"**{t}**: {prices.get(t, '—')}" for t in sorted(item.tickers))
        fields.append({"name": "📈 Anlık Fiyat", "value": val[:1024], "inline": False})
    elif item.tickers and not inference:
        val = ", ".join(f"`{t}`" for t in sorted(item.tickers))
        fields.append({"name": "İlgili Hisseler", "value": val[:1024], "inline": False})

    desc = item.summary[:500] if item.summary else ""
    embed = {
        "title": item.title[:256] or "(başlıksız)",
        "description": desc,
        "color": color,
        "fields": fields,
        "footer": {"text": item.source},
    }
    # Tarih/saat: ayrıştırılabiliyorsa Discord'un yerel saat gösterimini kullan,
    # olmuyorsa ham metni footer'a ekle.
    dt = _parse_published(item.published)
    if dt:
        embed["timestamp"] = dt.isoformat()
    elif item.published:
        embed["footer"]["text"] = f"{item.source}  •  {item.published}"
    if item.url:
        embed["url"] = item.url
    return embed


async def send(session: aiohttp.ClientSession, webhook_url: str, embed: dict) -> bool:
    payload = {"embeds": [embed]}
    for attempt in range(4):
        try:
            async with session.post(webhook_url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status in (200, 204):
                    return True
                if r.status == 429:  # rate limit
                    body = await r.json(content_type=None)
                    wait = float(body.get("retry_after", 1.0))
                    log.info("Discord rate limit, %.1fs bekleniyor", wait)
                    await asyncio.sleep(wait + 0.2)
                    continue
                log.warning("Discord beklenmeyen durum %s", r.status)
                return False
        except Exception as ex:
            log.warning("Discord gönderim hatası (deneme %d): %s", attempt + 1, ex)
            await asyncio.sleep(1.5)
    return False
