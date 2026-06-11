"""
Kural tabanlı haber çıkarımı (LLM'siz).

Eşleşen kalıplardan haberin beklenen etki YÖNÜNÜ (pozitif/negatif/karışık/belirsiz)
ve etkilenmesi beklenen HEDEFLERİ (hisse kodları ya da piyasa geneli) tahmin eder.
Kaba bir tahmindir; embed'de "Olası Etki (tahmini)" olarak sunulur ve
yatırım tavsiyesi değildir.

NOT: Kalıplar fold() ile katlanmış metne uygulanır — bu yüzden Türkçe
karaktersiz (ASCII) yazılır: "temettü" yerine "temettu" gibi.
Daha isabetli (LLM tabanlı) çıkarım için filters.llm_classify() iskeleti duruyor.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field

from filters import NewsItem
from textnorm import fold

# (katlanmış regex, yön, ağırlık, etiket)
# Etiket embed'de "neden" olarak gösterilir; istediğin kadar satır ekleyebilirsin.
SENTIMENT_PATTERNS: list[tuple[str, str, int, str]] = [
    # --- Pozitif ---
    (r"temettu|kar payi", "pozitif", 3, "temettü/kâr payı"),
    (r"pay geri alim|geri alim program", "pozitif", 3, "pay geri alımı"),
    (r"bedelsiz", "pozitif", 2, "bedelsiz sermaye artırımı"),
    (r"rekor (net )?(kar|ciro|satis|uretim|ihracat)", "pozitif", 3, "rekor finansal sonuç"),
    (r"net kar.{0,15}art", "pozitif", 2, "kâr artışı"),
    (r"yeni (sozlesme|siparis|anlasma)|sozlesme imzala|ihale.{0,10}kazan", "pozitif", 2, "yeni iş/sözleşme"),
    (r"not.{0,8}(artir|yukselt)|not artirimi", "pozitif", 3, "kredi notu artışı"),
    (r"yatirim tesvik", "pozitif", 2, "yatırım teşviki"),
    (r"is ?birligi|ortaklik anlasmasi", "pozitif", 1, "iş birliği/ortaklık"),
    (r"birlesme|devralma|satin alma", "pozitif", 1, "birleşme/devralma"),
    # --- Negatif ---
    (r"net zarar|zarar acikla", "negatif", 3, "zarar açıklaması"),
    (r"not.{0,8}(indir|dusur)|not indirimi", "negatif", 3, "kredi notu indirimi"),
    (r"yaptirim", "negatif", 2, "yaptırım riski"),
    (r"iflas|konkordato", "negatif", 3, "iflas/konkordato"),
    (r"idari para cezasi|ceza kesti|sorusturma (acildi|baslat)", "negatif", 2, "ceza/soruşturma"),
    (r"uretim.{0,6}(ara verdi|durdur)|faaliyet.{0,6}durdur", "negatif", 2, "üretim/faaliyet durması"),
    (r"bedelli", "negatif", 1, "bedelli sermaye artırımı"),
    (r"grev|is birakma", "negatif", 2, "grev/iş bırakma"),
    (r"net kar.{0,15}(dustu|geriledi|azal)", "negatif", 2, "kâr düşüşü"),
    # --- Piyasa tedbirleri: kısıt haberi genelde baskı yaratır ---
    (r"brut takas", "negatif", 2, "brüt takas tedbiri"),
    (r"aciga satis yasag", "negatif", 2, "açığa satış yasağı"),
    (r"kredili islem yasag", "negatif", 2, "kredili işlem yasağı"),
    (r"devre kesici", "negatif", 1, "devre kesici"),
    (r"vbts|volatilite bazli", "negatif", 1, "VBTS tedbiri"),
    (r"yakin izleme", "negatif", 2, "yakın izleme pazarı"),
    (r"islem siras\w*.{0,12}durdur", "negatif", 2, "işlem sırası durdurma"),
    (r"islem (yasagi|kisitlama)", "negatif", 2, "işlem kısıtlaması"),
    (r"tedbir (karari|uygulan)", "negatif", 1, "piyasa tedbiri"),
    # Tedbirin kalkması pozitiftir; ağırlık 3, "kredili islem yasag" gibi
    # negatif kalıpla aynı haberde çakışırsa pozitif yön baskın çıksın diye
    (r"yasa(k|gi|klar).{0,10}kalk|tedbir(ler)?.{0,12}sona er", "pozitif", 3, "tedbir kaldırılıyor"),
    # --- Yönü bağlama bağlı (sadece etiket üretir, yön puanı vermez) ---
    (r"halka arz", "notr", 0, "halka arz"),
    (r"faiz karari|politika faizi|ppk", "notr", 0, "faiz kararı"),
    (r"enflasyon|tufe", "notr", 0, "enflasyon verisi"),
    (r"bilanco|finansal sonuc", "notr", 0, "bilanço/finansal sonuç"),
]

_COMPILED = [(re.compile(p), yon, w, etiket) for p, yon, w, etiket in SENTIMENT_PATTERNS]


@dataclass
class Inference:
    """Bir haber için kural tabanlı çıkarım sonucu."""
    yon: str                                       # pozitif | negatif | karisik | belirsiz
    nedenler: list[str] = field(default_factory=list)   # eşleşen etiketler
    hedefler: list[str] = field(default_factory=list)   # hisse kodları veya "BIST geneli"


def infer(item: NewsItem) -> Inference | None:
    """
    Haberden kaba bir etki çıkarımı üretir.
    Gösterecek hiçbir şey yoksa (ne neden ne hedef) None döner.
    evaluate() çağrılmış olmalı (tickers/category dolu).
    """
    low = fold(item.text)
    pos = neg = 0
    nedenler: list[str] = []

    for pattern, yon, weight, etiket in _COMPILED:
        if pattern.search(low):
            if yon == "pozitif":
                pos += weight
            elif yon == "negatif":
                neg += weight
            if etiket not in nedenler:
                nedenler.append(etiket)

    if pos > neg:
        sonuc_yon = "pozitif"
    elif neg > pos:
        sonuc_yon = "negatif"
    elif pos and neg:
        sonuc_yon = "karisik"
    else:
        sonuc_yon = "belirsiz"

    hedefler = sorted(item.tickers)
    if not hedefler and item.category == "makro":
        hedefler = ["BIST geneli"]

    if not nedenler and not hedefler:
        return None
    return Inference(yon=sonuc_yon, nedenler=nedenler, hedefler=hedefler)
