"""Small helpers shared by fetch_comments.py and post_replies.py.

The Figma token comes from the FIGMA_TOKEN environment variable, or a FIGMA_TOKEN=
line in a .env file in the current folder. It is never printed or written anywhere.
Create one in Figma: Settings > Security > Personal access tokens, with the
file_comments:write and file_content:read scopes.
"""
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

API = "https://api.figma.com/v1"


def token():
    tok = os.environ.get("FIGMA_TOKEN", "").strip()
    env = pathlib.Path.cwd() / ".env"
    if not tok and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("FIGMA_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not tok:
        sys.exit("No Figma token. Set FIGMA_TOKEN, or put FIGMA_TOKEN=... in a .env file in this folder "
                 "(see .env.example). Make one in Figma: Settings > Security > Personal access tokens.")
    return tok


def file_key(url_or_key):
    m = re.search(r"figma\.com/(?:slides|design|file|deck|board)/([0-9a-zA-Z]{22,128})", url_or_key)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9a-zA-Z]{22,128}", url_or_key):
        return url_or_key
    sys.exit(f"Couldn't find a Figma file key in: {url_or_key}")


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Figma-Token": token(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}   # a DELETE may answer with no body
    except urllib.error.HTTPError as e:
        sys.exit(f"Figma API {method} {path} failed: {e.code} {e.read().decode()[:300]}")
