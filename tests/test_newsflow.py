import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from newsflow import collect, digest, mail, seal, state_io
from newsflow.compat import analyse_page
from newsflow.common import article_id, canonical, date_value, load, save

AT = datetime(2026, 10, 16, 15, tzinfo=timezone.utc)  # 08:00 Los Angeles
URL = "https://www.nytimes.com/2026/10/15/world/example.html"


def config():
    return load(Path(__file__).resolve().parents[1] / "config.json")


def article():
    return {"id": article_id(URL), "site": "NYT", "url": URL, "title": "<script>alert(1)</script> & News",
            "first_seen": "2026-10-15T12:00:00+00:00", "sources": ["https://rss.nytimes.com/test"],
            "fetch_status": "not_attempted", "attempts": 0}


class CatalogueTests(unittest.TestCase):
    def test_robots_403_is_unavailable_not_blanket_disallow(self):
        cfg = config()
        site = next(x for x in cfg["sites"] if x["id"] == "WSJ")
        f = collect.Fetcher(site, cfg)
        err = urllib.error.HTTPError("https://feeds.content.dowjones.io/robots.txt", 403, "Forbidden", {}, None)
        with patch.object(f, "request", side_effect=[err, b"<rss/>"]):
            self.assertEqual(f.fetch(site["feeds"][0]), b"<rss/>")
        self.assertEqual(f.health[0]["status"], "unavailable_4xx")

    def test_robots_rate_limit_server_error_and_explicit_denial(self):
        cfg = config()
        site = next(x for x in cfg["sites"] if x["id"] == "WSJ")
        for status in [429, 503]:
            f = collect.Fetcher(site, cfg)
            err = urllib.error.HTTPError("https://www.wsj.com/robots.txt", status, "Unavailable", {}, None)
            with patch.object(f, "request", side_effect=err):
                with self.assertRaises(ValueError):
                    f.fetch("https://www.wsj.com/")
        f = collect.Fetcher(site, cfg)
        with patch.object(f, "request", return_value=b"User-agent: *\nDisallow: /\n") as request:
            with self.assertRaises(ValueError):
                f.fetch("https://www.wsj.com/")
            self.assertEqual(request.call_count, 1)

    def test_url_validation_and_dedup(self):
        self.assertEqual(canonical(URL + "?utm_source=x&smid=y#top", "nytimes.com"), URL)
        ft = "https://www.ft.com/content/12345678-1234-1234-1234-123456789abc"
        self.assertEqual(canonical(ft + "?syn-25a6b1a6=1", "ft.com"), ft)
        for url in ["https://nytimes.com.evil.test/2026/10/15/world/example.html", "file:///etc/passwd",
                    "https://user:password@nytimes.com/2026/10/15/world/a", "https://www.ft.com/world"]:
            self.assertIsNone(canonical(url, "nytimes.com" if "ft.com" not in url else "ft.com"))
        self.assertEqual(canonical("https://m.nytimes.com/2026/10/15/world/example.html/amp", "nytimes.com"), URL)

    def test_rss_atom_and_sitemap(self):
        rss = b'<rss><channel><item><title>A &amp; B</title><link>https://x/a</link><pubDate>Thu, 15 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        rows, _ = collect.parse_xml(rss, "https://x/feed")
        self.assertEqual(rows[0]["title"], "A & B")
        self.assertEqual(date_value(rows[0]["published"]).day, 15)
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom</title><link rel="self" href="bad"/><link href="/story"/><published>2026-10-15T12:00:00Z</published></entry></feed>'
        self.assertEqual(collect.parse_xml(atom, "https://x/feed")[0][0]["url"], "https://x/story")
        sm = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x/a</loc><lastmod>2026-10-15</lastmod></url></urlset>'
        self.assertEqual(collect.parse_xml(sm, "https://x/")[0][0]["published"], "")

    def test_reject_entity_and_wrong_xml(self):
        for data in [b'<!DOCTYPE x [<!ENTITY x "boom">]><rss/>', b'<html>challenge</html>']:
            with self.assertRaises(ValueError):
                collect.parse_xml(data, "https://x/")

    def test_merge_dates_provenance_and_title_revision(self):
        cfg = config()
        site = next(x for x in cfg["sites"] if x["id"] == "NYT")
        c = {}
        rows = [{"url": URL + "?utm_source=a", "source": "feed1", "title": "Title", "published": "2026-10-15T12:00:00Z", "date_basis": "feed"},
                {"url": URL, "source": "feed2", "title": "Updated", "lastmod": "2026-10-16"}]
        collect.merge(c, rows, site, cfg, AT)
        self.assertEqual(len(c), 1)
        a = next(iter(c.values()))
        self.assertEqual(a["sources"], ["feed1", "feed2"])
        self.assertEqual(a["date_basis"], "feed")
        self.assertIn("Title", a["previous_titles"])
        self.assertEqual(a["published"], "2026-10-15T12:00:00+00:00")

    def test_lastmod_is_not_publication(self):
        cfg = config()
        site = next(x for x in cfg["sites"] if x["id"] == "FT")
        c = {}
        collect.merge(c, [{"url": "https://www.ft.com/content/12345678-1234-1234-1234-123456789abc", "source": "sitemap", "lastmod": "2026-10-15"}], site, cfg, AT)
        a = next(iter(c.values()))
        self.assertNotIn("published", a)
        self.assertEqual(a["date_basis"], "unknown")

    def test_rotating_sitemaps(self):
        cfg = config()
        cfg.update(discover_sitemaps_from_robots=False, sitemap_child_budget_per_site=4)
        site = {"id": "test", "domain": "x", "feeds": [], "pages": [], "sitemaps": ["https://www.x/index"], "fetch_hosts": ["www.x"]}
        visited = []
        index = ('<sitemapindex>' + ''.join(f'<sitemap><loc>https://www.x/map{i}</loc></sitemap>' for i in range(8)) + '</sitemapindex>').encode()
        def fetch(_, url):
            visited.append(url)
            return index if url.endswith("index") else b'<urlset/>'
        with patch.object(collect.Fetcher, "fetch", fetch):
            _, _, cursor = collect.collect_site(site, cfg, 0)
            first = set(visited)
            visited.clear()
            collect.collect_site(site, cfg, cursor)
            self.assertTrue(set(visited) - first)


