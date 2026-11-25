"""Serve a Blueline review folder and pass the owner's decisions to Claude, one card at a time.

Usage:
  python3 serve.py <review-dir> [--port 4400]

The review folder holds index.html, data.json and img/. Every decision the owner
makes on the page is recorded the moment they make it, and Claude works through
them while the owner keeps reviewing:

  POST /api/act        the page: {"id": 2, "act": "approve", ...}. Recorded in acts.json
                       with a running number "n", so a newer decision on a card replaces
                       an older one and Claude can tell which one it has handled.
  GET  /api/status     the page polls this: {"phase", "acts", "progress"}
  POST /api/reset      clears the round (acts, progress, decisions...). The running number
                       keeps counting up, so nothing Claude writes late matches a new act.

Acts the page sends:
  approve  {"edit": [new wording per line], "pick": "B", "reply": "text or empty"}
  back     {"note": "what's off"}           Not quite: Claude redoes the card
  undo     {}                               take a card back: an applied change comes out of the deck,
                                            and a reply it posted comes out of the thread. The server
                                            copies that reply ("reply") over from the approval it undoes.
  skip, reopen, close {}                    no work for Claude
  later    {"text": "..."}                  one more reply into a finished card's thread
  unlater  {"of": 12}                       take that later reply (its "n") back out of the thread
  draft    {"for": "reply" | "later"}       ask Claude to write a reply

Claude never posts to this server. It reads acts.json (scripts/next.py waits for new
work) and writes progress.json (scripts/progress.py), which this server hands to the page.
acts.json is written only by this server and progress.json only by Claude, so neither
can overwrite the other.

POST /api/decisions and /api/replies also take a whole round's decisions in one go, and
Claude answers those in status.json.

It listens on 127.0.0.1 only, so nothing outside this computer can reach it. It also
turns away requests from other websites open in the same browser: every request must
name this server as its Host, and a POST must be JSON from this page's own origin.
"""
import argparse
import json
import pathlib
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY = 2_000_000  # decisions are small; refuse anything bigger
MAX_TEXT = 20_000     # one reply or one line of wording
ACTS = {"approve", "back", "undo", "skip", "reopen", "close", "later", "unlater", "draft"}
ROUND_FILES = ("acts.json", "progress.json", "decisions.json", "replies.json", "status.json")


class Broken(Exception):
    """A round file that exists but isn't a JSON object. Writes here are atomic, so it was damaged by hand."""


def read_json(path, empty):
    """For files only the page reads (status.json): anything unreadable counts as nothing yet."""
    try:
        return json.loads(path.read_text()) if path.exists() else empty
    except ValueError:
        return empty


def read_round(path):
    """acts.json or progress.json: {} if it isn't there yet, Broken if it can't be read. Never read a broken one
    as empty: the running number would start again at 1 and Claude's progress would hide every new decision."""
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text())
    except ValueError as e:
        raise Broken(f"{path.name} can't be read ({e}). Fix it or restore it; nothing more is recorded until then.")
    if not isinstance(obj, dict):
        raise Broken(f"{path.name} should hold a JSON object. Fix it or restore it; nothing more is recorded until then.")
    return obj


def too_long(what, v):
    """Why a text field is refused, in words the owner can act on, or None."""
    if not isinstance(v, str):
        return f"{what} must be text"
    if len(v) > MAX_TEXT:
        return f"{what} is {len(v):,} characters; the most is {MAX_TEXT:,}. Shorten it and try again"
    return None


