"""
Türkçe-duyarlı metin normalleştirme.

Eşleştirme amacıyla (görüntüleme için DEĞİL) metni katlar:
Türkçe özel harfleri ASCII karşılığına indirger ve küçük harfe çevirir.
Böylece 'İş', 'ŞİŞECAM', 'İhale' gibi ifadeler doğru eşleşir
(Python'ın varsayılan .lower() davranışı 'İ' -> 'i̇' nedeniyle hatalı eşleşir).
"""

from __future__ import annotations
import unicodedata

_MAP = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ş": "s", "Ş": "s",
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o",
    "â": "a", "Â": "a",
    "î": "i", "Î": "i",
    "û": "u", "Û": "u",
})


def fold(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    return s.translate(_MAP).lower()
