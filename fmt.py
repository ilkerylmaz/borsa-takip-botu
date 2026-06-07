"""
Türkçe sayı biçimleme yardımcıları.

Python'ın `:,` biçimi ABD düzeni üretir (1,234,567.89); burada
ayraçlar takas edilerek Türkçe düzene çevrilir (1.234.567,89).
Saf ve bağımsız modül: charting.py'nin tek başına debug yolu da
discord'a bulaşmadan import edebilsin diye ayrı tutuldu.
"""

from __future__ import annotations


def tr_sayi(x: float, ondalik: int = 2) -> str:
    """1234567.89 -> '1.234.567,89' (Türkçe biçim)."""
    s = f"{x:,.{ondalik}f}"  # önce ABD biçimi: 1,234,567.89
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def tr_buyuk(x: float) -> str:
    """Büyük tutarları kısalt: 4.08e11 -> '408,00 Mr TL' (Mr=Milyar, Mn=Milyon)."""
    if x >= 1e9:
        return f"{tr_sayi(x / 1e9)} Mr TL"
    if x >= 1e6:
        return f"{tr_sayi(x / 1e6)} Mn TL"
    return f"{tr_sayi(x, 0)} TL"


def tr_yuzde(x: float) -> str:
    """1.23 -> '+1,23%' (işaret her zaman gösterilir)."""
    return f"{x:+.2f}%".replace(".", ",")
