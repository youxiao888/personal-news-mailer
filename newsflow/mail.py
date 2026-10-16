"""SMTP-only runner. Stdlib only; never imports or runs bpc-fetch."""
import argparse
import hashlib
import json
import os
import re
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from pathlib import Path

from .common import canonical, iso, load, save
from .digest import render, write_bundle
from .state_io import push


def address(key):
    value = os.environ.get(key, "").strip()
    if not re.fullmatch(r"[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+", value):
        raise ValueError(key + " must contain one email address")
    return value


def connect():
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    mode = os.environ.get("SMTP_SECURITY", "starttls")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or mode not in {"ssl", "starttls"}:
        raise ValueError("invalid SMTP host/security; plaintext SMTP is forbidden")
    user, password = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    if not user or not password:
        raise ValueError("SMTP_USER and SMTP_PASSWORD are required")
    context = ssl.create_default_context()
    client = smtplib.SMTP_SSL(host, port, timeout=60, context=context) if mode == "ssl" else smtplib.SMTP(host, port, timeout=60)
    try:
        client.ehlo()
        if mode == "starttls":
            client.starttls(context=context)
            client.ehlo()
        client.login(user, password)
        return client
    except Exception:
        client.close()
        raise


def validate_bundle(bundle, catalogue):
    if bundle.get("schema") != 1 or date.fromisoformat(bundle["target"]).isoformat() != bundle["target"]:
        raise ValueError("invalid bundle schema/date")
    seen = set()
    for a in bundle["articles"]:
        aid = a["id"]
        original = catalogue.get(aid)
        if aid in seen or not original or a["url"] != original["url"]:
            raise ValueError("bundle article does not match committed catalogue")
        domain = a["url"].split("/")[2].removeprefix("www.")
        if canonical(a["url"], domain) != a["url"]:
            raise ValueError("invalid article URL")
        seen.add(aid)
    if any(k not in seen or not isinstance(v, str) for k, v in bundle.get("bodies", {}).items()):
        raise ValueError("invalid bodies")


def build_message(bundle, folder, sender, recipient):
    # Regenerate HTML from escaped text; never execute or send arbitrary artifact HTML.
    write_bundle(folder, bundle)
    title, _, _ = render(bundle)
    directory = {**bundle, "bodies": {}}
    _, page, text = render(directory)
    msg = EmailMessage(policy=SMTP)
    msg["Subject"], msg["From"], msg["To"] = title, sender, recipient
    msg["Date"] = formatdate(localtime=False)
    fingerprint = hashlib.sha256(json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    msg["Message-ID"] = f"<news-{bundle['target']}-{fingerprint[:20]}@{sender.split('@')[1]}>"
    msg.set_content(text + "\n\n提取文本见 reading.html 附件；可另存后在浏览器阅读。")
    # Large directories stay complete in attachments, while mobile mail bodies remain small.
    if len(page.encode()) > 85_000:
        msg.set_content(title + "\n完整目录与提取文本请打开 reading.html 或 catalogue.csv 附件。")
        msg.add_alternative("<p>完整目录与提取文本请打开 reading.html 或 catalogue.csv 附件。</p>", subtype="html")
    else:
        msg.add_alternative(page, subtype="html")
    for name, subtype in [("reading.html", "html"), ("catalogue.csv", "csv"), ("health.json", "json")]:
        msg.add_attachment((Path(folder) / name).read_bytes(), maintype="text" if subtype != "json" else "application",
                           subtype=subtype, filename=name)
    # Body text comes only from an artifact. The serialized envelope has a hard size cap.
    if len(msg.as_bytes()) > 9_000_000:
        raise ValueError("message too large; reduce BPC text budget, preview again; no entries dropped")
    return msg


def mark_sent(state, ledger, target, entry):
    catalogue = load(state / "catalogue.json", {})
    for aid in entry["article_ids"]:
        if aid in catalogue:
            catalogue[aid]["mailed_at"] = iso()
    entry["status"], entry["sent_at"] = "sent", iso()
    ledger["days"][target] = entry
    save(state / "catalogue.json", catalogue)
    save(state / "ledger.json", ledger)


def resolve(state, target, action):
    date.fromisoformat(target)
    ledger = load(state / "ledger.json", {"days": {}})
    entry = ledger["days"].get(target)
    if not entry or entry["status"] != "pending":
        raise ValueError("only pending deliveries can be resolved")
    if action == "mark-sent":
        mark_sent(state, ledger, target, entry)
    else:
        entry["status"] = "retry_authorized"
        entry["resolved_at"] = iso()
        save(state / "ledger.json", ledger)
    push(state)
    print("pending delivery resolved: " + action)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["send", "test", "preview", "resolve"])
    p.add_argument("--state", default=".runtime/state")
    p.add_argument("--bundle", default=".runtime/bundle")
    a = p.parse_args()
    state, folder = Path(a.state), Path(a.bundle)
    if a.action == "resolve":
        action = os.environ.get("RESOLVE_ACTION")
        if action not in {"mark-sent", "allow-retry"}:
            raise ValueError("invalid resolution")
        return resolve(state, os.environ.get("RESOLVE_DATE", ""), action)
    if a.action == "test":
        sender, recipient = address("MAIL_FROM"), address("MAIL_TO")
        msg = EmailMessage(policy=SMTP)
        msg["From"], msg["To"], msg["Subject"] = sender, recipient, "四刊工作流 SMTP 测试"
        msg.set_content("这封邮件验证 SMTP 配置。未读取、发送文章，也未修改日报发送记录。")
        with connect() as client:
            client.send_message(msg)
        print("test accepted by SMTP; check inbox/spam")
        return
    bundle = load(folder / "bundle.json")
    catalogue = load(state / "catalogue.json", {})
    validate_bundle(bundle, catalogue)
    sender = "preview@example.invalid" if a.action == "preview" else address("MAIL_FROM")
    recipient = "reader@example.invalid" if a.action == "preview" else address("MAIL_TO")
    msg = build_message(bundle, folder, sender, recipient)
    if a.action == "preview":
        (folder / "preview.eml").write_bytes(msg.as_bytes())
        print("preview generated; no email sent")
        return
    ledger = load(state / "ledger.json", {"days": {}})
    target = bundle["target"]
    if ledger["days"].get(target, {}).get("status") in {"pending", "sent"}:
        print("delivery already sent or pending; skipped")
        return
    # Authorised retries of old pending dates must occur before a later daily digest.
    if any(x["status"] == "pending" for x in ledger["days"].values()):
        raise ValueError("older delivery pending; inspect SMTP provider log and resolve it")
    with connect() as client:
        entry = {"status": "pending", "prepared_at": iso(), "message_id": msg["Message-ID"],
                 "article_ids": [x["id"] for x in bundle["articles"]]}
        ledger["days"][target] = entry
        save(state / "ledger.json", ledger)
        push(state)  # Durable intent BEFORE SMTP DATA. If this fails, no mail is sent.
        try:
            refused = client.send_message(msg)
            if refused:
                raise RuntimeError("SMTP recipient rejected")
        except Exception:
            print("delivery uncertain; pending retained; check provider log before allowing retry")
            raise
        mark_sent(state, ledger, target, entry)
        push(state)
    print("daily digest accepted by SMTP; this does not confirm inbox delivery")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SMTP/server errors may echo credentials, addresses or message data.
        print("Mail operation failed (" + type(exc).__name__ + "). Check configuration and news-state ledger; private error details suppressed.")
        raise SystemExit(1)
