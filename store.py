"""
Tekilleştirme deposu (SQLite).
Aynı haberin iki kez gönderilmesini engeller. Kararlı bir 'uid' (link veya KAP id)
üzerinden çalışır. İstersen gönderilen haberleri denetim için saklar.
"""

from __future__ import annotations
import sqlite3
import time


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
