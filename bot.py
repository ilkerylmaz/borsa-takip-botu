"""
BIST Hisse Botu — Discord gateway istemcisi (İKİNCİ giriş noktası).

Haber botundan (main.py, webhook ile iter) BAĞIMSIZ ayrı bir süreçtir:
Discord'a sürekli bağlı kalır ve /hisse komutuna anında cevap verir.
Şimdilik lokalde çalıştırılır: python bot.py  (DISCORD_BOT_TOKEN gerekir).

/hisse kod:<KOD> periyot:<1 Hafta|1 Ay>
  -> anlık fiyat, hacim, dolaşımdaki lot/değer + 4 panelli teknik grafik
     (günlük mum + SMA200, hacim, MACD, RSI)

Bloklayan işler (yfinance, matplotlib) asyncio.to_thread ile ayrı thread'de
koşar — gateway heartbeat'i donmaz (notifier.enrich_prices ile aynı desen).
"""

from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

import analiz
import charting
import fmt
import market
import sources  # /health: haber kaynaklarını canlı yoklamak için (salt tanı)
import store    # /health: seen.db salt-okunur özeti
from textnorm import fold
from tickers import TICKERS, find_tickers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

# Embed renkleri (günlük değişim yönüne göre)
_YESIL = 0x26A69A
_KIRMIZI = 0xEF5350
_GRI = 0x95A5A6

# Teknik görünüm yönü -> emoji (embed rengi günlük değişimi gösterdiği için
# yön ayrıca emoji ile kodlanır).
_GORUNUM_EMOJI = {
    "Güçlü Yükseliş": "🟢", "Yükseliş": "🟢",
    "Nötr": "⚪",
    "Düşüş": "🔴", "Güçlü Düşüş": "🔴",
}
# yfinance recommendationKey -> Türkçe tavsiye etiketi
_TAVSIYE_TR = {
    "strong_buy": "Güçlü AL", "buy": "AL", "hold": "TUT",
    "underperform": "Endeks Altı", "sell": "SAT", "strong_sell": "Güçlü SAT",
}

# Slash komut seçenekleri ve grafik altı düğmeler aynı listeden beslenir.
PERIYOTLAR = [app_commands.Choice(name=p, value=p) for p in market.PERIYOT_SIRA]


class HisseBot(discord.Client):
    def __init__(self):
        # Slash komutları için ayrıcalıklı intent gerekmez; yalnızca guilds
        # açık (sunucu durumu takibi için discord.py'nin önerdiği asgari).
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.getenv("GUILD_ID", "").strip().strip('"').strip("'")
        if guild_id:
            # Dev modu: komut tek sunucuya anında senkronlanır
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Komutlar sunucuya senkronlandı (GUILD_ID=%s, anında).", guild_id)
        else:
            # Global senkron: Discord'a yayılması ~1 saat sürebilir
            await self.tree.sync()
            log.info("Komutlar global senkronlandı (yayılma ~1 saat sürebilir).")


client = HisseBot()


def _kod_normalle(girdi: str) -> str:
    """Kullanıcı girdisini hisse koduna indirger: 'thyao' / 'THYAO.IS' -> 'THYAO'."""
    kod = fold(girdi).upper().strip().removesuffix(".IS").strip()
    return kod


def _teknik_alani(e: discord.Embed, gor: analiz.TeknikGorunum | None) -> None:
    """'📈 Teknik Görünüm' alanını ekler (gor yoksa hiç eklemez)."""
    if gor is None:
        return
    emoji = _GORUNUM_EMOJI.get(gor.yon, "⚪")
    satirlar = [f"{emoji} **{gor.yon}** · güven: {gor.guven}"]
    satirlar += [f"• {n}" for n in gor.nedenler[:3]]   # en belirleyici 3 gerekçe
    if gor.uyari:
        satirlar.append(f"⚠️ {gor.uyari}")
    e.add_field(name="📈 Teknik Görünüm (kısa-orta vade)",
                value="\n".join(satirlar), inline=False)


