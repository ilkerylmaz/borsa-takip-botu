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

import discord
from discord import app_commands
from dotenv import load_dotenv

import charting
import fmt
import market
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

PERIYOTLAR = [
    app_commands.Choice(name="1 Hafta", value="1 Hafta"),
    app_commands.Choice(name="1 Ay", value="1 Ay"),
]


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


def _overview_embed(ov: dict, periyot: str) -> discord.Embed:
    """Özet verilerden embed kurar; eksik alanlar 'veri yok' / '—' gösterilir."""
    degisim = ov["degisim_yuzde"]
    renk = _GRI if degisim is None else (_YESIL if degisim >= 0 else _KIRMIZI)

    baslik = ov["kod"] if ov["ad"] == ov["kod"] else f"{ov['kod']} — {ov['ad']}"
    e = discord.Embed(
        title=baslik,
        description=f"Günlük mum • {periyot} • SMA200 · MACD · RSI",
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

    e.set_footer(text="Veriler Yahoo Finance kaynaklıdır, ~15 dk gecikmeli olabilir. "
                      "Yatırım tavsiyesi değildir.")
    return e


@client.tree.command(name="hisse", description="BIST hissesi: fiyat, hacim ve teknik grafik (SMA200, MACD, RSI)")
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
        pencere = market.slice_window(df, secilen)
        buf = await asyncio.to_thread(
            charting.render_chart, pencere, kod_norm, ov["ad"])

        embed = _overview_embed(ov, secilen)
        dosya = discord.File(buf, filename="grafik.png")
        embed.set_image(url="attachment://grafik.png")  # dosya adıyla birebir aynı
        await interaction.followup.send(embed=embed, file=dosya)
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
