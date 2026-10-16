"""Union of public RSS, section pages and bounded rotating sitemaps."""
import argparse
import concurrent.futures
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .common import article_id, canonical, date_value, iso, load, now, plain, save

UA = "PersonalNewsCatalogue/1.0"
MAX_BYTES = 8_000_000


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.current, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.current, self.text = attrs.get("href"), []

    def handle_data(self, data):
        if self.current:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.links.append((self.current, plain(" ".join(self.text))))
            self.current = None


def xml_root(data):
    # Refuse DTD/entity declarations before parsing; cap response/decompression size.
    if b"\x00" in data or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, re.I):
        raise ValueError("unsafe XML")
    return ET.fromstring(data)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    return next((x for x in el if local(x.tag) == name), None)


def text_of(el, name):
    x = child(el, name)
    return "".join(x.itertext()).strip() if x is not None else ""


def parse_xml(data, base):
    root = xml_root(data)
    items, maps = [], []
    if local(root.tag) == "sitemapindex":
        for el in root:
            loc = text_of(el, "loc")
            if loc:
                maps.append((urljoin(base, loc), text_of(el, "lastmod")))
    elif local(root.tag) == "urlset":
        for el in root:
            n = child(el, "news")
            items.append({"url": text_of(el, "loc"),
                          "title": text_of(n, "title") if n is not None else "",
                          "published": text_of(n, "publication_date") if n is not None else "",
                          "lastmod": text_of(el, "lastmod"), "date_basis": "news_sitemap"})
    else:
        if local(root.tag) not in {"rss", "feed", "RDF"}:
            raise ValueError("not RSS/Atom/sitemap")
        for el in root.iter():
            if local(el.tag) not in {"item", "entry"}:
                continue
            link = text_of(el, "link")
            for x in el:
                if local(x.tag) == "link" and x.get("rel", "alternate") == "alternate" and x.get("href"):
                    link = x.get("href")
                    break
            pub = text_of(el, "pubDate") or text_of(el, "published") or text_of(el, "date")
            items.append({"url": urljoin(base, link), "title": plain(text_of(el, "title")),
                          "published": pub, "lastmod": text_of(el, "updated"), "date_basis": "feed"})
    return items, maps


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Fetcher:
    def __init__(self, site, cfg):
        self.site, self.cfg = site, cfg
        self.health, self.robots, self.last_request = [], {}, 0
        self.opener = urllib.request.build_opener(NoRedirect)

    def allowed_host(self, url):
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname in self.site["fetch_hosts"]
                and not p.username and not p.password and p.port in (None, 443))

    def request(self, url):
        for redirect in range(5):
            if not self.allowed_host(url):
                raise ValueError("host rejected")
            time.sleep(max(0, self.cfg["request_delay_seconds"] - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            try:
                with self.opener.open(req, timeout=self.cfg["http_timeout_seconds"]) as resp:
                    data = resp.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise ValueError("response too large")
                    if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                        import io
                        with gzip.GzipFile(fileobj=io.BytesIO(data)) as z:
                            data = z.read(MAX_BYTES + 1)
                        if len(data) > MAX_BYTES:
                            raise ValueError("expanded response too large")
                    return data
            except urllib.error.HTTPError as e:
                if e.code in {301, 302, 303, 307, 308}:
                    url = urljoin(url, e.headers.get("Location", ""))
                    continue
                raise
        raise ValueError("too many redirects")

    def robot(self, url):
        p = urlsplit(url)
        base = f"https://{p.netloc}/robots.txt"
        if base not in self.robots:
            r = urllib.robotparser.RobotFileParser(base)
            try:
                raw = self.request(base).decode("utf-8", "replace")
                r.parse(raw.splitlines())
                self.health.append({"source": base, "status": "ok", "kind": "robots"})
                self.robots[base] = r
            except Exception as e:
                # RFC 9309 2.3.1.3: robots 4xx means unavailable, not a site-wide
                # Disallow. S3 feed hosts commonly return 403 for a missing key.
                # Treat 429 as rate limiting and defer instead of adding requests.
                unavailable = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429
                if unavailable:
                    r.parse([])
                    self.robots[base] = r
                else:
                    self.robots[base] = None
                self.health.append({"source": base, "status": "unavailable_4xx" if unavailable else error_code(e),
                                    "http_status": e.code if isinstance(e, urllib.error.HTTPError) else None,
                                    "kind": "robots", "policy": "no_rules_available" if unavailable else "defer_host"})
        return self.robots[base]

    def fetch(self, url):
        r = self.robot(url)
        if r is None or not r.can_fetch(UA, url):
            raise ValueError("robots denied or unavailable")
        return self.request(url)


def error_code(e):
    return f"HTTP_{e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__ + ": " + str(e)[:120]


def collect_site(site, cfg, cursor):
    f = Fetcher(site, cfg)
    found, seen = [], set()
    def get(url, kind):
        if url in seen:
            return []
        seen.add(url)
        try:
            data = f.fetch(url)
            if kind == "html":
                parser = Links()
                parser.feed(data.decode("utf-8", "replace"))
                items, maps = [{"url": urljoin(url, u), "title": t} for u, t in parser.links], []
            else:
                items, maps = parse_xml(data, url)
            found.extend({**x, "source": url} for x in items)
            f.health.append({"source": url, "kind": kind, "status": "ok", "raw_items": len(items), "child_maps": len(maps)})
            return maps
        except Exception as e:
            f.health.append({"source": url, "kind": kind, "status": error_code(e)})
            return []
    for url in site["feeds"]:
        get(url, "feed")
    for url in site["pages"]:
        get(url, "html")
    roots = list(site["sitemaps"])
    if cfg["discover_sitemaps_from_robots"]:
        r = f.robot("https://www." + site["domain"] + "/")
        if r:
            roots += r.site_maps() or []
    queue = []
    for url in dict.fromkeys(roots):
        if f.allowed_host(url):
            queue.extend(get(url, "sitemap"))
    # Always revisit two newest dated maps; rotate remaining maps, including undated ones.
    queue = sorted(dict(queue).items(), key=lambda x: (x[1], x[0]), reverse=True)
    budget = cfg["sitemap_child_budget_per_site"]
    selected = queue[:min(2, budget)]
    rest = queue[len(selected):]
    if rest:
        offset = cursor % len(rest)
        selected += (rest[offset:] + rest[:offset])[:max(0, budget - len(selected))]
    selected_count = len(selected)
    index = 0
    while index < len(selected) and index < budget:
        u, _ = selected[index]
        nested = get(u, "sitemap")
        selected.extend(x for x in nested if x[0] not in seen)
        index += 1
    f.health.append({"kind": "coverage", "status": "bounded", "site": site["id"],
                     "known_child_maps": len(queue), "selected_child_maps": min(len(selected), budget),
                     "not_scanned_this_run": max(0, len(queue) - selected_count) + max(0, len(selected) - budget)})
    return found, f.health, cursor + max(1, budget - 2)


def merge(catalogue, candidates, site, cfg, at):
    accepted, fresh = 0, 0
    for x in candidates:
        url = canonical(x.get("url", ""), site["domain"])
        if not url:
            continue
        pub = date_value(x.get("published"))
        basis = x.get("date_basis", "unknown")
        if pub is None:
            m = re.search(r"/(\d{4}/\d{2}/\d{2})/", url)
            pub = date_value(m[1].replace("/", "-")) if m else None
            basis = "url_day_utc_assumed" if pub else "unknown"
        modified = date_value(x.get("lastmod"))
        if pub is None and modified and modified < at - timedelta(days=cfg["lookback_days"]):
            # Recency filter only. Modification time is never relabelled publication.
            continue
        # lastmod is NOT a publication date. Unknown-date entries remain visible.
        if pub and (pub < at - timedelta(days=cfg["lookback_days"]) or pub > at + timedelta(days=1)):
            continue
        aid = article_id(url)
        accepted += 1
        if aid not in catalogue:
            catalogue[aid] = {"id": aid, "site": site["id"], "url": url, "title": "",
                              "first_seen": iso(at), "sources": [], "fetch_status": "not_attempted", "attempts": 0}
            fresh += 1
        a = catalogue[aid]
        a["last_seen"] = iso(at)
        title = plain(x.get("title"))
        if title and a.get("title") != title:
            if a.get("title"):
                a["previous_titles"] = (a.get("previous_titles", []) + [a["title"]])[-3:]
            a["title"] = title
        if pub and (not a.get("published") or basis in {"feed", "news_sitemap"}):
            a.update(published=iso(pub), date_basis=basis)
        if not a.get("published"):
            a["date_basis"] = "unknown"
        if x.get("lastmod"):
            a["source_lastmod"] = x["lastmod"]
        if x["source"] not in a["sources"]:
            a["sources"].append(x["source"])
    return accepted, fresh


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--state", default=".runtime/state")
    args = p.parse_args()
    cfg, state = load(args.config), Path(args.state)
    catalogue = load(state / "catalogue.json", {})
    health = load(state / "health.json", {"runs": [], "cursors": {}})
    at = now()
    run = {"at": iso(at), "sources": [], "sites": {}}
    if health["runs"]:
        run["gap_hours"] = round((at - date_value(health["runs"][-1]["at"])).total_seconds() / 3600, 2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(collect_site, s, cfg, health["cursors"].get(s["id"], 0)): s for s in cfg["sites"]}
        for task in concurrent.futures.as_completed(tasks):
            s = tasks[task]
            rows, observations, cursor = task.result()
            accepted, fresh = merge(catalogue, rows, s, cfg, at)
            health["cursors"][s["id"]] = cursor
            run["sources"].extend(observations)
            run["sites"][s["id"]] = {"accepted_observations": accepted, "new_unique": fresh,
                                      "ok_sources": sum(x["status"] == "ok" and x["kind"] != "robots" for x in observations)}
    health["runs"] = (health["runs"] + [run])[-72:]
    save(state / "catalogue.json", catalogue)
    save(state / "health.json", health)
    save(state / "ledger.json", load(state / "ledger.json", {"days": {}}))
    summary = "### Collection\n" + json.dumps(run["sites"], ensure_ascii=False) + "\n\nCoverage is unknown; consult health.json.\n"
    print(summary)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)


if __name__ == "__main__":
    main()