def _finansal_alani(e: discord.Embed, ov: dict) -> None:
    """'💳 Borçluluk' alanını ekler (bilanço verisi yoksa hiç eklemez).

    Bankalar/finans kuruluşları atlanır: mevduat ve fonlama bilanço gereği
    'borç' göründüğünden totalDebt/FAVÖK metrikleri orada yanıltıcıdır.
    Net borç <= 0 ise şirket net nakit pozisyonundadır (güçlü bilanço) ve
    Net Borç/FAVÖK gösterilmez (negatif oran anlamsız).
    """
    f = ov.get("finansal") or {}
    if f.get("sektor") == "Financial Services":
        return
    borc, nakit, net = f.get("toplam_borc"), f.get("nakit"), f.get("net_borc")
    if borc is None and f.get("borc_ozsermaye") is None:
        return  # gösterecek bilanço verisi yok

    # Bilanço, şirketin raporlama para birimindedir (THYAO: USD) — TL sanma
    birim = "TL" if f.get("para_birimi") in (None, "TRY") else f["para_birimi"]

    satirlar = []
    if net is not None:
        if net <= 0:
            satirlar.append(f"Net **nakit** pozisyonu: {fmt.tr_buyuk(-net, birim)} "
                            f"(nakit {fmt.tr_buyuk(nakit, birim)} > borç {fmt.tr_buyuk(borc, birim)})")
        else:
            satirlar.append(f"Net borç: {fmt.tr_buyuk(net, birim)} "
                            f"(borç {fmt.tr_buyuk(borc, birim)} − nakit {fmt.tr_buyuk(nakit, birim)})")
    elif borc is not None:
        satirlar.append(f"Toplam borç: {fmt.tr_buyuk(borc, birim)}")

    oranlar = []
    bo = f.get("borc_ozsermaye")
    if bo is not None:
        oranlar.append(f"Borç/Özkaynak: %{fmt.tr_sayi(bo, 1)}")
    nbf = f.get("net_borc_favok")
    if nbf is not None:
        oranlar.append(f"Net Borç/FAVÖK: {fmt.tr_sayi(nbf, 1)}x")
    if oranlar:
        satirlar.append(" · ".join(oranlar))

    if satirlar:
        e.add_field(name="💳 Borçluluk", value="\n".join(satirlar), inline=False)


def _analist_alani(e: discord.Embed, ov: dict) -> None:
    """'🏦 Analist Konsensüsü' alanını ekler.

    En az 2 analist yoksa alan hiç gösterilmez (tek-analist/bayat veriyi eler,
    örn. hedefi fiyatın katı çıkan kapsanmamış hisseler). Tavsiye 'none' ama
    hedef varsa tavsiye '—' gösterilip yalnızca hedef verilir.
    """
    a = ov.get("analist") or {}
    sayi = a.get("analist_sayisi")
    if not sayi or sayi < 2:
        return

    tavsiye = _TAVSIYE_TR.get(a.get("tavsiye"), "—")
    hedef = a.get("hedef_ort")
    if hedef:
        ust = f"Tavsiye: {tavsiye} · Ort. hedef {fmt.tr_sayi(hedef)} TL"
        pot = a.get("hedef_potansiyel")
        if pot is not None:
            ust += f" ({fmt.tr_yuzde(pot)})"
        satirlar = [ust]
        dusuk, yuksek = a.get("hedef_dusuk"), a.get("hedef_yuksek")
        if dusuk and yuksek:
            satirlar.append(f"Aralık: {fmt.tr_sayi(dusuk)} – {fmt.tr_sayi(yuksek)} TL")
    elif tavsiye != "—":
        satirlar = [f"Tavsiye: {tavsiye}"]
    else:
        return  # ne tavsiye ne hedef -> gösterecek bir şey yok

    e.add_field(name=f"🎯 Analist Konsensüsü · {sayi} analist",
                value="\n".join(satirlar), inline=False)


