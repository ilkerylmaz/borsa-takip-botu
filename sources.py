"""
Haber kaynakları.

İki kaynak:
  - RSS: feedparser ile, aiohttp üzerinden indirilip ayrıştırılır.
  - KAP: kap.org.tr iç uç noktasından son bildirimler (opsiyonel).

Her kaynak ortak `NewsItem` listesi döndürür. Ölü/erişilemeyen kaynak
sessizce atlanır (bot çalışmaya devam eder).

NOT: Aşağıdaki varsayılan RSS adreslerini kendi güvendiğin kaynaklarla
DOĞRULA/DEĞİŞTİR. Çalışmayan adres otomatik atlanır.
"""

from __future__ import annotations
import asyncio
import logging
import os
import time
from urllib.parse import quote

import aiohttp
import feedparser

from filters import NewsItem
from textnorm import fold

log = logging.getLogger("sources")

# ---------------------------------------------------------------------------
# Hedefli arama feed'leri (Google News RSS).
# Genel ekonomi feed'leri SPK onayı / halka arz gibi olay haberlerini güvenilir
# taşımıyor (teşhis: SPK halka arz onayı hiç çekilmemişti). Google News araması
# bu boşluğu kapatır: sorgu BIST-olay terimlerine kilitli olduğundan sorgudan
# gelen her haber doğal bir alaka sinyali taşır -> QUERY_FEED_BONUS.
# `when:1d` son 24 saate kısar (10 dk'lık cron penceresi için bol bol yeterli).
# ---------------------------------------------------------------------------
GNEWS_QUERIES = [
    '"halka arz"',
    "SPK onay",
    "temettü",
    # "bedelsiz" tek başına futbol transferlerini de çekiyor ("bedelsiz transfer");
    # "bedelsiz sermaye" / "bedelli sermaye" borsa bağlamına kilitler.
    '"bedelsiz sermaye" OR "bedelli sermaye" OR "sermaye artırımı"',
    '"pay geri alım" OR "geri alım programı"',
    # Piyasa tedbirleri: bu terimler tek anlamlı (finans dışı kullanım yok)
    '"brüt takas" OR "açığa satış yasağı" OR "kredili işlem yasağı" OR VBTS',
    # "devre kesici" elektrik ürünlerinde de geçer; borsa bağlamına kilitle
    '"devre kesici" (borsa OR hisse OR endeks)',
]

_GNEWS_PREFIX = "https://news.google.com/rss/"
QUERY_FEED_BONUS = 2  # sorgu feed'inden gelen habere eklenen alaka puanı

# Google News sonuçlarından elenecek yayıncılar (fold'lanmış alt-dize eşleşmesi):
# kripto siteleri ve BIST ile ilgisiz yabancı içerik çevirileri. Kuru-çalıştırma
# testinde gözlenen gürültü kaynakları; gerektikçe genişlet.
GNEWS_PUBLISHER_BLOCKLIST = (
    "coin", "kripto", "bitcoin", "phemex", "paribu", "beincrypto",
    "vietnam", "invezz", "winally", "traders union", "firstonline", "cgtn",
)


def gnews_url(query: str) -> str:
    """Google News RSS arama adresi üretir (Türkçe/TR sürümü)."""
    return f"{_GNEWS_PREFIX}search?q={quote(query + ' when:1d')}&hl=tr&gl=TR&ceid=TR:tr"


DEFAULT_RSS_FEEDS = [
    "https://www.bloomberght.com/rss",
    "https://www.hurriyet.com.tr/rss/ekonomi",  # eski bigpara feed'i 404'a düştü; yerine Hürriyet Ekonomi
    "https://www.dunya.com/rss",
    "https://tr.investing.com/rss/news.rss",
    # KAP WAF tarafından engellendiği için (bkz. fetch_kap), KAP kapsamını RSS'ten
    # telafi eden borsa-odaklı bir kaynak. (finansgundem.com kardeş sitesi neredeyse
    # birebir aynı içeriği farklı URL'le yayınladığından eklenmedi — çift haber olurdu.)
    "https://www.borsagundem.com/rss",
] + [gnews_url(q) for q in GNEWS_QUERIES]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BIST-HaberBot/1.0)",
    "Accept": "application/json, text/xml, */*",
}

# KAP ana sayfasındaki son bildirim listesini döndüren iç uç nokta.
# KAP yapısını değiştirirse burayı güncelle ya da `pykap` kütüphanesine geç.
KAP_DISCLOSURES_URL = "https://www.kap.org.tr/tr/api/disclosures"
KAP_ITEM_BASE = "https://www.kap.org.tr/tr/Bildirim/"

_kap_uyarildi = False  # aynı KAP uyarısını her turda tekrarlamamak için


