"""
Kural tabanlı teknik görünüm (LLM'siz).

market.compute_indicators çıktısı olan TAM seri (~2 yıl) üzerinden çalışır —
görüntü penceresi DEĞİL. SMA200/SMA50 ısınma payı gerektirir (indikatörlerle
aynı uyarı: kısa pencerede hesaplanırsa yanlış çıkar). Birkaç teknik sinyali
(uzun/orta vade trend, kesişim, MACD momentumu, RSI, kısa vade getiri)
ağırlıklı toplayıp 5 kademeli bir yön etiketi + güven + gerekçeler üretir.

Kaba bir özettir; embed'de "Teknik Görünüm" olarak sunulur ve yatırım tavsiyesi
değildir. inference.py'nin (haber çıkarımı) fiyat tarafındaki karşılığıdır.

Tek başına debug:  python analiz.py THYAO
"""

from __future__ import annotations
from dataclasses import dataclass, field

import pandas as pd

import fmt  # saf modül (discord bağımsız), Türkçe yüzde biçimi için


@dataclass
class TeknikGorunum:
    """Bir hisse için kural tabanlı teknik görünüm sonucu."""
    yon: str                                            # Güçlü Yükseliş | Yükseliş | Nötr | Düşüş | Güçlü Düşüş
    skor: float                                         # ham birleşik skor (yaklaşık -8..+8)
    guven: str                                          # düşük | orta | yüksek
    nedenler: list[str] = field(default_factory=list)   # etkiye göre sıralı gerekçeler
    uyari: str | None = None                            # veri yetersizse temkin notu


def _isaret_degisti(seri: pd.Series, bar: int) -> bool:
    """Serinin işareti son `bar` çubukta değişti mi (taze kesişim tespiti)?

    Şu anki işaret `bar` çubuk öncekiyle farklıysa True. Arada gidip gelen
    nadir durumları kaçırabilir; "yeni kesişim" etiketi için yeterli kaba ölçü.
    """
    v = seri.dropna().tail(bar + 1)
    if len(v) < 2:
        return False
    pos = v > 0
    return bool(pos.iloc[-1] != pos.iloc[0])


def _sinyaller(df: pd.DataFrame) -> list[tuple[float, str]]:
    """Tam indikatörlü seriden (puan, gerekçe) listesi üretir.

    Pozitif puan yükseliş, negatif puan düşüş yönünde katkı verir.
    """
    close = df["Close"]
    son = float(close.iloc[-1])
    sinyaller: list[tuple[float, str]] = []

    # --- 1) Uzun vade trend: Fiyat vs SMA200 ---
    sma200 = float(df["SMA200"].iloc[-1])
    if son > sma200:
        sinyaller.append((+2.0, "Fiyat 200 günlük ortalama üzerinde (uzun vade yukarı)"))
    else:
        sinyaller.append((-2.0, "Fiyat 200 günlük ortalama altında (uzun vade aşağı)"))

    # --- 2) Orta vade kesişim: SMA50 vs SMA200 (+ taze kesişim vurgusu) ---
    sma50 = float(df["SMA50"].iloc[-1])
    taze = _isaret_degisti(df["SMA50"] - df["SMA200"], bar=5)
    if sma50 > sma200:
        sinyaller.append((+1.5, "Yeni golden cross (50g, 200g'yi yukarı kesti)"
                          if taze else "50g ortalama 200g üzerinde (golden cross)"))
    else:
        sinyaller.append((-1.5, "Yeni death cross (50g, 200g'yi aşağı kesti)"
                          if taze else "50g ortalama 200g altında (death cross)"))

    # --- 3) Kısa-orta vade: Fiyat vs SMA50 ---
    if son > sma50:
        sinyaller.append((+1.0, "Fiyat 50 günlük ortalama üzerinde"))
    else:
        sinyaller.append((-1.0, "Fiyat 50 günlük ortalama altında"))

    # --- 4) Momentum: MACD vs sinyal (histogram işareti + taze kesişim) ---
    hist = float(df["MACDhist"].iloc[-1])
    macd_taze = _isaret_degisti(df["MACDhist"], bar=3)
    if hist >= 0:
        sinyaller.append((+1.5, "MACD yeni al sinyali (yukarı kesişim)"
                          if macd_taze else "MACD al tarafında (çizgi sinyalin üzerinde)"))
    else:
        sinyaller.append((-1.5, "MACD yeni sat sinyali (aşağı kesişim)"
                          if macd_taze else "MACD sat tarafında (çizgi sinyalin altında)"))

    # --- 5) RSI: aşırı alım/satım bölgeleri + nötr bölgede eğim ---
    rsi = float(df["RSI"].iloc[-1])
    onceki_rsi = float(df["RSI"].iloc[-2]) if len(df) >= 2 else rsi
    if rsi >= 70:
        sinyaller.append((-1.0, f"RSI {rsi:.0f} — aşırı alım (geri çekilme riski)"))
    elif rsi <= 30:
        sinyaller.append((+1.0, f"RSI {rsi:.0f} — aşırı satım (tepki potansiyeli)"))
    elif rsi > onceki_rsi:
        sinyaller.append((+0.5, f"RSI {rsi:.0f} — nötr, yukarı eğimli"))
    else:
        sinyaller.append((-0.5, f"RSI {rsi:.0f} — nötr, aşağı eğimli"))

    # --- 6) Kısa vade getiri: son ~20 işlem günü ---
    n = min(20, len(close) - 1)
    if n >= 1:
        gecmis = float(close.iloc[-1 - n])
        if gecmis:
            getiri = (son - gecmis) / gecmis * 100
            etiket = f"Son ~1 ay getirisi {fmt.tr_yuzde(getiri)}"
            if getiri >= 5:
                sinyaller.append((+1.0, etiket + " (güçlü momentum)"))
            elif getiri <= -5:
                sinyaller.append((-1.0, etiket + " (zayıf momentum)"))
            else:
                sinyaller.append((0.0, etiket + " (yatay)"))

    return sinyaller