def _overview_embed(ov: dict, periyot: str,
                    gor: analiz.TeknikGorunum | None = None) -> discord.Embed:
    """Özet verilerden embed kurar; eksik alanlar 'veri yok' / '—' gösterilir."""
    degisim = ov["degisim_yuzde"]
    renk = _GRI if degisim is None else (_YESIL if degisim >= 0 else _KIRMIZI)

    baslik = ov["kod"] if ov["ad"] == ov["kod"] else f"{ov['kod']} — {ov['ad']}"
    e = discord.Embed(
        title=baslik,
        description=f"Günlük mum • {periyot} • SMA50 · SMA200 · MACD · RSI",
        color=renk,
    )

    if ov["fiyat"]:
        fiyat_txt = f"{fmt.tr_sayi(ov['fiyat'])} TL"
        if degisim is not None:
            fiyat_txt += f" ({fmt.tr_yuzde(degisim)})"
    else:
        fiyat_txt = "veri yok"
    e.add_field(name="💰 Fiyat", value=fiyat_txt, inline=True)

    if ov["hacim_lot"]:
        hacim_txt = f"{fmt.tr_sayi(ov['hacim_lot'], 0)} lot"
        if ov["hacim_tl"]:
            hacim_txt += f"\n≈ {fmt.tr_buyuk(ov['hacim_tl'])}"
    else:
        hacim_txt = "veri yok"
    e.add_field(name="📊 Hacim (son seans)", value=hacim_txt, inline=True)

    if ov["dolasim_lot"]:
        dolasim_txt = f"{fmt.tr_sayi(ov['dolasim_lot'], 0)} lot"
        if ov["dolasim_kaynak"] == "shares":
            dolasim_txt += "\n(halka açıklık verisi yok; toplam pay)"
    else:
        dolasim_txt = "veri yok"
    e.add_field(name="🔄 Dolaşımdaki Lot", value=dolasim_txt, inline=True)

    e.add_field(
        name="💵 Dolaşım Değeri",
        value=fmt.tr_buyuk(ov["dolasim_tl"]) if ov["dolasim_tl"] else "—",
        inline=True,
    )
    e.add_field(
        name="🏦 Piyasa Değeri",
        value=fmt.tr_buyuk(ov["piyasa_degeri"]) if ov["piyasa_degeri"] else "—",
        inline=True,
    )

    _finansal_alani(e, ov)
    _teknik_alani(e, gor)
    _analist_alani(e, ov)

    e.set_footer(text="Teknik görünüm ve analist konsensüsü bilgilendirme amaçlıdır, "
                      "yatırım tavsiyesi değildir. Veriler Yahoo Finance kaynaklı, "
                      "~15 dk gecikmeli olabilir.")
    return e


