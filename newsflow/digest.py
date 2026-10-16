import argparse
import csv
import html
import io
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import date_value, iso, load, now, save


def plan(cfg, catalogue, ledger, at, force=False):
    zone = ZoneInfo(cfg["timezone"])
    local = at.astimezone(zone)
    target = local.date() - timedelta(days=1)
    status = ledger["days"].get(str(target), {}).get("status")
    if not force and (local.hour < cfg["send_after_local_hour"] or status in {"sent", "pending"}):
        return None
    cutoff = datetime.combine(target + timedelta(days=1), time.min, zone)
    # Include late discoveries and unsent catch-up entries; never cap the directory silently.
    articles = []
    for x in catalogue.values():
        retry = (cfg["bpc"]["enabled"] and x.get("fetch_status") in {"failed", "short_or_wall_unverified"}
                 and 0 < x.get("attempts", 0) < cfg["bpc"]["max_attempts_per_article"]
                 and at - date_value(x["last_attempt"]) >= timedelta(hours=18))
        publication = date_value(x.get("published"))
        before_cutoff = date_value(x["first_seen"]) < cutoff or (publication and publication < cutoff)
        if (not x.get("mailed_at") or retry) and before_cutoff:
            articles.append({**x, "delivery_kind": "正文重试补发" if x.get("mailed_at") else "首次发送/回补"})
    articles.sort(key=lambda x: (x["site"], x.get("published", x["first_seen"]), x["id"]))
    return {"schema": 1, "target": str(target), "timezone": cfg["timezone"], "created_at": iso(at),
            "articles": articles, "bodies": {}, "notes": [
                "目录按首次发现时间截日，含此前未发送条目与回补；不等同于当日完整出版目录。",
                "出版日期未知会明确标注；站点地图 lastmod 只表示修改时间。",
                "正文为工具提取结果，可能只有片段，未经全文完整性核实。"
            ]}


def render(bundle):
    e = html.escape
    articles, health = bundle["articles"], bundle.get("health", {})
    title = f"四刊目录 · {bundle['target']} · {len(articles)} 条"
    notes = list(bundle["notes"])
    if bundle.get("bpc_note"):
        notes.append(bundle["bpc_note"])
    stats = health.get("sites", {})
    notes.append("本次采集：" + "; ".join(f"{k}: 新增 {v['new_unique']} / 正常来源 {v['ok_sources']}" for k, v in sorted(stats.items())))
    if health.get("gap_hours", 0) > 2:
        notes.append(f"距上次采集 {health['gap_hours']} 小时，可能存在缺口。")
    errors = [x for x in health.get("sources", []) if x["status"] not in {"ok", "bounded", "unavailable_4xx"}]
    if errors:
        notes.append(f"本次有 {len(errors)} 个来源或 robots 检查失败；详情见附件 health.json。")
    body = [f"<h1>{e(title)}</h1>", "<ul>" + "".join(f"<li>{e(x)}</li>" for x in notes) + "</ul>"]
    texts = [title, *notes, ""]
    last = None
    for a in articles:
        if a["site"] != last:
            last = a["site"]
            body.append(f"<h2>{e(last)}</h2>")
            texts.append("\n" + last)
        heading = a.get("title") or "标题未获取（仅来源链接）"
        pub = a.get("published", "未知") + " · " + a.get("date_basis", "unknown")
        status = a.get("fetch_status", "not_attempted") + " · " + a.get("delivery_kind", "")
        if a.get("access_classification"):
            status += " · " + a["access_classification"]
        body.append(f'<article><h3><a href="{e(a["url"], quote=True)}">{e(heading)}</a></h3><p class="meta">出版日期: {e(pub)}<br>首次发现: {e(a["first_seen"])}<br>正文状态: {e(status)}</p>')
        texts.extend([heading, a["url"], "出版日期: " + pub, "正文状态: " + status])
        content = bundle.get("bodies", {}).get(a["id"])
        if content:
            body.append('<p class="meta">提取文本（完整性未核实）</p><div class="text">' + e(content) + "</div>")
            texts.append(content)
        body.append("</article>")
    css = "body{font:18px/1.7 system-ui,sans-serif;margin:auto;padding:20px;max-width:850px;color:#202020}a{color:#075a9e;overflow-wrap:anywhere}h1{font-size:26px}h2{margin-top:36px}h3{font-size:21px}.meta{color:#666;font-size:13px}article{border-bottom:1px solid #ddd;padding:12px 0}.text{white-space:pre-wrap;overflow-wrap:anywhere}"
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + e(title) + "</title><style>" + css + "</style></head><body>" + "".join(body) + "</body></html>"
    return title, page, "\n".join(texts)


def write_bundle(path, bundle):
    path = Path(path)
    save(path / "bundle.json", bundle)
    _, page, text = render(bundle)
    (path / "reading.html").write_text(page, encoding="utf-8")
    (path / "reading.txt").write_text(text, encoding="utf-8")
    f = io.StringIO(newline="")
    w = csv.writer(f)
    w.writerow(["site", "title", "url", "published", "date_basis", "first_seen", "fetch_status", "sources"])
    for a in bundle["articles"]:
        values = [a.get(k, "") for k in ["site", "title", "url", "published", "date_basis", "first_seen", "fetch_status"]]
        values.append(" | ".join(a["sources"]))
        # Avoid spreadsheet formula execution if a publisher title starts with '=' etc.
        w.writerow(["'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    (path / "catalogue.csv").write_text(f.getvalue(), encoding="utf-8-sig")
    save(path / "health.json", bundle.get("health", {}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--state", default=".runtime/state")
    p.add_argument("--out", default=".runtime/bundle")
    p.add_argument("--force", action="store_true", help="preview only; sender still enforces ledger")
    a = p.parse_args()
    cfg, state = load(a.config), Path(a.state)
    b = plan(cfg, load(state / "catalogue.json", {}), load(state / "ledger.json", {"days": {}}), now(), a.force)
    if b:
        health = load(state / "health.json", {"runs": []})
        b["health"] = health["runs"][-1] if health["runs"] else {}
        write_bundle(a.out, b)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("due=" + ("true" if b else "false") + "\n")
    print("digest ready" if b else "not due or already sent/pending")


if __name__ == "__main__":
    main()
