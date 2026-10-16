import hashlib
import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTC = timezone.utc


def now():
    return datetime.now(UTC)


def iso(dt=None):
    return (dt or now()).isoformat()


def load(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def date_value(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def plain(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value or "")).split())[:1000]


def canonical(url, domain):
    try:
        p = urlsplit(html.unescape(url.strip()))
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or p.username or p.password or p.port not in (None, 80, 443):
            return None
        if host not in {domain, "www." + domain, "m." + domain}:
            return None
        path = re.sub(r"/+", "/", p.path).rstrip("/")
        if path.endswith("/amp"):
            path = path[:-4]
        if domain == "ft.com":
            match = re.fullmatch(r"/content/([0-9a-fA-F-]{36})", path)
            if not match:
                return None
            path = "/content/" + match[1].lower()
        elif domain == "nytimes.com":
            if not re.match(r"/(?:interactive/|live/)?\d{4}/\d{2}/\d{2}/.+", path):
                return None
        elif domain == "economist.com":
            if not re.search(r"/\d{4}/\d{2}/\d{2}/[^/]+$", path):
                return None
        elif domain == "wsj.com":
            if not (path.startswith("/articles/") or re.search(r"-[0-9a-f]{8,}$", path)):
                return None
        else:
            return None
        # These four publishers identify stories by path. Query strings contain
        # syndication/tracking/gift codes, not catalogue identity; do not persist them.
        return urlunsplit(("https", "www." + domain, path, "", ""))
    except ValueError:
        return None


def article_id(url):
    # FT and WSJ IDs also merge alternative section paths to the same story.
    p = urlsplit(url)
    if p.hostname == "www.wsj.com":
        m = re.search(r"-([0-9a-f]{8,})$", p.path)
        if m:
            return hashlib.sha256(("wsj:" + m[1]).encode()).hexdigest()[:24]
    return hashlib.sha256(url.encode()).hexdigest()[:24]