class GrafikView(discord.ui.View):
    """Grafiğin altına periyot değiştirme düğmeleri ekler.

    Veri (df + indikatörler) ve özet komut anında bir kez çekilir; bir düğmeye
    basılınca yeni veri ÇEKİLMEZ — yalnızca pencere yeniden dilimlenip grafik
    yeniden çizilir (hızlı). Kanaldaki herkes periyodu değiştirebilir.
    """

    def __init__(self, df, ov: dict, kod: str, ad: str, secilen: str,
                 gor: analiz.TeknikGorunum | None = None):
        super().__init__(timeout=600)  # 10 dk hareketsizlikten sonra düğmeler pasifleşir
        self.df = df
        self.ov = ov
        self.kod = kod
        self.ad = ad
        self.secilen = secilen
        self.gor = gor  # teknik görünüm periyottan bağımsız: bir kez hesaplanır, saklanır
        self.message: discord.Message | None = None
        self._butonlari_kur()

    def _butonlari_kur(self):
        """Düğmeleri (yeniden) oluşturur; aktif periyot vurgulu ve pasiftir."""
        self.clear_items()
        for p in market.PERIYOT_SIRA:
            aktif = p == self.secilen
            btn = discord.ui.Button(
                label=p,
                style=discord.ButtonStyle.primary if aktif else discord.ButtonStyle.secondary,
                disabled=aktif,  # zaten gösterilen periyoda tekrar basmak anlamsız
            )
            btn.callback = self._tiklama(p)
            self.add_item(btn)

    def _tiklama(self, periyot: str):
        async def callback(interaction: discord.Interaction):
            # Bileşen etkileşimini onayla (mesajı sonra düzenleyeceğiz)
            await interaction.response.defer()
            self.secilen = periyot
            pencere = market.slice_window(self.df, periyot)
            buf = await asyncio.to_thread(
                charting.render_chart, pencere, self.kod, self.ad)

            embed = _overview_embed(self.ov, periyot, self.gor)
            embed.set_image(url="attachment://grafik.png")
            dosya = discord.File(buf, filename="grafik.png")
            self._butonlari_kur()  # aktif düğme vurgusunu güncelle
            await interaction.edit_original_response(
                embed=embed, attachments=[dosya], view=self)
            log.info("/hisse %s periyot -> %s (%s)",
                     self.kod, periyot, interaction.user)

        return callback

    async def on_timeout(self):
        # Süre dolunca düğmeleri pasifleştir ki bayat tıklamalar takılmasın
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@client.tree.command(name="hisse", description="BIST hissesi: fiyat, hacim ve teknik grafik (SMA50, SMA200, MACD, RSI)")
@app_commands.describe(kod="Hisse kodu veya şirket adı (örn. THYAO)", periyot="Grafik periyodu (varsayılan: 1 Ay)")
@app_commands.choices(periyot=PERIYOTLAR)
async def hisse(
    interaction: discord.Interaction,
    kod: str,
    periyot: app_commands.Choice[str] | None = None,
):
    secilen = periyot.value if periyot else "1 Ay"
    # Grafik üretimi birkaç saniye sürer; 3 sn'lik cevap penceresini uzat
    await interaction.response.defer(thinking=True)

    kod_norm = _kod_normalle(kod)
    try:
        df = await asyncio.to_thread(market.fetch_history, kod_norm)

        # Kod tutmadıysa: kullanıcı şirket adı yazmış olabilir, alias dene
        if not market.is_valid(df):
            adaylar = find_tickers(kod)
            if len(adaylar) == 1:
                kod_norm = next(iter(adaylar))
                df = await asyncio.to_thread(market.fetch_history, kod_norm)

        if not market.is_valid(df):
            await interaction.followup.send(
                f"`{kod_norm}` için veri bulunamadı. Kodu kontrol et (örn. `THYAO`).")
            return

        df = await asyncio.to_thread(market.compute_indicators, df)
        ov = await asyncio.to_thread(market.get_overview, kod_norm)
        # Teknik görünüm TAM seri üzerinden hesaplanır (periyottan bağımsız);
        # view'da saklanıp düğme tıklamalarında yeniden hesaplanmaz.
        gor = await asyncio.to_thread(analiz.teknik_gorunum, df)
        pencere = market.slice_window(df, secilen)
        buf = await asyncio.to_thread(
            charting.render_chart, pencere, kod_norm, ov["ad"])

        embed = _overview_embed(ov, secilen, gor)
        dosya = discord.File(buf, filename="grafik.png")
        embed.set_image(url="attachment://grafik.png")  # dosya adıyla birebir aynı
        # Periyot düğmeleri: tam df + özet view'da saklanır, tıklamada yeniden
        # veri çekmeden pencere yeniden dilimlenir.
        view = GrafikView(df, ov, kod_norm, ov["ad"], secilen, gor)
        await interaction.followup.send(embed=embed, file=dosya, view=view)
        view.message = await interaction.original_response()
        log.info("/hisse %s (%s) gönderildi — %s", kod_norm, secilen, interaction.user)

    except Exception:
        log.exception("/hisse hatası (%s)", kod_norm)
        await interaction.followup.send(
            "Beklenmeyen bir hata oluştu, lütfen birazdan tekrar dene.")


