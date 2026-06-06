"""
BIST hisse kodu -> şirket adı / takma adlar eşleştirmesi.

Buradaki liste sık işlem gören başlıca hisseleri kapsar (başlangıç seti).
TAM liste için iki yol var:
  1) `borsapy` veya `isyatirimhisse` ile tüm sembolleri çekip bu sözlüğü doldurmak,
  2) elle bir CSV (kod;ad) hazırlayıp `load_from_csv()` ile yüklemek.

Eşleştirme iki şekilde yapılır:
  - Hisse KODU (THYAO gibi): kelime sınırıyla, büyük harf.
  - Şirket ADI / takma adlar (Türk Hava Yolları, THY gibi): küçük harfe çevirip aranır.
Yanlış pozitifi azaltmak için liste özenle seçildi (örn. günlük dile karışan
kelimelerle çakışan kodlar dahil edilmedi).
"""

from __future__ import annotations
import csv
import re

from textnorm import fold

# kod -> [takma adlar]  (kodun kendisi otomatik eklenir)
TICKERS: dict[str, list[str]] = {
    # Bankalar
    "AKBNK": ["akbank"],
    "GARAN": ["garanti bbva", "garanti bankası", "garanti bankasi"],
    "ISCTR": ["iş bankası", "is bankasi", "türkiye iş bankası"],
    "YKBNK": ["yapı kredi", "yapi kredi", "yapı ve kredi bankası"],
    "HALKB": ["halkbank", "halk bankası", "halk bankasi"],
    "VAKBN": ["vakıfbank", "vakifbank", "vakıflar bankası"],
    "TSKB": ["tskb"],
    "ALBRK": ["albaraka türk", "albaraka"],
    "SKBNK": ["şekerbank", "sekerbank"],
    "QNBFK": ["qnb finansbank", "finansbank"],
    # Holdingler
    "KCHOL": ["koç holding", "koc holding"],
    "SAHOL": ["sabancı holding", "sabanci holding"],
    "TKFEN": ["tekfen holding", "tekfen"],
    "ENKAI": ["enka inşaat", "enka insaat", "enka"],
    "ALARK": ["alarko holding", "alarko"],
    "DOHOL": ["doğan holding", "dogan holding"],
    "GSDHO": ["gsd holding"],
    "AGHOL": ["ag anadolu grubu", "anadolu grubu holding"],
    # Havacılık / Ulaşım
    "THYAO": ["türk hava yolları", "turk hava yollari", "thy", "turkish airlines"],
    "PGSUS": ["pegasus"],
    "TAVHL": ["tav havalimanları", "tav havalimanlari", "tav"],
    "CLEBI": ["çelebi", "celebi hava servisi"],
    # Savunma / Teknoloji
    "ASELS": ["aselsan"],
    "OTKAR": ["otokar"],
    "KONTR": ["kontrolmatik"],
    "SMRTG": ["smart güneş", "smart gunes"],
    "ASTOR": ["astor enerji", "astor"],
    "REEDR": ["reeder"],
    "MIATK": ["mia teknoloji"],
    # Demir-Çelik / Sanayi
    "EREGL": ["ereğli demir çelik", "eregli demir celik", "erdemir"],
    "ISDMR": ["iskenderun demir çelik", "isdemir"],
    "KRDMD": ["kardemir"],
    "BRSAN": ["borusan boru", "borusan"],
    "CEMTS": ["çemtaş", "cemtas"],
    # Otomotiv
    "FROTO": ["ford otosan", "ford otomotiv"],
    "TOASO": ["tofaş", "tofas"],
    "TTRAK": ["türk traktör", "turk traktor"],
    "DOAS": ["doğuş otomotiv", "dogus otomotiv"],
    "ARCLK": ["arçelik", "arcelik"],
    "VESTL": ["vestel"],
    # Petrokimya / Kimya
    "TUPRS": ["tüpraş", "tupras"],
    "PETKM": ["petkim"],
    "SASA": ["sasa polyester", "sasa"],
    "GUBRF": ["gübre fabrikaları", "gubre fabrikalari", "gübretaş"],
    "HEKTS": ["hektaş", "hektas"],
    # Perakende / Gıda
    "BIMAS": ["bim ", "bim mağazalar", "bim magazalar"],
    "MGROS": ["migros"],
    "SOKM": ["şok marketler", "sok marketler", "şok market"],
    "ULKER": ["ülker", "ulker"],
    "CCOLA": ["coca-cola içecek", "coca cola icecek", "cci"],
    "AEFES": ["anadolu efes", "efes"],
    # Telekom
    "TCELL": ["turkcell"],
    "TTKOM": ["türk telekom", "turk telekom"],
    # Enerji / Çimento
    "ENJSA": ["enerjisa"],
    "AKSEN": ["aksa enerji"],
    "ODAS": ["odaş elektrik", "odas elektrik"],
    "OYAKC": ["oyak çimento", "oyak cimento"],
    "CIMSA": ["çimsa", "cimsa"],
    "AKCNS": ["akçansa", "akcansa"],
    # Madencilik
    "KOZAL": ["koza altın", "koza altin"],
    "KOZAA": ["koza anadolu metal", "koza anadolu"],
    "IPEKE": ["ipek doğal enerji", "ipek dogal enerji"],
    # Cam
    "SISE": ["şişecam", "sisecam", "şişe ve cam"],
}


def _build_index(tickers: dict[str, list[str]]):
    """Kod regex'i ve (takma ad -> kod) sözlüğü üretir."""
    codes = sorted(tickers.keys(), key=len, reverse=True)
    code_re = re.compile(r"\b(" + "|".join(map(re.escape, codes)) + r")\b")
    alias_map: dict[str, str] = {}
    for code, aliases in tickers.items():
        for a in aliases:
            alias_map[fold(a)] = code
    return code_re, alias_map


_CODE_RE, _ALIAS_MAP = _build_index(TICKERS)


def find_tickers(text: str) -> set[str]:
    """Verilen metinde geçen BIST hisse kodlarını (set) döndürür."""
    if not text:
        return set()
    found: set[str] = set()
    # 1) Büyük harf kodlar (THYAO, ASELS ...)
    for m in _CODE_RE.findall(text.upper()):
        found.add(m)
    # 2) Şirket adları / takma adlar (Türkçe-duyarlı katlama ile)
    low = fold(text)
    for alias, code in _ALIAS_MAP.items():
        if alias in low:
            found.add(code)
    return found


def load_from_csv(path: str, sep: str = ";") -> None:
    """
    'kod;ad' biçiminde bir CSV ile sözlüğü genişletir (başlık satırı opsiyonel).
    Tam BIST listesini eklemek için kullanışlıdır.
    """
    global _CODE_RE, _ALIAS_MAP
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter=sep):
            if len(row) < 2:
                continue
            code, name = row[0].strip().upper(), row[1].strip().lower()
            if not code.isalpha():
                continue
            TICKERS.setdefault(code, [])
            if name and name not in TICKERS[code]:
                TICKERS[code].append(name)
    _CODE_RE, _ALIAS_MAP = _build_index(TICKERS)
