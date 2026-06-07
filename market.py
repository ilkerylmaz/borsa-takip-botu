"""
Hisse verisi: fiyat geçmişi, teknik indikatörler ve özet bilgiler.

Tüm fonksiyonlar SENKRON (yfinance + pandas bloklar); bot tarafı
`asyncio.to_thread` ile çağırır (notifier.enrich_prices ile aynı desen).

İndikatörler saf pandas ile hesaplanır (TA-Lib/pandas-ta bağımlılığı yok):
  - SMA200: 200 günlük basit hareketli ortalama
  - MACD(12,26,9): EMA farkı + sinyal + histogram
  - RSI(14): Wilder yumuşatması (ewm alpha=1/14)

Önemli: indikatörler TAM seri (~2 yıl) üzerinde hesaplanır, görüntü
penceresi (1 hafta / 1 ay) SONRA dilimlenir — yoksa kısa pencerede
SMA200/RSI ısınma payı bulamaz ve yanlış çıkar.
"""

from __future__ import annotations
import logging
import time

import pandas as pd
import yfinance as yf

log = logging.getLogger("market")

# yfinance geçersiz kodda exception atmaz, stderr'e gürültü basar; kıs.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# get_info() yavaş ve hız sınırlı; floatShares/marketCap zaten seyrek değişir.
_INFO_TTL = 6 * 3600.0  # ~6 saat
_info_cache: dict[str, tuple[float, dict]] = {}  # kod -> (zaman, info)

# Periyot adı -> gösterilecek günlük mum sayısı (işlem günü)
# PERIYOT_SIRA: hem slash komut seçenekleri hem de grafik altı düğmeler için
# kullanılan görüntüleme sırası. fetch_history 2y çektiği için 1 Yıl (252) rahat sığar.
PERIYOT_MUM = {"1 Hafta": 5, "1 Ay": 22, "3 Ay": 66, "1 Yıl": 252}
PERIYOT_SIRA = ["1 Hafta", "1 Ay", "3 Ay", "1 Yıl"]


def fetch_history(kod: str, lookback: str = "2y") -> pd.DataFrame:
    """Günlük OHLCV geçmişi. Geçersiz kodda BOŞ DataFrame döner, raise etmez."""
    try:
        tk = yf.Ticker(f"{kod}.IS")
        df = tk.history(period=lookback, interval="1d", auto_adjust=False)
    except Exception as ex:
        log.debug("history hatası (%s): %s", kod, ex)
        return pd.DataFrame()
    return df


def is_valid(df: pd.DataFrame) -> bool:
    """Veri çizilebilir mi? (geçersiz kod -> boş frame)"""
    return df is not None and not df.empty and len(df) >= 2


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """SMA200, MACD(12,26,9) ve RSI(14) kolonlarını ekler (yerinde)."""
    close = df["Close"]

    # SMA200 — kısa geçmişli hisselerde de çizgi çıksın diye min_periods=1
    df["SMA200"] = close.rolling(200, min_periods=1).mean()

    # SMA50 — orta vade trend / kesişim (analiz.teknik_gorunum kullanır)
    df["SMA50"] = close.rolling(50, min_periods=1).mean()

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACDsig"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACDhist"] = df["MACD"] - df["MACDsig"]

    # RSI(14) — Wilder yumuşatması
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    return df


def slice_window(df: pd.DataFrame, periyot: str) -> pd.DataFrame:
    """Görüntü penceresi: son N işlem günü (indikatör kolonları korunur)."""
    n = PERIYOT_MUM.get(periyot, 22)
    return df.tail(n)


def _get_info_cached(kod: str, tk: yf.Ticker) -> dict:
    """get_info() sonucu 6 saat bellekte tutulur; hata halinde boş dict."""
    now = time.time()
    cached = _info_cache.get(kod)
    if cached and now - cached[0] < _INFO_TTL:
        return cached[1]
    try:
        info = tk.get_info() or {}
        _info_cache[kod] = (now, info)
        return info
    except Exception as ex:
        log.debug("get_info hatası (%s): %s", kod, ex)
        # Süresi geçmiş de olsa eski veri taze hatadan iyidir
        return cached[1] if cached else {}


def get_overview(kod: str) -> dict:
    """Embed için özet: fiyat, değişim, hacim, dolaşımdaki lot ve değerler.

    Eksik alanlar None döner; çağıran taraf '—' / 'veri yok' gösterir.
    """
    tk = yf.Ticker(f"{kod}.IS")

    # --- Fiyat: fast_info (camelCase!) -> history son kapanış fallback ---
    fiyat = onceki = None
    try:
        fi = tk.fast_info
        fiyat = fi.get("lastPrice") or fi.get("last_price")
        onceki = fi.get("previousClose") or fi.get("previous_close")
        hacim_lot = fi.get("lastVolume") or fi.get("last_volume")
    except Exception:
        hacim_lot = None
    if not fiyat:
        son = fetch_history(kod, lookback="5d")
        if is_valid(son):
            fiyat = float(son["Close"].iloc[-1])
            onceki = float(son["Close"].iloc[-2])
            hacim_lot = int(son["Volume"].iloc[-1])

    degisim = None
    if fiyat and onceki:
        degisim = (fiyat - onceki) / onceki * 100

    # --- Dolaşım / piyasa değeri: yavaş get_info, 6 saat cache'li ---
    info = _get_info_cached(kod, tk)
    dolasim_lot = info.get("floatShares")
    dolasim_kaynak = "float"
    if not dolasim_lot:
        dolasim_lot = info.get("sharesOutstanding")
        dolasim_kaynak = "shares" if dolasim_lot else None
    piyasa_degeri = info.get("marketCap")
    ad = info.get("longName") or info.get("shortName") or _ad_from_tickers(kod)

    # --- Analist konsensüsü: aynı (cache'li) info'dan, ekstra istek yok ---
    # Yahoo BIST'in likit isimlerini kapsar; küçük/yeni hisselerde alanlar
    # boş gelir. tavsiye ham anahtar olarak döner ("strong_buy"/"none"/None);
    # Türkçeye çevirme + "en az 2 analist" eşiği sunum tarafında (bot.py).
    hedef_ort = info.get("targetMeanPrice")
    analist = {
        "tavsiye": info.get("recommendationKey"),
        "analist_sayisi": info.get("numberOfAnalystOpinions"),
        "hedef_ort": hedef_ort,
        "hedef_yuksek": info.get("targetHighPrice"),
        "hedef_dusuk": info.get("targetLowPrice"),
        # ortalama hedefe göre yukarı/aşağı potansiyel (%)
        "hedef_potansiyel": ((hedef_ort - fiyat) / fiyat * 100)
                            if (hedef_ort and fiyat) else None,
    }

    return {
        "kod": kod,
        "ad": ad,
        "fiyat": fiyat,
        "degisim_yuzde": degisim,
        "hacim_lot": hacim_lot,
        "hacim_tl": hacim_lot * fiyat if (hacim_lot and fiyat) else None,
        "dolasim_lot": dolasim_lot,
        "dolasim_kaynak": dolasim_kaynak,  # "float" | "shares" | None
        "dolasim_tl": dolasim_lot * fiyat if (dolasim_lot and fiyat) else None,
        "piyasa_degeri": piyasa_degeri,
        "analist": analist,
    }


def _ad_from_tickers(kod: str) -> str:
    """tickers.py listesinden şirket adı; bilinmiyorsa kodun kendisi."""
    try:
        from tickers import TICKERS
        aliases = TICKERS.get(kod)
        if aliases:
            return aliases[0].title()
    except Exception:
        pass
    return kod
