"""Penjelajah internet: mengunjungi halaman, merangkum, menyimpan, dan menyebar tautan."""

import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

from . import VERSION, REPO, llm
from .cluster import log

UA = f"ItsMeNode/{VERSION} (+https://github.com/{REPO})"
MAX_BYTES = 2_000_000
LINKS_PER_PAGE = 40
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|pdf|zip|rar|7z|gz|exe|msi|apk|dmg|iso|"
                      r"mp[34]|avi|mkv|mov|wav|flac|css|js|json|xml|rss|woff2?|ttf)$", re.I)
SEEDS = [
    "https://id.wikipedia.org/wiki/Istimewa:Halaman_sembarang",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://www.antaranews.com/",
    "https://www.kompas.com/",
    "https://www.detik.com/",
    "https://www.bbc.com/news",
    "https://apnews.com/",
    "https://news.ycombinator.com/",
    "https://www.nature.com/news",
    "https://arstechnica.com/",
    "https://www.sciencedaily.com/news/",
]


LANGS = ("id", "en", "ms")
WIKI = re.compile(r"^([a-z\-]+)\.(m\.)?(wikipedia|wiktionary|wikibooks|wikinews|wikiquote|"
                  r"wikisource|wikivoyage|wikiversity)\.org$")
SKIP_HOST = re.compile(r"(^|\.)(wikimedia|wikidata|mediawiki|wikimediafoundation)\.org$")
SKIP_PATH = re.compile(r"/(login|signin|signup|register|logout|cart|checkout|search)\b|"
                       r"[?&](action|oldid|diff|printable|share|replytocom)=", re.I)


def wanted(url):
    """Saring halaman yang tidak berguna: wiki bahasa lain, halaman meta, login, dsb."""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    if SKIP_HOST.search(host) or SKIP_PATH.search(p.path + "?" + p.query):
        return False
    m = WIKI.match(host)
    if m:
        if m.group(1) not in LANGS:
            return False
        page = urllib.parse.unquote(p.path.removeprefix("/wiki/"))
        if ":" in page and not page.startswith(("Special:Random", "Istimewa:Halaman_sembarang")):
            return False
    return True


