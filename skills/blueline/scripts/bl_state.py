"""Shared by next.py and progress.py: read the round files, update progress.json under a lock,
and post a reply into a Figma thread."""
import contextlib
import fcntl
import json
import time


class Unreadable(SystemExit):
    """A round file that exists but isn't a JSON object. Every write here is atomic, so this means it was
    damaged by hand. Treating it as empty would hand out finished work again (a second apply, a second
    post), so the scripts stop and say which file instead."""


def read(path):
    """A round file as a dict: {} when it doesn't exist yet. Stops with Unreadable when it can't be read."""
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text())
    except ValueError as e:
        raise Unreadable(f"{path} can't be read ({e}). Fix it or restore it, then run this again. "
                         "Nothing was changed: reading it as empty would redo finished work.")
    if not isinstance(obj, dict):
        raise Unreadable(f"{path} should hold a JSON object, not a {type(obj).__name__}. Fix it or restore it, "
                         "then run this again. Nothing was changed.")
    return obj


@contextlib.contextmanager
def progress(root):
    """Read-modify-write progress.json. The lock keeps next.py and progress.py from losing each other's writes."""
    with open(root / ".progress.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prog = read(root / "progress.json")
        yield prog
        tmp = root / "progress.json.tmp"
        tmp.write_text(json.dumps(prog, indent=2, ensure_ascii=False))
        tmp.replace(root / "progress.json")   # atomic: the server never reads half a file


def set_card(root, cid, n, st, why=None, **extra):
    """Write one card's state against decision n. extra fields (a claim's "act", a posted "reply") ride along;
    the page reads only n, st and why."""
    with progress(root) as prog:
        entry = {"n": n, "st": st, "at": time.time(), **{k: v for k, v in extra.items() if v is not None}}
        if why:
            entry["why"] = why
        prog.setdefault("cards", {})[str(cid)] = entry
    return entry


def post_reply(url, comment_id, text):
    """Post one reply. Returns None, or why it failed. Skips a reply already in the thread."""
    from figma_api import call, file_key
    try:
        key = file_key(url)
        for c in call("GET", f"/files/{key}/comments")["comments"]:
            if c.get("parent_id") == comment_id and c.get("message") == text:
                return None
        call("POST", f"/files/{key}/comments", {"message": text, "comment_id": comment_id})
        return None
    except SystemExit as e:          # figma_api exits with a readable message; keep waiting instead
        return str(e)[:200]
    except Exception as e:           # network trouble
        return f"couldn't reach Figma: {e}"[:200]


def delete_reply(url, comment_id, text):
    """Take a posted reply back out of its thread: the newest reply in that thread with exactly this text.
    Returns None (also when it's already gone), or why it failed. Figma only lets you delete your own."""
    from figma_api import call, file_key
    text = (text or "").strip()
    if not text:
        return None
    try:
        key = file_key(url)
        mine = [c for c in call("GET", f"/files/{key}/comments")["comments"]
                if c.get("parent_id") == comment_id and (c.get("message") or "").strip() == text]
        if mine:
            newest = max(mine, key=lambda c: c.get("created_at") or "")
            call("DELETE", f"/files/{key}/comments/{newest['id']}")
        return None
    except SystemExit as e:
        return str(e)[:200]
    except Exception as e:
        return f"couldn't reach Figma: {e}"[:200]
