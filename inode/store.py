"""Penyimpanan lokal per node (SQLite): antrean URL, riwayat kunjungan, dan hasil riset."""

import os
import re
import sqlite3
import threading
import time

MAX_FRONTIER = 300_000
MAX_PER_DOMAIN_QUEUE = 500


class Store:
    def __init__(self, data_dir):
        self.db = sqlite3.connect(os.path.join(data_dir, "node.db"), check_same_thread=False,
                                  timeout=30)
        self.lock = threading.RLock()
        self._stats, self._stats_at = {}, 0.0
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS frontier(url TEXT PRIMARY KEY, domain TEXT,
                    depth INT, seed INT DEFAULT 0, added REAL);
                CREATE INDEX IF NOT EXISTS frontier_domain ON frontier(domain, depth);
                CREATE TABLE IF NOT EXISTS visited(url TEXT PRIMARY KEY, ts REAL);
                CREATE TABLE IF NOT EXISTS domains(domain TEXT PRIMARY KEY, pages INT DEFAULT 0);
                CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, url TEXT UNIQUE,
                    title TEXT, summary TEXT, ts REAL, node TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(title, summary);
                CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE IF NOT EXISTS enrolled(fp TEXT PRIMARY KEY, name TEXT, ts REAL);
            """)

    # --- perangkat terdaftar (supaya bisa gabung lagi walau diinstal ulang) ----
    def enroll_device(self, fp, name=""):
        if not fp:
            return
        with self.lock:
            self.db.execute(
                "INSERT INTO enrolled(fp,name,ts) VALUES(?,?,?) ON CONFLICT(fp) "
                "DO UPDATE SET name=excluded.name, ts=excluded.ts",
                (fp, (name or "")[:80], time.time()))
            self.db.commit()

    def is_enrolled(self, fp):
        with self.lock:
            return bool(fp and self.db.execute(
                "SELECT 1 FROM enrolled WHERE fp=?", (fp,)).fetchone())

    def enrolled_list(self):
        with self.lock:
            return [{"fp": f, "name": n, "ts": t}
                    for f, n, t in self.db.execute("SELECT fp,name,ts FROM enrolled")]

    def merge_enrolled(self, rows):
        """Satukan daftar perangkat dari node lain (ambil yang terbaru)."""
        with self.lock:
            for r in rows or []:
                fp = r.get("fp")
                if fp:
                    self.db.execute(
                        "INSERT INTO enrolled(fp,name,ts) VALUES(?,?,?) ON CONFLICT(fp) "
                        "DO UPDATE SET name=CASE WHEN excluded.ts > enrolled.ts "
                        "THEN excluded.name ELSE enrolled.name END, "
                        "ts=MAX(enrolled.ts, excluded.ts)",
                        (fp, (r.get("name") or "")[:80], float(r.get("ts") or 0)))
            self.db.commit()

    # --- antrean URL -----------------------------------------------------------
    def add_links(self, rows):
        """rows: [(url, domain, depth, seed)]. Mengembalikan jumlah yang masuk."""
        added = 0
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]
            per = {}
            for url, domain, depth, seed in rows:
                if total >= MAX_FRONTIER and not seed:
                    break
                if not seed:
                    if self.db.execute("SELECT 1 FROM visited WHERE url=?", (url,)).fetchone():
                        continue
                    if domain not in per:
                        per[domain] = self.db.execute(
                            "SELECT COUNT(*) FROM frontier WHERE domain=?", (domain,)).fetchone()[0]
                    if per[domain] >= MAX_PER_DOMAIN_QUEUE:
                        continue
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO frontier VALUES(?,?,?,?,?)",
                    (url, domain, depth, int(seed), time.time()))
                if cur.rowcount:
                    added += 1
                    total += 1
                    per[domain] = per.get(domain, 0) + 1
            self.db.commit()
        return added

    def frontier_domains(self, limit=20000):
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT DISTINCT domain FROM frontier LIMIT ?", (limit,))]

    def pop(self, domain):
        with self.lock:
            row = self.db.execute(
                "SELECT url, depth, seed FROM frontier WHERE domain=? ORDER BY depth, added LIMIT 1",
                (domain,)).fetchone()
            if row:
                self.db.execute("DELETE FROM frontier WHERE url=?", (row[0],))
                self.db.commit()
            return row

    def take_domains(self, domains, limit=2000):
        """Ambil (dan hapus) antrean milik domain tertentu untuk diteruskan ke node lain."""
        out = []
        with self.lock:
            for d in domains:
                rows = self.db.execute(
                    "SELECT url, domain, depth, seed FROM frontier WHERE domain=? LIMIT ?",
                    (d, limit - len(out))).fetchall()
                out.extend(rows)
                if len(out) >= limit:
                    break
            for r in out:
                self.db.execute("DELETE FROM frontier WHERE url=?", (r[0],))
            self.db.commit()
        return out

    def give_back(self, rows):
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO frontier VALUES(?,?,?,?,?)",
                                [(*r, time.time()) for r in rows])
            self.db.commit()

    def frontier_size(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]

    # --- kunjungan -------------------------------------------------------------
    def visited(self, url):
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM visited WHERE url=?", (url,)).fetchone())

    def mark_visited(self, url, domain):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO visited VALUES(?,?)", (url, time.time()))
            self.db.execute("INSERT INTO domains VALUES(?,1) ON CONFLICT(domain) "
                            "DO UPDATE SET pages=pages+1", (domain,))
            self.db.commit()

    def domain_pages(self, domain):
        with self.lock:
            r = self.db.execute("SELECT pages FROM domains WHERE domain=?", (domain,)).fetchone()
            return r[0] if r else 0

    # --- hasil riset -----------------------------------------------------------
    def add_doc(self, url, title, summary, node):
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO docs(url,title,summary,ts,node) "
                                  "VALUES(?,?,?,?,?)", (url, title, summary, time.time(), node))
            if cur.rowcount:
                self.db.execute("INSERT INTO docs_fts(rowid,title,summary) VALUES(?,?,?)",
                                (cur.lastrowid, title, summary))
            self.db.commit()
            return bool(cur.rowcount)

    def search(self, query, limit=8):
        words = re.findall(r"\w+", query.lower())
        words = [w for w in words if len(w) > 2] or words
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words[:12])
        with self.lock:
            rows = self.db.execute(
                "SELECT d.url, d.title, d.summary, d.node, bm25(docs_fts) AS rank "
                "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
                "WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)).fetchall()
        return [{"url": u, "title": t, "summary": s, "node": n, "rank": r}
                for u, t, s, n, r in rows]

    def recent(self, limit=10):
        with self.lock:
            rows = self.db.execute("SELECT url, title, summary, node FROM docs "
                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"url": u, "title": t, "summary": s, "node": n} for u, t, s, n in rows]

    def stats(self, max_age=15):
        """Jumlah dokumen/kunjungan/antrean. Disimpan sebentar karena dipanggil di
        setiap gosip, dan COUNT(*) makin lambat saat datanya membesar."""
        now = time.time()
        if now - self._stats_at > max_age:
            with self.lock:
                q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
                self._stats = {"docs": q("SELECT COUNT(*) FROM docs"),
                               "visited": q("SELECT COUNT(*) FROM visited"),
                               "frontier": q("SELECT COUNT(*) FROM frontier")}
            self._stats_at = now
        return dict(self._stats)

    # --- checkpoint kecil ------------------------------------------------------
    def get(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return r[0] if r else default

    def put(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))
            self.db.commit()
