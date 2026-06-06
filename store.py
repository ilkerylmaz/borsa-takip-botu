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

# Akışlar haberi en fazla birkaç gün taşır; 60 gün bol bol güvenli pencere.
RETENTION_DAYS = 60


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

    def is_empty(self) -> bool:
        """Hiç kayıt yoksa True (taze veritabanı -> ilk tur sessiz geçilir)."""
        cur = self.conn.execute("SELECT 1 FROM seen LIMIT 1")
        return cur.fetchone() is None

    def mark(self, uid: str, source: str = "", title: str = "", score: int = 0) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (uid, source, title, score, ts) VALUES (?,?,?,?,?)",
            (uid, source, title, score, time.time()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