class DigestTests(unittest.TestCase):
    def test_timezone_daily_gate_and_duplicates(self):
        cfg, a = config(), article()
        c = {a["id"]: a}
        b = digest.plan(cfg, c, {"days": {}}, AT)
        self.assertEqual(b["target"], "2026-10-15")
        self.assertEqual(len(b["articles"]), 1)
        for status in ["sent", "pending"]:
            self.assertIsNone(digest.plan(cfg, c, {"days": {"2026-10-15": {"status": status}}}, AT))
        self.assertIsNone(digest.plan(cfg, c, {"days": {}}, AT.replace(hour=12)))

    def test_late_discovery_and_bpc_retry(self):
        cfg, a = config(), article()
        a.update(first_seen="2026-10-16T14:00:00Z", published="2026-10-15T12:00:00Z")
        self.assertEqual(len(digest.plan(cfg, {a["id"]: a}, {"days": {}}, AT)["articles"]), 1)
        a.update(mailed_at="2026-10-15", fetch_status="failed", attempts=1, last_attempt="2026-10-15T10:00:00Z")
        b = digest.plan(cfg, {a["id"]: a}, {"days": {}}, AT)
        self.assertEqual(b["articles"][0]["delivery_kind"], "正文重试补发")
        a["attempts"] = 2
        self.assertEqual(len(digest.plan(cfg, {a["id"]: a}, {"days": {}}, AT)["articles"]), 0)

    def test_html_escaping_and_csv_formula_safety(self):
        a = article()
        a["title"] = '=HYPERLINK("bad") <script>alert(1)</script>'
        b = digest.plan(config(), {a["id"]: a}, {"days": {}}, AT)
        b["bodies"][a["id"]] = '<img src="https://tracker.test">'
        with tempfile.TemporaryDirectory() as td:
            digest.write_bundle(td, b)
            page = (Path(td) / "reading.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", page)
            self.assertNotIn('<img src=', page)
            self.assertIn("&lt;img", page)
            self.assertIn("'=HYPERLINK", (Path(td) / "catalogue.csv").read_text(encoding="utf-8-sig"))


class AccessTests(unittest.TestCase):
    def test_challenge_and_subscription_pages_are_not_articles(self):
        self.assertEqual(analyse_page('<title>Just a moment...</title><script src="/cf-chl"></script>', 200)["classification"], "blocked_challenge")
        self.assertEqual(analyse_page('<title>Subscribe to read</title><p>Choose a plan</p>', 200)["classification"], "paywall_requires_access")
        self.assertEqual(analyse_page('<title>Article</title><nav>Links</nav>', 200)["classification"], "no_article_element")
        self.assertEqual(analyse_page('<title>Article</title><article><p>Text</p></article>', 403)["classification"], "http_403")

    def test_paid_partial_stays_explicit_and_menu_is_excluded(self):
        source = '<title>Story</title><nav>Subscribe now</nav><script type="application/ld+json">{"isAccessibleForFree":false}</script><article><p>Actual introductory text</p></article>'
        result = analyse_page(source, 200)
        self.assertEqual(result["classification"], "partial_or_paid_unverified")
        self.assertNotIn("Subscribe now", result["article_html"])