@hisse.autocomplete("kod")
async def kod_autocomplete(interaction: discord.Interaction, current: str):
    """tickers.py listesinden kod VE şirket adıyla eşleşen ilk 25 öneri."""
    f = fold(current)
    oneriler: list[app_commands.Choice[str]] = []
    for code, aliases in TICKERS.items():
        ad = aliases[0].title() if aliases else code
        if not f or f in fold(code) or any(f in fold(a) for a in aliases):
            oneriler.append(
                app_commands.Choice(name=f"{code} — {ad}"[:100], value=code))
            if len(oneriler) >= 25:  # Discord üst sınırı
                break
    return oneriler


def _ts_tr(ts: float | None) -> str:
    """Epoch saniyeyi Türkiye saatiyle 'gg.aa.yyyy SS:DD' biçimine çevirir."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=3)))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "—"


@client.tree.command(name="health",
                     description="Haber botunun kaynak sağlığını kontrol eder (RSS/KAP scrape testi)")
async def health(interaction: discord.Interaction):
    """Haber çeken botun (main.py) kaynaklarını CANLI yoklar ve durum raporu verir.

    Kaynaklar bu komutun çalıştığı ortamdan denenir; scrape kırıksa hatayı gösterir.
    Haber botu ayrı bir süreç olduğu için bu, paylaşılan kaynak erişimini test eder.
    """
    await interaction.response.defer(thinking=True)
    feeds = sources.feeds_from_env()
    enable_kap = os.getenv("ENABLE_KAP", "1") == "1"

    try:
        async with aiohttp.ClientSession() as session:
            rapor = await sources.probe(session, feeds, enable_kap)
    except Exception:
        log.exception("/health yoklama hatası")
        await interaction.followup.send(
            "Sağlık kontrolü sırasında beklenmeyen bir hata oluştu.")
        return

    ok_sayi = sum(1 for r in rapor if r["ok"])
    toplam = len(rapor)
    if toplam and ok_sayi == toplam:
        renk, durum = _YESIL, "Tüm kaynaklar çalışıyor"
    elif ok_sayi == 0:
        renk, durum = _KIRMIZI, "Hiçbir kaynak çalışmıyor"
    else:
        renk, durum = 0xE67E22, "Bazı kaynaklarda sorun var"

    e = discord.Embed(
        title="🩺 Haber Botu Sağlık Kontrolü",
        description=f"{durum} · {ok_sayi}/{toplam} kaynak",
        color=renk,
    )

    satirlar = []
    for r in rapor:
        isim = (r["ad"] or r["url"])[:48]
        if r["ok"]:
            satirlar.append(f"✅ **{isim}** — {r['adet']} öğe · {r['sure_ms']} ms")
        else:
            satirlar.append(f"❌ **{isim}** — {str(r['hata'])[:140]}")
    if not enable_kap:
        satirlar.append("➖ **KAP** — devre dışı (ENABLE_KAP=0)")
    e.add_field(name="Kaynaklar", value="\n".join(satirlar)[:1024] or "—", inline=False)

    # seen.db (varsa) özeti — salt okunur, yan etkisiz
    db_path = os.getenv("SEEN_DB_PATH", "seen.db")
    st = await asyncio.to_thread(store.db_stats, db_path)
    if st:
        db_txt = f"{fmt.tr_sayi(st['kayit'], 0)} kayıt · son: {_ts_tr(st['son_ts'])}"
    else:
        db_txt = "bulunamadı (haber botu bu makinede çalışmamış olabilir)"
    e.add_field(name="📦 seen.db", value=db_txt, inline=False)

    e.set_footer(text="Canlı tanı: kaynaklar bu komutun çalıştığı ortamdan denenir. "
                      "Haber botu (main.py) ayrı bir süreçtir.")
    await interaction.followup.send(embed=e)
    log.info("/health — %d/%d kaynak ok (%s)", ok_sayi, toplam, interaction.user)


@client.event
async def on_ready():
    log.info("Bot hazır: %s (%d sunucu)", client.user, len(client.guilds))


if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN boş. .env dosyasını doldur "
                         "(Discord Developer Portal -> Bot -> Reset Token).")
    try:
        client.run(token, log_handler=None)  # logging'i kendimiz kurduk
    except KeyboardInterrupt:
        print("\nKapatıldı.")
