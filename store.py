"""
Tekilleştirme deposu (SQLite).
Aynı haberin iki kez gönderilmesini engeller. Kararlı bir 'uid' (link veya KAP id)
üzerinden çalışır. İstersen gönderilen haberleri denetim için saklar.

Veri kıymetli değildir: bir haber RSS akışından düştükten sonra kaydı gereksizdir.
Bu yüzden RETENTION_DAYS'ten eski kayıtlar açılışta silinir (boyut sabit kalır)
ve veritabanının tamamen kaybı bile tek sessiz tura mal olur (bkz. main.py).
"""

from __future__ import annotations
import sqlite3
import time

from filters import olay_benzer

# Akışlar haberi en fazla birkaç gün taşır; 60 gün bol bol güvenli pencere.
RETENTION_DAYS = 60

# Çapraz-kaynak tekilleştirme penceresi: aynı haberin başka kaynakta yeniden
# yayını / aynı olayın farklı başlıkla ikinci haberi saatler içinde gelir;
# 2 gün sonra aynı anahtar büyük olasılıkla YENİ bir olaydır (ör. yeni temettü
# açıklaması) — o yüzden pencere kısa tutulur, RETENTION_DAYS'e bağlanmaz.
DUP_WINDOW_DAYS = 2


class SeenStore:
    def __init__(self, path: str = "seen.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS seen (
                   uid TEXT PRIMARY KEY,
                   source TEXT,
                   title TEXT,
                   score INTEGER,
                   ts REAL
               )"""
        )
        # Eski db'ye (Actions cache'inden gelen) yeni sütunları ekle; eski
        # satırlarda anahtarlar NULL kalır — yalnız 2 günlük pencerede iş
        # gördüklerinden geriye doldurma gerekmez.
        for col in ("tkey", "ekey"):
            try:
                self.conn.execute(f"ALTER TABLE seen ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # sütun zaten var
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_seen_tkey ON seen(tkey)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_seen_ekey ON seen(ekey)")
        self.conn.commit()
        self._prune()

    def _prune(self) -> None:
        """RETENTION_DAYS'ten eski kayıtları siler; db boyutunu sabit tutar."""
        esik = time.time() - RETENTION_DAYS * 86400
        cur = self.conn.execute("DELETE FROM seen WHERE ts < ?", (esik,))
        self.conn.commit()  # VACUUM açık işlem varken çalışmaz; önce commit
        if cur.rowcount:
            self.conn.execute("VACUUM")

    def is_seen(self, uid: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen WHERE uid = ?", (uid,))
        return cur.fetchone() is not None

    def is_recent_dup(self, tkey: str = "", ekey: str = "") -> bool:
        """Son DUP_WINDOW_DAYS içinde aynı başlık (tkey) ya da aynı olay (ekey)
        var mı? Aynı haberin başka kaynaktan/URL'den ikinci gelişini yakalar
        (uid farklı olduğundan is_seen yakalayamaz). Olay karşılaştırması
        bulanıktır: aynı baskın kelime + örtüşen varlıklar (filters.olay_benzer)."""
        esik = time.time() - DUP_WINDOW_DAYS * 86400
        if tkey:
            cur = self.conn.execute("SELECT 1 FROM seen WHERE tkey = ? AND ts > ? LIMIT 1", (tkey, esik))
            if cur.fetchone():
                return True
        if ekey:
            # Aday küme küçük: aynı baskın kelimeyle başlayan son kayıtlar.
            on_ek = ekey.split("|", 1)[0] + "|%"
            cur = self.conn.execute(
                "SELECT ekey FROM seen WHERE ekey LIKE ? AND ts > ?", (on_ek, esik))
            for (eski,) in cur:
                if olay_benzer(ekey, eski):
                    return True
        return False

    def is_empty(self) -> bool:
        """Hiç kayıt yoksa True (taze veritabanı -> ilk tur sessiz geçilir)."""
        cur = self.conn.execute("SELECT 1 FROM seen LIMIT 1")
        return cur.fetchone() is None

    def mark(self, uid: str, source: str = "", title: str = "", score: int = 0,
             tkey: str = "", ekey: str = "") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (uid, source, title, score, ts, tkey, ekey) VALUES (?,?,?,?,?,?,?)",
            (uid, source, title, score, time.time(), tkey or None, ekey or None),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def db_stats(path: str = "seen.db") -> dict | None:
    """seen.db özeti (SALT-OKUNUR): kayıt sayısı + en yeni/eski kayıt zamanı.

    Dosya yoksa/okunamazsa None döner. Yan etkisizdir: tablo OLUŞTURMAZ, prune
    ETMEZ — bot.py'deki /health bunu çağırır; SeenStore() kullanmak boş bir db
    yaratıp prune tetikleyeceği için salt-okunur bir bağlantı tercih edilir.
    """
    import os
    from pathlib import Path
    if not os.path.exists(path):
        return None
    try:
        # as_uri(): boşluk/parantez içeren Windows yollarını da geçerli
        # file: URI'sine çevirir (ham f"file:{path}" özel karakterde kırılır)
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute("SELECT COUNT(*), MAX(ts), MIN(ts) FROM seen").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {"kayit": row[0] or 0, "son_ts": row[1], "ilk_ts": row[2]}
