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
    # --- İş geliştirme ---
    ("ihale", 2, "is"),
    ("sözleşme", 2, "is"),
    ("yeni sözleşme", 3, "is"),
    ("ihracat", 2, "is"),
    ("yatırım teşvik", 2, "is"),
    ("spk", 2, "regülasyon"),
    ("kap", 1, "regülasyon"),
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

_COMPILED = [(re.compile(re.escape(fold(k))), w, c) for k, w, c in KEYWORD_WEIGHTS]
_TICKER_BONUS = 2          # eşleşen her hisse için puan
_TICKER_BONUS_CAP = 6      # hisse puanı tavanı


@dataclass
class NewsItem:
    """Tüm kaynaklar için ortak haber modeli."""
    source: str
    uid: str          # tekilleştirme için kararlı kimlik (link veya KAP id)
    title: str
    summary: str
    url: str
    published: str = ""
    # filtre çıktıları (doldurulur)
    tickers: set[str] = field(default_factory=set)
    score: int = 0
    category: str = "default"
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary}"


def evaluate(item: NewsItem) -> NewsItem:
    """Haberi puanlar; tickers/score/category/matched_keywords alanlarını doldurur."""
    item.tickers = find_tickers(item.text)

    score = min(len(item.tickers) * _TICKER_BONUS, _TICKER_BONUS_CAP)
    low = fold(item.text)
    cat_scores: dict[str, int] = {}
    matched: list[str] = []

    for pattern, weight, category in _COMPILED:
        if pattern.search(low):
            score += weight
            cat_scores[category] = cat_scores.get(category, 0) + weight
            matched.append(pattern.pattern.replace("\\", ""))

    item.score = score
    item.matched_keywords = matched
    # baskın kategori = en yüksek puan toplayan
    item.category = max(cat_scores, key=cat_scores.get) if cat_scores else "default"
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