def teknik_gorunum(df: pd.DataFrame) -> TeknikGorunum | None:
    """Tam indikatörlü seriden kural tabanlı teknik görünüm üretir.

    df: market.compute_indicators çıktısı (TAM seri; dilimlenmemiş). Veri
    çizilemeyecek kadar azsa None döner.
    """
    if df is None or df.empty or len(df) < 2:
        return None

    sinyaller = _sinyaller(df)
    skor = sum(p for p, _ in sinyaller)

    # Ham skoru 5 kademeli yön etiketine eşle
    if skor >= 5:
        yon = "Güçlü Yükseliş"
    elif skor >= 2:
        yon = "Yükseliş"
    elif skor > -2:
        yon = "Nötr"
    elif skor > -5:
        yon = "Düşüş"
    else:
        yon = "Güçlü Düşüş"

    # Güven = sinyallerin hizalanması (nötr/0 puanlılar sayılmaz)
    poz = sum(1 for p, _ in sinyaller if p > 0)
    neg = sum(1 for p, _ in sinyaller if p < 0)
    toplam = poz + neg
    hizalanma = abs(poz - neg) / toplam if toplam else 0.0
    if hizalanma >= 0.7:
        guven = "yüksek"
    elif hizalanma >= 0.4:
        guven = "orta"
    else:
        guven = "düşük"

    # Gerekçeleri etki büyüklüğüne göre sırala (en belirleyici önce)
    sinyaller.sort(key=lambda s: abs(s[0]), reverse=True)
    nedenler = [etiket for _, etiket in sinyaller]

    # Kısa fiyat geçmişi: SMA200 ısınmamış -> güveni "düşük"e kıs + temkin notu
    uyari = None
    if len(df) < 200:
        guven = "düşük"
        uyari = "Kısa fiyat geçmişi — uzun vadeli ortalamalar ısınmadı, temkinli yorumla."

    return TeknikGorunum(yon=yon, skor=round(skor, 1), guven=guven,
                         nedenler=nedenler, uyari=uyari)


if __name__ == "__main__":
    # Debug yolu: teknik görünümü Discord olmadan gözle kontrol etmek için.
    import sys

    import market

    kod = (sys.argv[1] if len(sys.argv) > 1 else "THYAO").upper()
    df = market.fetch_history(kod)
    if not market.is_valid(df):
        print(f"'{kod}' için veri bulunamadı (kod hatalı olabilir).")
        sys.exit(1)

    df = market.compute_indicators(df)
    g = teknik_gorunum(df)
    if g is None:
        print(f"{kod}: yeterli veri yok.")
        sys.exit(0)

    print(f"{kod}: {g.yon}  (skor {g.skor}, güven: {g.guven})")
    if g.uyari:
        print(f"  ! {g.uyari}")
    for n in g.nedenler:
        print(f"  • {n}")
