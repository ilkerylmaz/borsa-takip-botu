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
import logging

import aiohttp
import feedparser

from filters import NewsItem

log = logging.getLogger("sources")

DEFAULT_RSS_FEEDS = [
    "https://www.bloomberght.com/rss",
    "https://bigpara.hurriyet.com.tr/rss/",
    "https://www.dunya.com/rss",
    "https://tr.investing.com/rss/news.rss",
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BIST-HaberBot/1.0)",
    "Accept": "application/json, text/xml, */*",
}

# KAP ana sayfasındaki son bildirim listesini döndüren iç uç nokta.
# KAP yapısını değiştirirse burayı güncelle ya da `pykap` kütüphanesine geç.
KAP_DISCLOSURES_URL = "https://www.kap.org.tr/tr/api/disclosures"
KAP_ITEM_BASE = "https://www.kap.org.tr/tr/Bildirim/"

_kap_uyarildi = False  # aynı KAP uyarısını her turda tekrarlamamak için


async def fetch_rss(session: aiohttp.ClientSession, feeds: list[str]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for url in feeds:
        try:
            async with session.get(url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
                raw = await r.read()
            parsed = feedparser.parse(raw)
            source_name = parsed.feed.get("title", url) if parsed.feed else url
            for e in parsed.entries:
                link = e.get("link", "")
                items.append(
                    NewsItem(
                        source=source_name,
                        uid=e.get("id") or link,
                        title=_clean(e.get("title", ""))[:256],
                        summary=_clean(e.get("summary", "")),
                        url=link,
                        published=e.get("published", ""),
                    )
                )
        except Exception as ex:  # ölü feed -> atla
            log.warning("RSS atlandı (%s) (%s: %s)", url, type(ex).__name__, ex)
    return items


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
