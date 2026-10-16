"""Authenticated encryption for temporary Actions artifacts, via runner OpenSSL.

AES-256-CBC/PBKDF2 encryption plus HMAC-SHA256 encrypt-then-MAC. The shared
secret must be 32 random bytes encoded as 64 hex characters. SMTP is uninvolved.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
from pathlib import Path

from .common import load, save


def key():
    secret = os.environ.get("DIGEST_KEY", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
        raise ValueError("DIGEST_KEY must be 64 random hex characters; see README")
    return secret, hashlib.sha256(b"news-mailer-mac-v1\0" + bytes.fromhex(secret)).digest()


def openssl(data, secret, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:DIGEST_KEY"]
    if decrypt:
        args.append("-d")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SystemRoot", "SYSTEMROOT"}}
    env["DIGEST_KEY"] = secret
    r = subprocess.run(args, input=data, capture_output=True, env=env)
    if r.returncode:
        raise ValueError("OpenSSL encryption/decryption failed")
    return r.stdout


def pack(bundle_path, out):
    secret, mac_key = key()
    data = Path(bundle_path).read_bytes()
    encrypted = openssl(data, secret)
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "digest.enc").write_bytes(encrypted)
    save(folder / "envelope.json", {"version": 1, "hmac_sha256": hmac.new(mac_key, encrypted, "sha256").hexdigest()})


def unpack(folder, out):
    secret, mac_key = key()
    folder = Path(folder)
    if (folder / "digest.enc").stat().st_size > 15_000_000:
        raise ValueError("encrypted payload too large")
    encrypted = (folder / "digest.enc").read_bytes()
    envelope = load(folder / "envelope.json")
    expected = hmac.new(mac_key, encrypted, "sha256").hexdigest()
    if envelope.get("version") != 1 or not hmac.compare_digest(expected, envelope.get("hmac_sha256", "")):
        raise ValueError("artifact authentication failed; refusing to decrypt")
    result = json.loads(openssl(encrypted, secret, decrypt=True))
    save(Path(out) / "bundle.json", result)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["pack", "unpack"])
    p.add_argument("--bundle", default=".runtime/bundle")
    p.add_argument("--sealed", default=".runtime/sealed")
    a = p.parse_args()
    if a.action == "pack":
        pack(Path(a.bundle) / "bundle.json", a.sealed)
    else:
        unpack(a.sealed, a.bundle)
