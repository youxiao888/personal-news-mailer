"""Browser-compatible HTTPS transport, with explicit challenge/paywall checks.

Uses bpc-fetch's article extractor on actual article elements. HTTP 200 alone is
never evidence of an article, and subscription offers are not saved as prose.
"""
import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .common import canonical

CHALLENGE = re.compile(r"cf-chl|captcha-delivery|challenge-platform|verify you are human|请验证您是真人", re.I)
WALL_TITLE = re.compile(r"^(?:subscribe to read|subscribe|subscription|sign in|log in|access denied|security verification|just a moment|请稍候)", re.I)


def analyse_page(source, status):
    title_match = re.search(r"<title[^>]*>(.*?)</title>", source, re.I | re.S)
    title = html.unescape(re.sub(r"<[^>]+>", "", title_match[1])).strip() if title_match else ""
    articles = re.findall(r"<article\b[^>]*>.*?</article\s*>", source, re.I | re.S)
    article_html = max(articles, key=len, default="")
    challenge = bool(CHALLENGE.search(source))
    if (challenge and (status != 200 or not article_html)) or re.match(r"^(security verification|just a moment|请稍候)", title, re.I):
        kind = "blocked_challenge"
    elif status != 200:
        kind = "http_" + str(status)
    elif WALL_TITLE.search(title):
        kind = "paywall_requires_access"
    elif not article_html:
        kind = "no_article_element"
    elif re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', source, re.I):
        kind = "partial_or_paid_unverified"
    else:
        kind = "candidate_text_unverified"
    return {"classification": kind, "title": title, "article_html": article_html}


def fetch(url, dest, timeout=15):
    # Imported only on the extraction runner, not on the SMTP runner / offline CI.
    import curl_cffi
    from bpc_fetch.extract import extract_article, article_to_markdown

    domain = (urlsplit(url).hostname or "").removeprefix("www.")
    normalized = canonical(url, domain)
    if not normalized:
        return {"ok": False, "error": "invalid_article_url"}
    # Native browser headers work for WSJ; overriding them with the old upstream
    # Chrome UA reproduced 401. FT uses only the upstream Google referer strategy.
    headers = {"Referer": "https://www.google.com/"} if domain == "ft.com" else None
    try:
        response = curl_cffi.get(normalized, impersonate="chrome", headers=headers, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        return {"ok": False, "error": "transport_" + type(exc).__name__, "transport": "curl_cffi"}
    if len(response.content) > 8_000_000:
        return {"ok": False, "error": "response_too_large", "transport": "curl_cffi"}
    info = analyse_page(response.text, response.status_code)
    result = {"ok": False, "url": normalized, "http_status": response.status_code,
              "access_classification": info["classification"], "transport": "curl_cffi+bpc_extract"}
    if info["classification"] not in {"candidate_text_unverified", "partial_or_paid_unverified"}:
        result["error"] = info["classification"]
        return result
    # Restrict extraction to the article; menus and pricing cards stay out.
    source = "<html><head><title>" + html.escape(info["title"]) + "</title></head><body>" + info["article_html"] + "</body></html>"
    article = extract_article(source, normalized)
    if len(article["text"].strip()) < 200:
        result["error"] = "article_text_too_short"
        return result
    article["images"] = []
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "compatible.md"
    path.write_text(article_to_markdown(article), encoding="utf-8")
    result.update(ok=True, path=str(path), title=article["title"], text_length=len(article["text"]))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--timeout", type=float, default=15)
    args = p.parse_args()
    print(json.dumps(fetch(args.url, args.out_dir, args.timeout), ensure_ascii=False))