def clean_act(body, cards=None):
    """The act as it will be stored, or an error message. cards: the card ids in data.json, if known."""
    act = body.get("act")
    if act not in ACTS:
        return None, f"act must be one of {sorted(ACTS)}"
    cid = body.get("id")
    if not isinstance(cid, int) or isinstance(cid, bool):
        return None, "id must be a card number"
    if cards is not None and cid not in cards:
        return None, f"there's no card {cid} in data.json"
    rec = {"act": act}
    text = lambda v: isinstance(v, str) and len(v) <= MAX_TEXT
    if act == "approve":
        edit = body.get("edit", [])
        if not isinstance(edit, list):
            return None, "edit must be a list of strings"
        for j, e in enumerate(edit):
            if too_long(f"Line {j + 1} of the new wording", e):
                return None, too_long(f"Line {j + 1} of the new wording", e)
        for what, key in (("The reply", "reply"), ("The pick", "pick")):
            if too_long(what, body.get(key, "")):
                return None, too_long(what, body.get(key, ""))
        rec.update(edit=edit, reply=body.get("reply", ""), pick=body.get("pick", ""))
    elif act == "back":
        if not text(body.get("note")) or not body["note"].strip():
            return None, "say what's off"
        rec["note"] = body["note"].strip()
    elif act == "later":
        if isinstance(body.get("text"), str) and len(body["text"]) > MAX_TEXT:
            return None, too_long("The reply", body["text"])
        if not text(body.get("text")) or not body["text"].strip():
            return None, "the reply is empty"
        rec["text"] = body["text"].strip()
    elif act == "unlater":
        if not isinstance(body.get("of"), int) or isinstance(body.get("of"), bool):
            return None, "unlater needs the later reply's number"
        rec["of"] = body["of"]
    elif act == "draft":
        if body.get("for") not in ("reply", "later"):
            return None, 'draft is "for": "reply" or "later"'
        rec["for"] = body["for"]
    return (cid, rec), None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=pathlib.Path)
    ap.add_argument("--port", type=int, default=4400)
    args = ap.parse_args()
    root = args.dir.resolve()
    if not (root / "index.html").exists() or not (root / "data.json").exists():
        sys.exit(f"{root} needs index.html and data.json")

    allowed_hosts = {f"localhost:{args.port}", f"127.0.0.1:{args.port}"}
    allowed_origins = {"http://" + h for h in allowed_hosts}
    lock = threading.Lock()  # the page can send two acts at once; they must not both read n

    def write(name, obj):
        tmp = root / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
        tmp.replace(root / name)  # atomic, so a watcher never reads half a file

    def card_ids():
        try:
            return {c.get("id") for c in json.loads((root / "data.json").read_text()).get("cards", [])}
        except (ValueError, OSError, AttributeError):
            return None   # mid-rewrite by Claude: don't turn decisions away over it

    def record(cid, rec):
        with lock:
            acts = read_round(root / "acts.json")   # Broken: refuse, rather than start the count again
            acts.setdefault("n", 0)
            acts.setdefault("cards", {})
            acts.setdefault("later", [])
            acts.setdefault("drafts", [])
            acts.setdefault("unlater", [])
            acts["n"] += 1
            rec = dict(rec, n=acts["n"], at=time.time())
            act = rec.pop("act")
            if act == "later":
                acts["later"].append(dict(rec, id=cid))
            elif act == "unlater":
                acts["unlater"].append(dict(rec, id=cid))
            elif act == "draft":
                acts["drafts"].append(dict(rec, id=cid))
            else:
                prev = acts["cards"].get(str(cid)) or {}
                if act == "undo":   # whatever the undone approval posted has to come out of the thread too
                    rec["reply"] = (prev.get("reply") or "").strip() if prev.get("act") == "approve" else prev.get("reply", "")
                acts["cards"][str(cid)] = dict(rec, act=act)
            write("acts.json", acts)
            return rec["n"]

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt, *a):  # keep the terminal quiet except for decisions
            if "/api/" in (self.path or "") and "/api/status" not in (self.path or ""):
                sys.stderr.write("blueline: " + fmt % a + "\n")

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            # A page on another site can't reach us by renaming itself to 127.0.0.1 (DNS rebinding)
            return (self.headers.get("Host") or "") in allowed_hosts

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {"error": "wrong host"})
            if self.path.split("?")[0] == "/api/status":
                st = read_json(root / "status.json", {"phase": "review"})
                if not isinstance(st, dict):
                    st = {"phase": "review"}
                st.setdefault("phase", "review")
                try:
                    st["acts"] = read_round(root / "acts.json")
                    st["progress"] = read_round(root / "progress.json")
                except Broken as e:   # the page says so instead of showing every card as undecided
                    return self.reply(500, {"error": str(e)})
                return self.reply(200, st)
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            origin = self.headers.get("Origin")
            if not self.host_ok() or (origin is not None and origin not in allowed_origins):
                return self.reply(403, {"error": "not from the review page"})
            # JSON only: a form or plain-text POST from another site can't get through
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self.reply(415, {"error": "send JSON"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self.reply(400, {"error": "bad length"})
            if n < 0:
                return self.reply(400, {"error": "bad length"})
            if n > MAX_BODY:
                return self.reply(413, {"error": "too big"})
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self.reply(400, {"error": "not JSON"})
            if not isinstance(body, dict):
                return self.reply(400, {"error": "expected a JSON object"})
            if path == "/api/act":
                got, err = clean_act(body, card_ids())
                if err:
                    return self.reply(400, {"error": err})
                try:
                    return self.reply(200, {"ok": True, "n": record(*got)})
                except Broken as e:
                    return self.reply(500, {"error": str(e)})
            if path == "/api/decisions":
                write("decisions.json", body)
                write("status.json", {"phase": "applying"})
            elif path == "/api/replies":
                write("replies.json", body)
                write("status.json", {"phase": "posting"})
            elif path == "/api/reset":
                with lock:
                    # keep counting from the highest number either file knows, even if one of them is damaged
                    last = 0
                    for name in ("acts.json", "progress.json"):
                        try:
                            f = read_round(root / name)
                        except Broken:
                            continue
                        last = max([last, f.get("n", 0) if isinstance(f.get("n"), int) else 0,
                                    *[c.get("n", 0) for c in (f.get("cards") or {}).values() if isinstance(c, dict) and isinstance(c.get("n"), int)]])
                    for name in ROUND_FILES:
                        (root / name).unlink(missing_ok=True)
                    if last:  # keep counting up, so progress Claude writes for an old act never matches a new one
                        write("acts.json", {"n": last, "cards": {}, "later": [], "drafts": [], "unlater": []})
            else:
                return self.reply(404, {"error": "unknown endpoint"})
            return self.reply(200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Blueline review: http://localhost:{args.port}/  (serving {root})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
