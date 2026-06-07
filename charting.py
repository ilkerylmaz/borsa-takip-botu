"""
Mum grafiği üretimi: mplfinance ile 3 panelli PNG (BytesIO).

Paneller: 0) mumlar + SMA50/SMA200 overlay, 1) MACD, 2) RSI.
(Hacim paneli kullanıcı isteğiyle kaldırıldı — mumlara daha çok dikey alan kalsın
ve grafik "ekran görüntüsü" gibi sıkışık değil, ferah/okunaklı dursun diye.)
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

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

import fmt  # saf modül (discord bağımsız), Türkçe sayı biçimi için

# Renk paleti — Discord koyu embed arka planına uyumlu
_BG = "#2b2d31"
_UP = "#26a69a"           # yükseliş / pozitif
_DOWN = "#ef5350"         # düşüş / negatif
_SMA_COLOR = "#f5c518"    # SMA200 (sarı)
_SMA50_COLOR = "#42a5f5"  # SMA50 (mavi) — kısa vadeli ortalama
_MACD_LINE = "#4fc3f7"    # MACD çizgisi (açık mavi)
_MACD_SIG = "#ff7043"     # sinyal çizgisi (turuncu)
_RSI_COLOR = "#ba68c8"    # RSI (mor)
_GRID = "#3a3d42"

_MARKETCOLORS = mpf.make_marketcolors(
    up=_UP, down=_DOWN,
    edge="inherit", wick="inherit", volume="in",
)
_STYLE = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=_MARKETCOLORS,
    facecolor=_BG, figcolor=_BG, gridcolor=_GRID, gridstyle=":",
    rc={"font.size": 9, "axes.labelcolor": "#c8cdd3",
        "xtick.color": "#c8cdd3", "ytick.color": "#c8cdd3",
        "axes.edgecolor": "#4a4d52"},
)

# Ana panelde mumların kaplaması gereken asgari dikey oran. SMA200 bu eşiği
# bozacak kadar uzaktaysa kadrajı genişletmeyiz: mumlar paneli doldurur, SMA200
# çizgisi yakın kenara kıstırılır ve gerçek değeri ▲/▼ ile etikette gösterilir
# (böylece "her halükarda görünür" ama mumlar ezilmez).
_MIN_MUM_ORANI = 0.35
_KENAR_PAYI = 0.04   # kıstırılan çizgi/etiketin kenardan içeri girinti oranı


def _hesapla_ylim(win: pd.DataFrame) -> tuple[float, float, bool]:
    """Ana panel y-sınırları + SMA200 uzakta mı (kıstırılacak mı) bilgisi.

    SMA200 mum aralığına yakınsa kadraj genişletilip çizgi gerçek konumunda
    gösterilir; çok uzaksa mum-odaklı kadraj korunur (uzak=True).
    """
    # SMA50 fiyata yakın kısa vadeli ortalama: SMA200'ün aksine kıstırma
    # gerekmez, mum aralığıyla birlikte "her zaman görünür" çerçeveye katılır.
    sma50 = win["SMA50"].dropna()
    low = float(min(win["Low"].min(), sma50.min())) if not sma50.empty else float(win["Low"].min())
    high = float(max(win["High"].max(), sma50.max())) if not sma50.empty else float(win["High"].max())
    span = (high - low) or (high * 0.02) or 1.0
    pad = span * 0.06
    c_lo, c_hi = low - pad, high + pad

    sma = win["SMA200"].dropna()
    if sma.empty:
        return c_lo, c_hi, False

    s_lo, s_hi = float(sma.min()), float(sma.max())
    lo, hi = min(c_lo, s_lo), max(c_hi, s_hi)

    cand = c_hi - c_lo
    if cand >= (hi - lo) * _MIN_MUM_ORANI:
        # SMA200 yeterince yakın: çizgiyi gerçek yerinde göster, biraz boşluk bırak
        m = (hi - lo) * 0.04
        return lo - m, hi + m, False

    # SMA200 çok uzakta: mum-odaklı kadrajı koru (çizgi kenara kıstırılacak)
    return c_lo, c_hi, True


def _sma_etiketleri(ax_ana, win: pd.DataFrame, n: int, lo: float, hi: float) -> None:
    """SMA50 + SMA200 son değerlerini sağ kenarda renkli etiketlerde gösterir.

    İki etiket dikeyde çok yakınsa üst üste binmesin diye ayrılır. SMA200 kadraj
    dışına taşıyorsa ▲/▼ ile işaretlenir (gerçek değer yine yazılır); SMA50 zaten
    çerçeveye dahil olduğundan hep gerçek konumunda.
    """
    pay = (hi - lo) * _KENAR_PAYI
    girisler: list[list] = []  # [y, renk, metin]

    s200 = float(win["SMA200"].iloc[-1])
    isaret = "▲ " if s200 > hi else ("▼ " if s200 < lo else "")
    girisler.append([min(max(s200, lo + pay), hi - pay), _SMA_COLOR,
                     f"{isaret}SMA200 {fmt.tr_sayi(s200)}"])

    s50 = float(win["SMA50"].iloc[-1])
    girisler.append([min(max(s50, lo + pay), hi - pay), _SMA50_COLOR,
                     f"SMA50 {fmt.tr_sayi(s50)}"])

    # Dikey çakışma önleme: iki etiket çok yakınsa simetrik olarak ayır
    min_aralik = (hi - lo) * 0.07
    girisler.sort(key=lambda g: g[0])
    if girisler[1][0] - girisler[0][0] < min_aralik:
        orta = (girisler[0][0] + girisler[1][0]) / 2
        girisler[0][0] = max(lo + pay, orta - min_aralik / 2)
        girisler[1][0] = min(hi - pay, orta + min_aralik / 2)

    for y, renk, metin in girisler:
        ax_ana.annotate(
            metin, xy=(n - 1, y), xytext=(-6, 0), textcoords="offset points",
            ha="right", va="center", fontsize=8.5, fontweight="bold",
            color="#1a1a1a", zorder=10,
            bbox=dict(boxstyle="round,pad=0.28", fc=renk, ec="none", alpha=0.92),
        )


def _suzle_panelleri(axlist, win: pd.DataFrame, n: int,
                     lo: float, hi: float) -> None:
    """Çizim sonrası kozmetik rötuşlar (MACD sıfır çizgisi, RSI bölgeleri,
    SMA50/SMA200 değer etiketleri). Salt görsel; hata olsa bile komut akışını bozmaz.

    axlist sırası (volume=False, 3 panel): [ana, ana2, macd, macd2, rsi, rsi2].
    """
    ax_ana, ax_macd, ax_rsi = axlist[0], axlist[2], axlist[4]

    # --- MACD: sıfır çizgisi ---
    ax_macd.axhline(0, color="#888888", lw=0.7, alpha=0.5)

    # --- RSI: aşırı alım/satım bölgeleri + 30/50/70 kılavuzları ---
    ax_rsi.set_ylim(0, 100)
    ax_rsi.axhspan(70, 100, color=_DOWN, alpha=0.10)    # aşırı alım
    ax_rsi.axhspan(0, 30, color=_UP, alpha=0.10)        # aşırı satım
    ax_rsi.axhline(70, color=_DOWN, lw=0.7, ls="--", alpha=0.55)
    ax_rsi.axhline(30, color=_UP, lw=0.7, ls="--", alpha=0.55)
    ax_rsi.axhline(50, color="#777777", lw=0.6, ls=":", alpha=0.5)
    ax_rsi.set_yticks([30, 50, 70])

    # --- SMA50 + SMA200: son değerleri okunur etiketlerde (çakışma önlemeli) ---
    _sma_etiketleri(ax_ana, win, n, lo, hi)


def render_chart(df: pd.DataFrame, kod: str, ad: str) -> io.BytesIO:
    """İndikatör kolonlu pencere DataFrame'inden PNG üretir.

    Beklenen kolonlar: OHLCV + SMA200, MACD, MACDsig, MACDhist, RSI
    (market.compute_indicators -> market.slice_window çıktısı).
    """
    win = df
    n = len(win)

    # Ana panel y-sınırları: SMA200 her halükarda kadrajda kalsın.
    lo, hi, uzak = _hesapla_ylim(win)
    if uzak:
        # SMA200 kadrajın çok dışında: çizgiyi yakın kenara kıstır (gerçek
        # değeri _suzle_panelleri'ndeki etikette ▲/▼ ile veriliyor).
        kenar = (hi - lo) * _KENAR_PAYI
        sma_plot = win["SMA200"].clip(lower=lo + kenar, upper=hi - kenar)
    else:
        sma_plot = win["SMA200"]   # gerçek konumunda

    # MACD histogramı: çubuklar işarete göre yeşil/kırmızı
    macd_renk = [_UP if v >= 0 else _DOWN for v in win["MACDhist"]]

    # secondary_y=False ŞART: mplfinance ölçek farkına göre serileri kendi
    # kafasına göre sağda ikinci bir eksene atıyor; hepsi tek eksende kalmalı.
    apds = [
        # Panel 0: SMA50 (mavi, ince) + SMA200 (sarı, kalın) overlay
        mpf.make_addplot(win["SMA50"], color=_SMA50_COLOR, width=1.3,
                         secondary_y=False),
        mpf.make_addplot(sma_plot, color=_SMA_COLOR, width=1.7,
                         secondary_y=False),
        # Panel 1: MACD — önce histogram (arkada), sonra çizgiler (önde)
        mpf.make_addplot(win["MACDhist"], panel=1, type="bar", color=macd_renk,
                         alpha=0.55, width=0.7, ylabel="MACD", secondary_y=False),
        mpf.make_addplot(win["MACD"], panel=1, color=_MACD_LINE, width=1.3,
                         secondary_y=False),
        mpf.make_addplot(win["MACDsig"], panel=1, color=_MACD_SIG, width=1.3,
                         secondary_y=False),
        # Panel 2: RSI (kılavuz çizgileri ve bölgeler _suzle_panelleri'nde)
        mpf.make_addplot(win["RSI"], panel=2, color=_RSI_COLOR, width=1.4,
                         ylabel="RSI", ylim=(0, 100), secondary_y=False),
    ]

    fig, axlist = mpf.plot(
        win,
        type="candle",
        style=_STYLE,
        addplot=apds,
        volume=False,                 # hacim paneli kaldırıldı
        panel_ratios=(6, 2, 2),       # mumlara bol alan
        figratio=(16, 10),
        figscale=1.15,
        datetime_format="%d.%m",
        xrotation=0,
        ylim=(lo, hi),                # ana panel: SMA200 dahil
        tight_layout=True,
        returnfig=True,
        title=dict(title=f"{kod} — {ad}", y=0.98),
    )

    try:
        _suzle_panelleri(axlist, win, n, lo, hi)
    except Exception:
        # Kozmetik rötuş başarısız olsa bile grafiği yine de gönder.
        pass

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


if __name__ == "__main__":
    # Debug yolu: grafik çıktısını Discord olmadan gözle kontrol etmek için.
    import sys

    import market

    kod = (sys.argv[1] if len(sys.argv) > 1 else "THYAO").upper()
    kisa = sys.argv[2] if len(sys.argv) > 2 else "1a"
    periyot = {"1h": "1 Hafta", "1a": "1 Ay",
               "3a": "3 Ay", "1y": "1 Yıl"}.get(kisa, "1 Ay")

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