class MailTests(unittest.TestCase):
    def setup_state(self, td):
        a = article()
        save(Path(td) / "catalogue.json", {a["id"]: a})
        save(Path(td) / "ledger.json", {"days": {}})
        save(Path(td) / "health.json", {"runs": []})
        b = digest.plan(config(), {a["id"]: a}, {"days": {}}, AT)
        folder = Path(td) / "bundle"
        digest.write_bundle(folder, b)
        return folder, b

    def test_mime_and_modified_url_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            folder, b = self.setup_state(td)
            msg = mail.build_message(b, folder, "sender@example.com", "reader@example.com")
            self.assertEqual(len(list(msg.iter_attachments())), 3)
            self.assertIn("text/html", msg.as_string())
            b["articles"][0]["url"] = "https://evil.test"
            with self.assertRaises(ValueError):
                mail.validate_bundle(b, load(Path(td) / "catalogue.json"))

    def test_pending_saved_before_smtp_data_and_no_automatic_retry(self):
        with tempfile.TemporaryDirectory() as td:
            folder, _ = self.setup_state(td)
            sent = []
            class Client:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def send_message(self, msg):
                    self_status = load(Path(td) / "ledger.json")["days"]["2026-10-15"]["status"]
                    self_assert(self_status, "pending")
                    sent.append(msg)
                    raise ConnectionError("lost after DATA")
            self_assert = self.assertEqual
            env = {"MAIL_FROM": "sender@example.com", "MAIL_TO": "reader@example.com"}
            args = ["newsflow.mail", "send", "--state", td, "--bundle", str(folder)]
            with patch.dict(os.environ, env), patch("sys.argv", args), patch.object(mail, "connect", return_value=Client()), patch.object(mail, "push"):
                with self.assertRaises(ConnectionError):
                    mail.main()
                mail.main()
            self.assertEqual(len(sent), 1)
            self.assertEqual(load(Path(td) / "ledger.json")["days"]["2026-10-15"]["status"], "pending")

    def test_no_mail_when_pending_commit_fails(self):
        with tempfile.TemporaryDirectory() as td:
            folder, _ = self.setup_state(td)
            client = unittest.mock.MagicMock()
            with patch.dict(os.environ, {"MAIL_FROM": "sender@example.com", "MAIL_TO": "reader@example.com"}), patch("sys.argv", ["mail", "send", "--state", td, "--bundle", str(folder)]), patch.object(mail, "connect", return_value=client), patch.object(mail, "push", side_effect=RuntimeError("git")):
                with self.assertRaises(RuntimeError):
                    mail.main()
                client.__enter__.return_value.send_message.assert_not_called()

    def test_reject_header_injection(self):
        with patch.dict(os.environ, {"MAIL_TO": "reader@example.com\r\nBcc: other@example.com"}):
            with self.assertRaises(ValueError):
                mail.address("MAIL_TO")


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL needed for encryption test")
class EncryptionTests(unittest.TestCase):
    def test_roundtrip_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"DIGEST_KEY": "ab" * 32}):
            td = Path(td)
            source, sealed, out = td / "bundle.json", td / "sealed", td / "out"
            save(source, {"text": "私人的测试文本"})
            seal.pack(source, sealed)
            self.assertNotIn("私人的".encode(), (sealed / "digest.enc").read_bytes())
            seal.unpack(sealed, out)
            self.assertEqual(load(source), load(out / "bundle.json"))
            data = bytearray((sealed / "digest.enc").read_bytes())
            data[-1] ^= 1
            (sealed / "digest.enc").write_bytes(data)
            with self.assertRaises(ValueError):
                seal.unpack(sealed, out)


class StateTests(unittest.TestCase):
    def test_git_roundtrip_uses_only_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            remote, state = td / "remote.git", td / "state"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            state.mkdir()
            for args in [["init"], ["checkout", "--orphan", state_io.BRANCH], ["remote", "add", "origin", str(remote)], ["config", "user.name", "test"], ["config", "user.email", "test@example.com"]]:
                state_io.git(args, state)
            for name in state_io.FILES:
                save(state / name, {})
            (state / "body.txt").write_text("must stay private")
            original = state_io.git
            def local_git(args, cwd, check=True, authenticated=False):
                return original(args, cwd, check=check, authenticated=False)
            with patch.object(state_io, "git", local_git):
                state_io.push(state)
            tracked = original(["ls-tree", "--name-only", "HEAD"], state).stdout.splitlines()
            self.assertEqual(set(tracked), set(state_io.FILES))


if __name__ == "__main__":
    unittest.main()