def normalize(url, base=None):
    try:
        if base:
            url = urllib.parse.urljoin(base, url)
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    if SKIP_EXT.search(p.path):
        return None
    host = p.hostname.lower()
    netloc = host + (f":{p.port}" if p.port and p.port not in (80, 443) else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", p.query, ""))


def domain_of(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


class Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "td", "blockquote", "pre", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.links, self.parts, self.lang = "", [], [], ""
        self._skip, self._in_title = 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "html":
            self.lang = (dict(attrs).get("lang") or "").lower()[:2]
        elif tag == "a":
            a = dict(attrs)
            href = a.get("href")
            if href and "nofollow" not in (a.get("rel") or ""):
                self.links.append(href)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        lines = [ln.strip() for ln in t.split("\n")]
        return "\n".join(ln for ln in lines if len(ln) > 40)


def fetch(url, timeout=15):
    """(url akhir, teks html) atau None kalau bukan halaman HTML."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5",
                                               "Accept-Language": "id,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if "html" not in r.headers.get("Content-Type", ""):
            return None
        raw = r.read(MAX_BYTES)
        charset = r.headers.get_content_charset() or "utf-8"
        return r.geturl(), raw.decode(charset, errors="replace")


def extractive(text, limit=500):
    sents = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    out = ""
    for s in sents:
        if len(out) + len(s) > limit:
            break
        out += s + " "
    return out.strip() or text[:limit]


class Crawler:
    def __init__(self, cfg, profile, cluster, store):
        self.cfg, self.profile, self.cluster, self.store = cfg, profile, cluster, store
        self.lock = threading.Lock()
        self.next_time = {}  # domain -> waktu boleh dikunjungi lagi
        self.busy = set()
        self.robots = {}
        self.owned, self.owned_at = [], 0
        self.last_seed = 0
        self.pages_done = 0

    # --- pilih pekerjaan ------------------------------------------------------
    def _refresh_owned(self):
        me = self.cluster.id
        doms = self.store.frontier_domains()
        self.owned = [d for d in doms if (self.cluster.owner(d) or {}).get("id") == me]
        random.shuffle(self.owned)
        self.owned_at = time.time()

    def _pick(self):
        with self.lock:
            if time.time() - self.owned_at > 5:
                self._refresh_owned()
            now, cap = time.time(), self.cfg["max_pages_per_domain"]
            for d in self.owned:
                if d in self.busy or self.next_time.get(d, 0) > now:
                    continue
                if self.store.domain_pages(d) >= cap and not d.endswith("wikipedia.org"):
                    continue
                row = self.store.pop(d)
                if row:
                    self.busy.add(d)
                    self.next_time[d] = now + self.cfg["domain_delay"]
                    return d, row
            if not self.owned and now - self.last_seed > 30:
                self.last_seed = now
                self.store.add_links([(normalize(u), domain_of(u), 0, 1)
                                      for u in random.sample(SEEDS, 3)])
                self.owned_at = 0
        return None

    def _robots_ok(self, url, domain):
        rp = self.robots.get(domain)
        if rp is None or time.time() - rp.mtime() > 86400:
            rp = urllib.robotparser.RobotFileParser()
            root = urllib.parse.urlsplit(url)
            try:
                got = fetch_text(f"{root.scheme}://{root.netloc}/robots.txt")
                rp.parse(got.splitlines() if got is not None else [])
            except Exception:
                rp.parse([])
            rp.modified()
            self.robots[domain] = rp
        return rp.can_fetch(UA, url)

    # --- kerjakan satu halaman ------------------------------------------------
    def process(self, domain, url, depth, seed):
        if not seed and self.store.visited(url):
            return
        if not self._robots_ok(url, domain):
            self.store.mark_visited(url, domain)
            return
        try:
            got = fetch(url)
        except Exception:
            self.store.mark_visited(url, domain)
            return
        self.store.mark_visited(url, domain)
        if not got:
            return
        final, html = got
        final = normalize(final) or url
        if final != url:
            if self.store.visited(final):
                return
            self.store.mark_visited(final, domain_of(final))
        page = Page()
        try:
            page.feed(html)
        except Exception:
            return
        if page.lang and page.lang not in LANGS:
            return  # bahasa lain: tidak disimpan dan tautannya tidak diikuti
        title = re.sub(r"\s+", " ", page.title).strip()[:200] or final
        text = page.text()
        if len(text) >= 400:
            summary = self.summarize(title, text)
            if summary and self.store.add_doc(final, title, summary, self.cfg["name"]):
                self.pages_done += 1
                rep = self.cluster.replica_for(domain)
                if rep:
                    self.cluster.rpc(rep["addr"], "/doc", {"url": final, "title": title,
                                     "summary": summary, "node": self.cfg["name"]})
        self.spread(final, page.links, depth + 1)

    def summarize(self, title, text):
        model = self.profile["model"]
        if model:
            try:
                return llm.summarize(self.cfg["ollama_url"], model, title, text)
            except Exception:
                pass
        # Tanpa model: minta tolong node lain yang punya model dan sedang menganggur.
        helpers = [s for i, s in self.cluster.alive().items()
                   if i != self.cluster.id and s.get("model") and s.get("working")]
        random.shuffle(helpers)
        for h in sorted(helpers, key=lambda s: -s.get("score", 0))[:2]:
            res = self.cluster.rpc(h["addr"], "/summarize", {"title": title, "text": text[:6000]},
                                   timeout=300)
            if res and res.get("summary"):
                return res["summary"]
        return extractive(text)

    def spread(self, base, links, depth):
        seen, rows = set(), []
        random.shuffle(links)
        for href in links:
            u = normalize(href, base)
            if not u or u in seen or not wanted(u):
                continue
            seen.add(u)
            rows.append((u, domain_of(u), depth, 0))
            if len(rows) >= LINKS_PER_PAGE:
                break
        # Simpan semua di antrean lokal dulu; rebalance meneruskan milik node lain.
        self.store.add_links(rows)

    # --- loop ------------------------------------------------------------------
    def can_work(self):
        return self.cluster.working and not self.cluster.paused

    def worker(self, stop):
        while not stop.is_set():
            if not self.can_work():
                stop.wait(1)
                continue
            job = self._pick()
            if not job:
                stop.wait(1)
                continue
            domain, (url, depth, seed) = job
            try:
                self.process(domain, url, depth, seed)
            except Exception as e:
                log("gagal memproses", url, e)
            finally:
                with self.lock:
                    self.busy.discard(domain)

    def rebalance(self, stop):
        """Teruskan antrean domain yang bukan milik node ini ke pemiliknya."""
        while not stop.wait(15):
            if self.cluster.paused:
                continue
            groups = {}
            for d in self.store.frontier_domains():
                o = self.cluster.owner(d)
                if o and o["id"] != self.cluster.id:
                    groups.setdefault(o["addr"], []).append(d)
            for addr, doms in groups.items():
                rows = self.store.take_domains(doms)
                if not rows:
                    continue
                res = self.cluster.rpc(addr, "/links", {"rows": rows}, timeout=20)
                if res is None:
                    self.store.give_back(rows)

    def start(self, stop):
        for _ in range(self.profile["workers"]):
            threading.Thread(target=self.worker, args=(stop,), daemon=True).start()
        threading.Thread(target=self.rebalance, args=(stop,), daemon=True).start()


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(500_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return "" if e.code in (404, 410) else "User-agent: *\nDisallow: /\n" if e.code in (401, 403) else ""