def _yayinci_eki_kirp(title: str, publisher: str) -> str:
    """Google News başlık sonundaki ' - Yayıncı' ekini kırpar (eşleşiyorsa)."""
    if publisher and " - " in title:
        govde, kuyruk = title.rsplit(" - ", 1)
        if fold(kuyruk) == fold(publisher):
            return govde.strip()
    return title


async def _fetch_one_rss(session: aiohttp.ClientSession, url: str) -> list[NewsItem]:
    """Tek RSS feed'ini çeker; ölü feed boş liste döndürür (asla raise etmez)."""
    items: list[NewsItem] = []
    try:
        async with session.get(url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
            raw = await r.read()
        parsed = feedparser.parse(raw)
        source_name = parsed.feed.get("title", url) if parsed.feed else url
        gnews = url.startswith(_GNEWS_PREFIX)
        for e in parsed.entries:
            link = e.get("link", "")
            title = _clean(e.get("title", ""))[:256]
            summary = _clean(e.get("summary", ""))
            source, bonus = source_name, 0
            if gnews:
                # Google News girdisi: gerçek yayıncı <source> etiketinde; başlık
                # ' - Yayıncı' eki taşır; özet diğer yayıncıların başlıklarını da
                # içerebildiğinden (yanlış kelime eşleşmesi riski) kullanılmaz.
                publisher = (e.get("source") or {}).get("title") or ""
                if any(b in fold(publisher) for b in GNEWS_PUBLISHER_BLOCKLIST):
                    continue
                title = _yayinci_eki_kirp(title, publisher)
                source = publisher or "Google Haberler"
                summary = ""
                bonus = QUERY_FEED_BONUS
            items.append(
                NewsItem(
                    source=source,
                    uid=e.get("id") or link,
                    title=title,
                    summary=summary,
                    url=link,
                    published=e.get("published", ""),
                    source_bonus=bonus,
                )
            )
    except Exception as ex:  # ölü feed -> atla
        log.warning("RSS atlandı (%s) (%s: %s)", url, type(ex).__name__, ex)
    return items


async def fetch_rss(session: aiohttp.ClientSession, feeds: list[str]) -> list[NewsItem]:
    """Tüm feed'leri EŞZAMANLI çeker (feed sayısı arttı; sıralı bekleme pahalı)."""
    sonuclar = await asyncio.gather(*(_fetch_one_rss(session, u) for u in feeds))
    return [it for grup in sonuclar for it in grup]


async def fetch_kap(session: aiohttp.ClientSession) -> list[NewsItem]:
    global _kap_uyarildi
    items: list[NewsItem] = []
    try:
        async with session.get(KAP_DISCLOSURES_URL, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)
    except Exception as ex:
        # NOT: KAP'ın yeni sitesi (Next.js) bildirim API'sini WAF arkasına aldı ve
        # bot isteklerini engelliyor (zaman aşımı / 666 hata sayfası). Engel sürerse
        # .env'de ENABLE_KAP=0 yaparak denemeyi tamamen kapatabilirsin.
        if not _kap_uyarildi:
            log.warning("KAP çekilemedi (%s: %s) — KAP bot erişimini engelliyor olabilir. "
                        "Sonraki denemeler sessizce sürecek; ENABLE_KAP=0 ile kapatabilirsin",
                        type(ex).__name__, ex)
            _kap_uyarildi = True
        else:
            log.debug("KAP yine çekilemedi (%s)", type(ex).__name__)
        return items
    if _kap_uyarildi:
        log.info("KAP erişimi geri geldi.")
        _kap_uyarildi = False

    rows = data if isinstance(data, list) else data.get("disclosures", data.get("data", []))
    for d in rows or []:
        try:
            idx = str(d.get("disclosureIndex") or d.get("index") or d.get("id") or "")
            title = (d.get("kapTitle") or d.get("title") or d.get("disclosureClass") or "").strip()
            company = (d.get("companyName") or d.get("stockCodes") or d.get("memberName") or "").strip()
            summary_parts = [company, d.get("summary", "") or "", d.get("disclosureClass", "") or ""]
            items.append(
                NewsItem(
                    source="KAP",
                    uid=f"kap-{idx}" if idx else (KAP_ITEM_BASE + title),
                    title=f"[KAP] {company} {title}".strip(),
                    summary=" ".join(p for p in summary_parts if p),
                    url=(KAP_ITEM_BASE + idx) if idx else KAP_ITEM_BASE,
                    published=str(d.get("publishDate") or d.get("date") or ""),
                )
            )
        except Exception as ex:
            log.debug("KAP satırı atlandı: %s", ex)
    return items


def feeds_from_env() -> list[str]:
    """RSS_FEEDS ortam değişkenini ayrıştırır; boşsa DEFAULT_RSS_FEEDS döner.

    Hem main._cfg hem de bot.py'deki /health aynı kaynak listesini buradan alır
    (tek doğruluk kaynağı).
    """
    ham = os.getenv("RSS_FEEDS", "").strip()
    feeds = [u.strip() for u in ham.split(",") if u.strip()]
    return feeds or DEFAULT_RSS_FEEDS


# ---------------------------------------------------------------------------
# Sağlık kontrolü (tanı): kaynakları tek tek dener ve durum raporu döndürür.
# Haber döngüsünü (run_once) ETKİLEMEZ; bot.py'deki /health komutu içindir.
# Her öğe: {tip, ad, url, ok, adet, sure_ms, hata}.
# ---------------------------------------------------------------------------
def _hata_metni(ex: Exception) -> str:
    """İstisnayı okunur kısa metne çevirir; bazı hatalar (TimeoutError) boş
    mesajlıdır — o zaman sondaki boş iki nokta kalmasın diye yalnız tip adı."""
    m = str(ex).strip()
    return f"{type(ex).__name__}: {m}" if m else type(ex).__name__


async def _probe_rss(session: aiohttp.ClientSession, url: str) -> dict:
    """Tek bir RSS kaynağını dener; sağlık sözlüğü döndürür (asla raise etmez)."""
    t0 = time.perf_counter()
    try:
        async with session.get(url, headers=_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            status = r.status
            raw = await r.read()
        parsed = feedparser.parse(raw)
        adet = len(parsed.entries)
        ad = (parsed.feed.get("title") if parsed.feed else None) or url
        sure = round((time.perf_counter() - t0) * 1000)
        if status != 200:
            return {"tip": "RSS", "ad": ad, "url": url, "ok": False,
                    "adet": adet, "sure_ms": sure, "hata": f"HTTP {status}"}
        if adet == 0:
            be = getattr(parsed, "bozo_exception", None)
            hata = f"0 öğe ({type(be).__name__})" if be else "0 öğe (ayrıştırılamadı?)"
            return {"tip": "RSS", "ad": ad, "url": url, "ok": False,
                    "adet": 0, "sure_ms": sure, "hata": hata}
        return {"tip": "RSS", "ad": ad, "url": url, "ok": True,
                "adet": adet, "sure_ms": sure, "hata": None}
    except Exception as ex:
        return {"tip": "RSS", "ad": url, "url": url, "ok": False, "adet": 0,
                "sure_ms": round((time.perf_counter() - t0) * 1000),
                "hata": _hata_metni(ex)}


async def _probe_kap(session: aiohttp.ClientSession) -> dict:
    """KAP uç noktasını dener (warn-once durumundan bağımsız ham test)."""
    t0 = time.perf_counter()
    try:
        async with session.get(KAP_DISCLOSURES_URL, headers=_HEADERS,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            status = r.status
            data = await r.json(content_type=None)
        sure = round((time.perf_counter() - t0) * 1000)
        if status != 200:
            return {"tip": "KAP", "ad": "KAP", "url": KAP_DISCLOSURES_URL, "ok": False,
                    "adet": 0, "sure_ms": sure, "hata": f"HTTP {status}"}
        rows = data if isinstance(data, list) else data.get("disclosures", data.get("data", []))
        adet = len(rows or [])
        return {"tip": "KAP", "ad": "KAP", "url": KAP_DISCLOSURES_URL,
                "ok": adet > 0, "adet": adet, "sure_ms": sure,
                "hata": None if adet else "0 bildirim"}
    except Exception as ex:
        return {"tip": "KAP", "ad": "KAP", "url": KAP_DISCLOSURES_URL, "ok": False,
                "adet": 0, "sure_ms": round((time.perf_counter() - t0) * 1000),
                "hata": _hata_metni(ex)}


async def probe(session: aiohttp.ClientSession, feeds: list[str],
                enable_kap: bool = True) -> list[dict]:
    """Tüm kaynakları eşzamanlı dener ve sağlık raporu listesi döndürür."""
    gorevler = [_probe_rss(session, u) for u in feeds]
    if enable_kap:
        gorevler.append(_probe_kap(session))
    return list(await asyncio.gather(*gorevler))


def _clean(raw: str) -> str:
    """
    RSS metnindeki kaba HTML'i ve HTML varlıklarını temizler.
    Bazı kaynaklar varlıkları çift kodlar ('&amp;#039;' -> '&#039;' -> "'"),
    bu yüzden değişmez hale gelene dek birkaç tur çözülür.
    """
    import html
    import re
    text = re.sub(r"<[^>]+>", " ", raw or "")
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        # çözülen varlıklardan ortaya çıkan etiketleri de süpür
        text = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", text).strip()[:500]
