"""Run the verified CLI, distrust success flags, keep bodies out of durable state."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .common import date_value, iso, load, now, save
from .digest import write_bundle

WALL = re.compile(r"subscribe to (?:continue|read)|sign in to (?:continue|read)|verify you are human|access denied|just a moment|enable javascript|请验证您是真人|正在进行安全验证|security verification|choose (?:your|a) subscription", re.I)


def run_cli(command, timeout):
    # A process group lets Linux runners terminate Chromium descendants on timeout.
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", **kwargs)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.communicate()
        return {"ok": False, "error": "timeout"}
    try:
        result = json.loads(out)
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_json"}
    except ValueError:
        return {"ok": False, "error": "invalid_json"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--state", default=".runtime/state")
    p.add_argument("--bundle", default=".runtime/bundle")
    p.add_argument("--sites-js", required=True)
    a = p.parse_args()
    cfg, state, bundle_dir = load(a.config)["bpc"], Path(a.state), Path(a.bundle)
    bundle = load(bundle_dir / "bundle.json")
    catalogue = load(state / "catalogue.json", {})
    started, attempts = time.monotonic(), 0
    # Fair interleaving across the four publications, oldest unseen first.
    groups = {}
    for x in bundle["articles"]:
        groups.setdefault(x["site"], []).append(x)
    ordered = [g[i] for i in range(max((len(g) for g in groups.values()), default=0))
               for _, g in sorted(groups.items()) if i < len(g)]
    for x in ordered:
        record = catalogue[x["id"]]
        if record.get("attempts", 0) >= cfg["max_attempts_per_article"]:
            continue
        if time.monotonic() - started >= cfg["total_budget_seconds"] or attempts >= cfg["max_articles_per_digest"]:
            break
        remaining = cfg["total_budget_seconds"] - (time.monotonic() - started)
        dest = Path(".runtime/bpc-output") / x["id"]
        article_budget = min(remaining, cfg["per_article_timeout_seconds"])
        article_deadline = time.monotonic() + article_budget
        compatible = run_cli([sys.executable, "-m", "newsflow.compat", x["url"], "--out-dir", str(dest),
                              "--timeout", str(max(1, min(15, article_budget - 2)))], min(18, article_budget))
        result = compatible
        if not compatible.get("ok") and article_deadline - time.monotonic() > 1:
            result = run_cli(["bpc-fetch", "fetch", x["url"], "--out-dir", str(dest), "--no-images",
                              "--sites-js", a.sites_js, "--compact"], article_deadline - time.monotonic())
        record["access_classification"] = result.get("access_classification", "upstream_result_unverified") if result.get("ok") else compatible.get("access_classification", compatible.get("error", "unknown"))
        record["transport"] = result.get("transport", "bpc-fetch-cli")
        attempts += 1
        record["attempts"] = record.get("attempts", 0) + 1
        record["last_attempt"] = iso()
        record["fetch_status"] = "failed"
        if result.get("ok") and result.get("path"):
            target = Path(result["path"]).resolve()
            if target.is_relative_to(dest.resolve()) and target.is_file() and target.stat().st_size <= 1_000_000:
                text = target.read_text(encoding="utf-8")
                text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
                text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text).strip()
                record["fetch_status"] = result.get("access_classification", "candidate_text_unverified")
                if len(text) < cfg["min_text_chars"] or WALL.search(text):
                    record["fetch_status"] = "short_or_wall_unverified"
                record["text_chars"] = len(text)
                record["text_sha256"] = __import__("hashlib").sha256(text.encode()).hexdigest()
                # Escaped at render time. A cap is visible, never called full text.
                cap = cfg["max_text_chars_per_article"]
                bundle["bodies"][x["id"]] = text[:cap] + ("\n[本地长度上限截断]" if len(text) > cap else "")
        x.update({k: record[k] for k in ["attempts", "last_attempt", "fetch_status", "text_chars", "text_sha256", "access_classification", "transport"] if k in record})
    bundle["bpc_note"] = f"本次尝试 {attempts} 篇；受时间、篇数和重试上限限制。其余仅提供目录链接。候选文本不代表已取得完整正文。"
    save(state / "catalogue.json", catalogue)
    write_bundle(bundle_dir, bundle)
    print(bundle["bpc_note"])


if __name__ == "__main__":
    main()
