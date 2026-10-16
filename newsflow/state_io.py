"""Persist metadata on an orphan branch. Never store SMTP values or article bodies."""
import argparse
import base64
import os
import re
import subprocess
from pathlib import Path

FILES = ("catalogue.json", "ledger.json", "health.json")
BRANCH = "news-state"


def git(args, cwd, check=True, authenticated=False):
    env = os.environ.copy()
    # Never pass SMTP credentials to git; never place the GitHub token in a URL.
    for key in list(env):
        if key.startswith("SMTP_") or key in {"MAIL_FROM", "MAIL_TO"}:
            env.pop(key)
    if authenticated:
        token = env.pop("GH_TOKEN", "")
        if not token:
            raise RuntimeError("GH_TOKEN is required for state transfer")
        header = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + header)
    else:
        env.pop("GH_TOKEN", None)
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode:
        # stderr could contain network/account details; keep it out of public logs.
        raise RuntimeError("state git operation failed: " + args[0])
    return r


def pull(path):
    path = Path(path)
    if (path / ".git").exists():
        raise RuntimeError("state directory already initialized; use a fresh runtime directory")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("invalid GITHUB_REPOSITORY")
    path.mkdir(parents=True, exist_ok=True)
    git(["init"], path)
    git(["remote", "add", "origin", "https://github.com/" + repo + ".git"], path)
    r = git(["ls-remote", "--exit-code", "--heads", "origin", BRANCH], path, check=False, authenticated=True)
    if r.returncode == 2:
        git(["checkout", "--orphan", BRANCH], path)
    elif r.returncode == 0:
        git(["fetch", "--depth=1", "origin", BRANCH], path, authenticated=True)
        git(["checkout", "-b", BRANCH, "FETCH_HEAD"], path)
        if any(not (path / f).exists() for f in FILES):
            raise RuntimeError("state branch is incomplete; restore it from backup")
    else:
        raise RuntimeError("cannot read remote state; refusing to reset")
    git(["config", "user.name", "news-mailer[bot]"], path)
    git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], path)


def push(path):
    path = Path(path)
    if any(not (path / f).exists() for f in FILES):
        raise RuntimeError("refusing to commit incomplete state")
    git(["add", "--", *FILES], path)
    if git(["diff", "--cached", "--quiet"], path, check=False).returncode == 0:
        return
    git(["commit", "-m", "Update catalogue and delivery ledger [skip ci]"], path)
    # No force push; conflicting writers fail visibly instead of losing state.
    git(["push", "origin", "HEAD:refs/heads/" + BRANCH], path, authenticated=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["pull", "push"])
    p.add_argument("--state", default=".runtime/state")
    a = p.parse_args()
    {"pull": pull, "push": push}[a.action](a.state)
