"""
Kural tabanlı teknik görünüm (LLM'siz).

market.compute_indicators çıktısı olan TAM seri (~2 yıl) üzerinden çalışır —
görüntü penceresi DEĞİL. SMA200/SMA50 ısınma payı gerektirir (indikatörlerle
aynı uyarı: kısa pencerede hesaplanırsa yanlış çıkar). On teknik sinyali
ağırlıklı toplayıp 5 kademeli bir yön etiketi + güven + gerekçeler üretir:

  trend:     fiyat vs SMA200 · SMA200 eğimi · SMA50/200 kesişimi · fiyat vs SMA50
  momentum:  MACD (işaret + histogram ivmesi) · RSI bantları · ~20 gün getiri
  teyit:     hacim (son 5 gün vs 60 gün ortalaması)
  uçlar:     Bollinger(20,2σ) bandı dışına taşma · 52 hafta zirve/dip yakınlığı

Skor, sinyal ağırlıkları toplamının olabilecek en yüksek değerine oranlanıp
-10..+10 aralığına NORMALİZE edilir (yon eşikleri bu ölçekte). ATR(14)/fiyat
yüksekse yön sinyali verilmez, volatilite uyarısı düşülür.

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
    skor: float                                         # normalize birleşik skor (-10..+10)
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


# Normalizasyon paydası: aynı anda alınabilecek en yüksek pozitif toplam.
# Sinyal ağırlığı değişirse burayı da güncelle (testte assert ile doğrulanmaz;
# ufak sapma yön eşiklerini bozmaz ama ölçek tutarlılığı için doğru tut).
_MAKS_SKOR = 2.0 + 0.5 + 1.5 + 1.0 + 1.5 + 1.0 + 1.0 + 0.75 + 0.5 + 1.0  # = 10.75


def _sinyaller(df: pd.DataFrame) -> list[tuple[float, str]]:
    """Tam indikatörlü seriden (puan, gerekçe) listesi üretir.

    Pozitif puan yükseliş, negatif puan düşüş yönünde katkı verir.
    Sinyaller kasıtlı olarak klasik ve tek tek açıklanabilir tutulur.
    """
    close = df["Close"]
    son = float(close.iloc[-1])
    sinyaller: list[tuple[float, str]] = []

    # --- 1) Uzun vade trend: Fiyat vs SMA200 (±2.0) ---
    sma200 = float(df["SMA200"].iloc[-1])
    if son > sma200:
        sinyaller.append((+2.0, "Fiyat 200 günlük ortalama üzerinde (uzun vade yukarı)"))
    else:
        sinyaller.append((-2.0, "Fiyat 200 günlük ortalama altında (uzun vade aşağı)"))

    # --- 2) SMA200'ün kendi eğimi (±0.5): yükselen ortalamanın üzerinde olmak,
    #        düşen ortalamanın üzerinde olmaktan daha güçlüdür ---
    if len(df) >= 21:
        sma200_egim = sma200 - float(df["SMA200"].iloc[-21])
        if sma200_egim > 0:
            sinyaller.append((+0.5, "200g ortalamanın kendisi yukarı eğimli"))
        elif sma200_egim < 0:
            sinyaller.append((-0.5, "200g ortalamanın kendisi aşağı eğimli"))

    # --- 3) Orta vade kesişim: SMA50 vs SMA200 (±1.5, taze kesişim vurgulu) ---
    sma50 = float(df["SMA50"].iloc[-1])
    taze = _isaret_degisti(df["SMA50"] - df["SMA200"], bar=5)
    if sma50 > sma200:
        sinyaller.append((+1.5, "Yeni golden cross (50g, 200g'yi yukarı kesti)"
                          if taze else "50g ortalama 200g üzerinde (golden cross)"))
    else:
        sinyaller.append((-1.5, "Yeni death cross (50g, 200g'yi aşağı kesti)"
                          if taze else "50g ortalama 200g altında (death cross)"))

    # --- 4) Kısa-orta vade: Fiyat vs SMA50 (±1.0) ---
    if son > sma50:
        sinyaller.append((+1.0, "Fiyat 50 günlük ortalama üzerinde"))
    else:
        sinyaller.append((-1.0, "Fiyat 50 günlük ortalama altında"))

    # --- 5) Momentum: MACD işareti + histogram İVMESİ (±1.5 / ±0.75) ---
    # Histogram işaretle aynı yönde büyüyorsa momentum güçleniyor (tam puan),
    # daralıyorsa zayıflıyor (yarım puan) — erken dönüş uyarısı.
    hist = float(df["MACDhist"].iloc[-1])
    macd_taze = _isaret_degisti(df["MACDhist"], bar=3)
    h = df["MACDhist"].dropna().tail(3)
    ivme_artiyor = len(h) >= 2 and float(h.iloc[-1]) > float(h.iloc[0])
    if hist >= 0:
        if macd_taze:
            sinyaller.append((+1.5, "MACD yeni al sinyali (yukarı kesişim)"))
        elif ivme_artiyor:
            sinyaller.append((+1.5, "MACD pozitif, momentum güçleniyor"))
        else:
            sinyaller.append((+0.75, "MACD pozitif ama momentum zayıflıyor"))
    else:
        if macd_taze:
            sinyaller.append((-1.5, "MACD yeni sat sinyali (aşağı kesişim)"))
        elif not ivme_artiyor:
            sinyaller.append((-1.5, "MACD negatif, momentum zayıflamaya devam ediyor"))
        else:
            sinyaller.append((-0.75, "MACD negatif ama toparlanma eğiliminde"))

    # --- 6) RSI bantları (±1.0 / ±0.75 / nötrde eğim ±0.25) ---
    rsi = float(df["RSI"].iloc[-1])
    onceki_rsi = float(df["RSI"].iloc[-2]) if len(df) >= 2 else rsi
    if rsi >= 70:
        sinyaller.append((-1.0, f"RSI {rsi:.0f} — aşırı alım (geri çekilme riski)"))
    elif rsi >= 60:
        sinyaller.append((+0.75, f"RSI {rsi:.0f} — güçlü momentum bölgesi"))
    elif rsi > 40:
        if rsi > onceki_rsi:
            sinyaller.append((+0.25, f"RSI {rsi:.0f} — nötr, yukarı eğimli"))
        else:
            sinyaller.append((-0.25, f"RSI {rsi:.0f} — nötr, aşağı eğimli"))
    elif rsi > 30:
        sinyaller.append((-0.75, f"RSI {rsi:.0f} — zayıf momentum bölgesi"))
    else:
        sinyaller.append((+1.0, f"RSI {rsi:.0f} — aşırı satım (tepki potansiyeli)"))

    # --- 7) Kısa vade getiri: son ~20 işlem günü (±1.0) ---
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

    # --- 8) Hacim teyidi (±0.75): son 5 gün ort. hacmi 60 gün ortalamasına
    #        göre belirgin yüksekse mevcut kısa vade fiyat yönünü teyit eder;
    #        yükseliş cılız hacimle geliyorsa küçük şüphe puanı ---
    if "Volume" in df.columns and len(df) >= 10:
        v = df["Volume"].astype(float)
        v60 = float(v.tail(60).mean())
        v5 = float(v.tail(5).mean())
        if v60 > 0 and len(close) >= 6:
            yon5 = son - float(close.iloc[-6])  # son 5 günün fiyat yönü
            oran = v5 / v60
            if oran >= 1.3:
                if yon5 > 0:
                    sinyaller.append((+0.75, f"Yükseliş artan hacimle teyitli (hacim {oran:.1f}x)"))
                elif yon5 < 0:
                    sinyaller.append((-0.75, f"Düşüş artan hacimle geliyor (satış baskısı, {oran:.1f}x)"))
            elif oran <= 0.7 and yon5 > 0:
                sinyaller.append((-0.25, "Yükselişte hacim zayıf (teyitsiz hareket)"))

    # --- 9) Bollinger(20,2σ) dışına taşma (±0.5): aşırı uzama / aşırı satım ---
    bb_ust = df["BBust"].iloc[-1] if "BBust" in df.columns else None
    bb_alt = df["BBalt"].iloc[-1] if "BBalt" in df.columns else None
    if pd.notna(bb_ust) and son > float(bb_ust):
        sinyaller.append((-0.5, "Fiyat üst Bollinger bandının dışında (aşırı uzama)"))
    elif pd.notna(bb_alt) and son < float(bb_alt):
        sinyaller.append((+0.5, "Fiyat alt Bollinger bandının dışında (aşırı satım)"))

    # --- 10) 52 hafta zirve/dip yakınlığı (±1.0) ---
    yillik = close.tail(min(252, len(close)))
    zirve, dip = float(yillik.max()), float(yillik.min())
    if zirve and son >= zirve * 0.98:
        sinyaller.append((+1.0, "Yıllık zirveye yakın / yeni zirve (göreli güç)"))
    elif dip and son <= dip * 1.02:
        sinyaller.append((-1.0, "Yıllık dibe yakın (göreli zayıflık)"))

    return sinyaller


def teknik_gorunum(df: pd.DataFrame) -> TeknikGorunum | None:
    """Tam indikatörlü seriden kural tabanlı teknik görünüm üretir.

    df: market.compute_indicators çıktısı (TAM seri; dilimlenmemiş). Veri
    çizilemeyecek kadar azsa None döner.
    """
    if df is None or df.empty or len(df) < 2:
        return None

    sinyaller = _sinyaller(df)
    # -10..+10 aralığına normalize: sinyal eklemek/ağırlık değiştirmek yön
    # eşiklerini bozmasın diye ham toplam olası maksimuma oranlanır.
    skor = sum(p for p, _ in sinyaller) / _MAKS_SKOR * 10

    # Normalize skoru 5 kademeli yön etiketine eşle
    if skor >= 5.5:
        yon = "Güçlü Yükseliş"
    elif skor >= 2:
        yon = "Yükseliş"
    elif skor > -2:
        yon = "Nötr"
    elif skor > -5.5:
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

    uyarilar: list[str] = []

    # Yüksek volatilite: ATR/fiyat büyükse sinyaller hızlı geçersizleşir —
    # yön verilir ama güven kısılır + not düşülür (yön sinyali DEĞİL).
    son = float(df["Close"].iloc[-1])
    atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns and pd.notna(df["ATR"].iloc[-1]) else None
    if atr and son and atr / son >= 0.045:
        if guven == "yüksek":
            guven = "orta"
        uyarilar.append(f"Volatilite yüksek (günlük ortalama bant ~%{atr / son * 100:.1f}) — "
                        "sinyaller hızlı geçersizleşebilir.")

    # Kısa fiyat geçmişi: SMA200 ısınmamış -> güveni "düşük"e kıs + temkin notu
    if len(df) < 200:
        guven = "düşük"
        uyarilar.append("Kısa fiyat geçmişi — uzun vadeli ortalamalar ısınmadı, temkinli yorumla.")

    return TeknikGorunum(yon=yon, skor=round(skor, 1), guven=guven,
                         nedenler=nedenler,
                         uyari=" ".join(uyarilar) if uyarilar else None)


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
