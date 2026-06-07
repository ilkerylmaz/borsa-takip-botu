"""
Mum grafiği üretimi: mplfinance ile 4 panelli PNG (BytesIO).

Paneller: 0) mumlar + SMA200 overlay, 1) hacim, 2) MACD, 3) RSI.
Koyu tema Discord'un karanlık arka planına (#2b2d31) uyacak şekilde seçildi.

Tek başına debug:  python charting.py THYAO 1a   -> _chart_THYAO.png yazar
(Discord'a bağlanmadan grafik estetiği üzerinde çalışmak için.)
"""

from __future__ import annotations

# DİKKAT: Agg backend, pyplot/mplfinance import edilmeden ÖNCE seçilmeli
# (GUI'siz / headless ortamda çökme olmasın diye).
import matplotlib

matplotlib.use("Agg")

import io

import mplfinance as mpf
import pandas as pd

# Discord koyu embed arka planı
_BG = "#2b2d31"

_MARKETCOLORS = mpf.make_marketcolors(
    up="#26a69a", down="#ef5350",
    edge="inherit", wick="inherit", volume="in",
)
_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MARKETCOLORS,
    facecolor=_BG, figcolor=_BG, gridcolor="#3a3d42",
    rc={"font.size": 9, "axes.labelcolor": "#c8cdd3",
        "xtick.color": "#c8cdd3", "ytick.color": "#c8cdd3"},
)


def render_chart(df: pd.DataFrame, kod: str, ad: str) -> io.BytesIO:
    """İndikatör kolonlu pencere DataFrame'inden PNG üretir.

    Beklenen kolonlar: OHLCV + SMA200, MACD, MACDsig, MACDhist, RSI
    (market.compute_indicators -> market.slice_window çıktısı).
    """
    # secondary_y=False ŞART: mplfinance ölçek farkına göre serileri kendi
    # kafasına göre sağda ikinci bir eksene atıyor (RSI 30/70 çizgileri ve
    # MACD histogramı ayrı eksenlere düşüyordu); hepsi tek eksende kalmalı.
    n = len(df)
    apds = [
        # Panel 0: SMA200 overlay (mumların üzerinde, sarı)
        mpf.make_addplot(df["SMA200"], color="#f5c518", width=1.2,
                         label="SMA200", secondary_y=False),
        # Panel 2: MACD çizgileri + histogram
        mpf.make_addplot(df["MACD"], panel=2, color="#4fc3f7", width=0.9,
                         ylabel="MACD", secondary_y=False),
        mpf.make_addplot(df["MACDsig"], panel=2, color="#ff7043", width=0.9,
                         secondary_y=False),
        mpf.make_addplot(df["MACDhist"], panel=2, type="bar", color="#888888",
                         alpha=0.5, secondary_y=False),
        # Panel 3: RSI + 30/70 kılavuz çizgileri
        mpf.make_addplot(df["RSI"], panel=3, color="#ba68c8", width=0.9,
                         ylabel="RSI", ylim=(0, 100), secondary_y=False),
        mpf.make_addplot([30] * n, panel=3, color="#777777", width=0.6,
                         linestyle="--", secondary_y=False),
        mpf.make_addplot([70] * n, panel=3, color="#777777", width=0.6,
                         linestyle="--", secondary_y=False),
    ]

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style=_STYLE,
        addplot=apds,
        volume=True,                  # panel 1
        panel_ratios=(6, 2, 2, 2),
        figratio=(16, 11),
        figscale=1.1,
        datetime_format="%d.%m",
        title=dict(title=f"{kod} — {ad}", y=0.98),  # mumlarla çakışmasın diye en üste
        tight_layout=True,
        savefig=dict(fname=buf, format="png", dpi=130, bbox_inches="tight"),
    )
    buf.seek(0)
    return buf


if __name__ == "__main__":
    # Debug yolu: grafik çıktısını Discord olmadan gözle kontrol etmek için.
    import sys

    import market

    kod = (sys.argv[1] if len(sys.argv) > 1 else "THYAO").upper()
    kisa = sys.argv[2] if len(sys.argv) > 2 else "1a"
    periyot = {"1h": "1 Hafta", "1a": "1 Ay"}.get(kisa, "1 Ay")

    df = market.fetch_history(kod)
    if not market.is_valid(df):
        print(f"'{kod}' için veri bulunamadı (kod hatalı olabilir).")
        sys.exit(1)

    df = market.compute_indicators(df)
    pencere = market.slice_window(df, periyot)
    buf = render_chart(pencere, kod=kod, ad=kod)

    dosya = f"_chart_{kod}.png"  # .gitignore'da
    with open(dosya, "wb") as f:
        f.write(buf.getvalue())
    print(f"yazıldı: {dosya} ({periyot}, {len(pencere)} mum)")
