"""
Filtreleme katmanı.

Bir haber için:
  - metinde geçen BIST hisseleri bulunur (tickers.find_tickers),
  - makro + şirket bazlı anahtar kelimeler ağırlıklandırılır,
  - toplam bir ÖNEM SKORU üretilir.

Skor MIN_RELEVANCE_SCORE eşiğinin altındaysa haber gönderilmez.
Ağırlıkları KEYWORD_WEIGHTS üzerinden serbestçe ayarlayabilirsin.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field

from tickers import find_tickers
from textnorm import fold

# (anahtar kelime, ağırlık, kategori)
# Kategori sadece etiketleme/renk için; istediğin kadar satır ekleyebilirsin.
KEYWORD_WEIGHTS: list[tuple[str, int, str]] = [
    # --- Makro / piyasa geneli ---
    ("politika faizi", 4, "makro"),
    ("faiz kararı", 4, "makro"),
    ("ppk", 4, "makro"),
    ("merkez bankası", 3, "makro"),
    ("tcmb", 3, "makro"),
    ("enflasyon", 3, "makro"),
    ("tüfe", 3, "makro"),
    ("üfe", 2, "makro"),
    ("kredi notu", 3, "makro"),
    ("moody's", 2, "makro"),
    ("fitch", 2, "makro"),
    ("s&p", 2, "makro"),
    ("yaptırım", 3, "makro"),
    # --- Şirket bazlı / önemli ---
    ("bilanço", 4, "sirket"),
    ("finansal sonuç", 4, "sirket"),
    ("net kar", 3, "sirket"),
    ("net zarar", 3, "sirket"),
    ("temettü", 4, "sirket"),
    ("kar payı", 4, "sirket"),
    ("kâr payı", 4, "sirket"),
    ("halka arz", 4, "sirket"),
    ("bedelli", 3, "sirket"),
    ("bedelsiz", 3, "sirket"),
    ("sermaye artırımı", 3, "sirket"),
    ("pay geri alım", 4, "sirket"),
    ("geri alım", 3, "sirket"),
    ("birleşme", 4, "ma"),
    ("devralma", 4, "ma"),
    ("satın alma", 3, "ma"),
    ("hisse devri", 3, "ma"),
    ("pay devri", 3, "ma"),
    ("ortaklık", 2, "ma"),
    ("pay alım teklifi", 4, "ma"),
    # --- Halka arz süreci (SPK bülteni / izahname / talep toplama) ---
    ("izahname", 2, "sirket"),
    ("talep toplama", 3, "sirket"),
    ("borsada işlem görmeye", 3, "sirket"),
    ("işlem görmeye başlayacak", 3, "sirket"),
    ("kotasyon", 2, "regülasyon"),
    ("konkordato", 2, "sirket"),
    # --- İş geliştirme ---
    ("ihale", 2, "is"),
    ("sözleşme", 2, "is"),
    ("yeni sözleşme", 3, "is"),
    ("ihracat", 2, "is"),
    ("yatırım teşvik", 2, "is"),
    ("spk", 2, "regülasyon"),
    ("spk onayı", 2, "regülasyon"),
    ("spk bülteni", 2, "regülasyon"),
    ("kap", 1, "regülasyon"),
]

# Gürültü cezaları: SEO/dolgu içerik kalıpları ("X nedir?", "ne zaman açıklanacak?")
# ve BIST dışı piyasa işaretleri. Skoru düşürür ama kategoriyi ETKİLEMEZ.
# Gerçek bir şirket olayı (hisse bonusu + güçlü kelime) cezayı telafi edebilir;
# salt dolgu başlık eşiğin altında kalır.
NEGATIVE_KEYWORDS: list[tuple[str, int]] = [
    ("ne zaman", -3),
    ("nedir", -3),
    ("nasıl yapılır", -3),
    ("nasıl sorgulanır", -3),
    ("nasıl kesilir", -3),
    ("nasıl düzenlenir", -3),
    ("nasıl hesaplanır", -3),
    ("nasıl alınır", -3),
    ("bilmen gereken", -2),
    ("bilmeniz gereken", -2),
    ("canlı borsa", -3),       # dakika dakika canlı yayın sayfaları (her gün aynı)
    ("canlı grafik", -4),      # "X Hisse Senedi Canlı Grafik" türü sayfa spam'i
    ("nasdaq", -3),
    ("nyse", -3),
    ("dow jones", -3),
    ("wall street", -2),
    ("s&p 500", -4),           # "s&p" (kredi notu, +2) endeks haberine de uyar; net etkiyi eksiye çevir
]

# Kategori -> Discord embed rengi (decimal)
CATEGORY_COLORS = {
    "makro": 0xE67E22,       # turuncu
    "sirket": 0x2ECC71,      # yeşil
    "ma": 0x9B59B6,          # mor (M&A)
    "is": 0x3498DB,          # mavi
    "regülasyon": 0x95A5A6,  # gri
    "default": 0x34495E,
}

def _kalip(k: str) -> re.Pattern:
    """Anahtar kelimeyi fold'layıp dereler. Kısa (<=3 harf) kelimeler kelime
    sınırı ister: 'kap' aksi halde 'kapsamında/kapanış' içine eşleşir
    (fold apostrofu koruduğundan \"KAP'a\" yine eşleşir). Uzun kelimelerde
    alt-dize eşleşmesi kasıtlı: 'temettü' -> 'temettüsü' yakalanmalı."""
    f = re.escape(fold(k))
    return re.compile(rf"(?<!\w){f}(?!\w)" if len(fold(k)) <= 3 else f)


# (kalıp, düz fold'lu kelime, ağırlık[, kategori]) — düz kelime log/olay anahtarı için
_COMPILED = [(_kalip(k), fold(k), w, c) for k, w, c in KEYWORD_WEIGHTS]
_COMPILED_NEG = [(_kalip(k), fold(k), w) for k, w in NEGATIVE_KEYWORDS]
_TICKER_BONUS = 2          # eşleşen her hisse için puan
_TICKER_BONUS_CAP = 6      # hisse puanı tavanı
_TKEY_MIN_LEN = 16         # bundan kısa başlık anahtarı çakışma riskli -> tekilleştirmede kullanma


@dataclass
class NewsItem:
    """Tüm kaynaklar için ortak haber modeli."""
    source: str
    uid: str          # tekilleştirme için kararlı kimlik (link veya KAP id)
    title: str
    summary: str
    url: str
    published: str = ""
    # Kaynak katmanının verdiği alaka bonusu: hedefli arama feed'lerinden
    # (Google News sorguları) gelen haber, sorgunun kendisi bir alaka sinyali
    # olduğu için ek puan taşır (sources.QUERY_FEED_BONUS).
    source_bonus: int = 0
    # filtre çıktıları (doldurulur)
    tickers: set[str] = field(default_factory=set)
    score: int = 0
    category: str = "default"
    matched_keywords: list[str] = field(default_factory=list)
    # çapraz-kaynak tekilleştirme anahtarları (evaluate doldurur; boş = kullanma)
    tkey: str = ""   # normalize başlık (aynı haberin birebir yeniden yayını)
    ekey: str = ""   # olay anahtarı: hisseler + en ağır anahtar kelime (farklı başlıklı aynı olay)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary}"


def baslik_anahtari(title: str) -> str:
    """Başlığı tekilleştirme anahtarına indirger: fold + yalnız harf/rakam.

    Aynı haberin başka kaynakta birebir yeniden yayınını yakalar (Google News
    feed'i ile doğrudan feed aynı makaleyi farklı URL'le verir). Çok kısa
    anahtar çakışma riskli olduğundan boş döner (tekilleştirmede kullanılmaz).
    """
    key = "".join(c for c in fold(title) if c.isalnum())
    return key if len(key) >= _TKEY_MIN_LEN else ""


# Olay anahtarındaki varlık adaylarından elenecek jenerik/kurumsal kelimeler.
# Başlangıç eşleşmesi (startswith) ile uygulanır: "borsa", "borsada", "borsaya"...
# Amaç: "Beta Enerji" ile "Enda Enerji"nin salt "enerji" üzerinden aynı olay
# sayılmasını önlemek değil (onu örtüşme ORANI çözer), başlık-düzeni gereği
# büyük harfle yazılan jenerik kelimelerin varlık sanılmasını önlemek.
_ENTITY_STOP = (
    "spk", "kap", "tcmb", "bist", "borsa", "turkiye", "istanbul",
    "sirket", "hisse", "pay", "lot", "fiyat", "onay", "karar", "yeni",
    "son", "dakika", "bedelsiz", "bedelli", "sermaye", "temettu", "halka",
    "arz", "yuzde", "milyon", "milyar", "resmi", "bugun", "yarin",
)


def _varliklar(title: str, tickers: set[str]) -> set[str]:
    """Başlıktaki olası özel adları (büyük harfle başlayan / tamamı büyük
    kelimeler), sayıları (oran/fiyat — 'yüzde 100' ile 'yüzde 463' farklı
    olaydır) ve eşleşen hisse kodlarını fold'lanmış küme olarak döndürür.
    Aynı olayın farklı yayıncılardaki farklı başlıklarını eşlemek için."""
    ents: set[str] = {fold(t) for t in tickers}
    for tok in re.findall(r"\w+", title, re.UNICODE):
        if tok.isdigit():
            if len(tok) >= 2:
                ents.add(tok)
            continue
        if len(tok) < 3 or not tok[0].isupper():
            continue
        f = fold(tok)
        if not any(f.startswith(s) for s in _ENTITY_STOP):
            ents.add(f)
    return ents


def olay_benzer(ekey_a: str, ekey_b: str) -> bool:
    """İki olay anahtarı aynı olayı mı anlatıyor? Anahtar biçimi:
    'anahtar_kelime|varlık1 varlık2 ...'. Aynı baskın kelime + varlık
    kümelerinin yarıdan fazlası örtüşüyorsa (küçük kümeye göre) aynı olaydır.
    ({spacex} vs {spacex, musk} -> evet; {beta, enerji} vs {enda, enerji} -> hayır)
    """
    kw_a, _, ent_a = ekey_a.partition("|")
    kw_b, _, ent_b = ekey_b.partition("|")
    if not kw_a or kw_a != kw_b:
        return False
    a, b = set(ent_a.split()), set(ent_b.split())
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) > 0.5


def evaluate(item: NewsItem) -> NewsItem:
    """Haberi puanlar; tickers/score/category/matched_keywords/tkey/ekey alanlarını doldurur."""
    item.tickers = find_tickers(item.text)

    score = min(len(item.tickers) * _TICKER_BONUS, _TICKER_BONUS_CAP) + item.source_bonus
    low = fold(item.text)
    cat_scores: dict[str, int] = {}
    matched: list[str] = []
    pozitif: list[str] = []   # olay anahtarı için (cezasızlar)
    best_kw, best_w = "", 0   # olay anahtarı için en ağır kelime

    for pattern, kw, weight, category in _COMPILED:
        if pattern.search(low):
            score += weight
            cat_scores[category] = cat_scores.get(category, 0) + weight
            matched.append(kw)
            pozitif.append(kw)
            if weight > best_w:
                best_w, best_kw = weight, kw

    for pattern, kw, weight in _COMPILED_NEG:
        if pattern.search(low):
            score += weight  # weight negatif
            matched.append(f"-{kw}")

    item.score = score
    item.matched_keywords = matched
    # baskın kategori = en yüksek puan toplayan (cezalar kategoriyi etkilemez)
    item.category = max(cat_scores, key=cat_scores.get) if cat_scores else "default"
    # tekilleştirme anahtarları: normalize başlık + olay (baskın kelime|varlıklar)
    item.tkey = baslik_anahtari(item.title)
    ents = _varliklar(item.title, item.tickers) if best_kw else set()
    if not ents and len(set(pozitif)) >= 2:
        # Varlıksız (şirket adı vermeyen) başlık: "SPK'dan bir şirketin halka
        # arzına onay" gibi. Eşleşen kelime KÜMESİ olay imzası olur — aynı
        # bülteni anlatan anonim varyantlar tek mesaja iner. Tek kelimelik
        # eşleşme imza için fazla genel olduğundan kullanılmaz.
        ents = {k.replace(" ", "") for k in set(pozitif)}
    item.ekey = f"{best_kw}|{' '.join(sorted(ents))}" if ents else ""
    return item


def passes(item: NewsItem, min_score: int) -> bool:
    return item.score >= min_score


# ---------------------------------------------------------------------------
# OPSİYONEL: LLM ile sınıflandırma (varsayılan kapalı).
# Anthropic API anahtarını ortam değişkenine koyup aşağıdaki gövdeyi doldurarak
# "bu haber hangi hisseyi nasıl (pozitif/negatif/nötr) etkiler" sınıflandırması
# ekleyebilirsin. Kural tabanlı skoru tamamlar.
# ---------------------------------------------------------------------------
def llm_classify(item: NewsItem) -> dict | None:
    """
    Geriye ör. {"impact": "pozitif", "confidence": 0.8, "explain": "..."} döndürür.
    Şu an devre dışı; bağlamak istersen ANTHROPIC_API_KEY ile anthropic SDK çağrısı ekle.
    """
    return None
