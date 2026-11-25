"""List the open comment threads on a Figma file as JSON.

Usage:
  python3 fetch_comments.py <figma-url-or-key> [--all]

Prints one entry per thread (resolved threads are left out unless --all):
  {"id", "author", "message", "node_id", "created_at", "replies": [{"author", "message"}]}

node_id is the layer the comment is pinned to. On a Slides file it is usually the
slide itself or something on it; resolve it to a slide with the Figma connector.
If a message starts with "Name (Role): ", that prefix is treated as the author, so
comments posted on someone's behalf (as in the demo) keep the right name.
"""
import json
import re
import sys

from figma_api import call, file_key

PREFIX = re.compile(r"^([A-Z][\w.'-]+(?: [A-Z][\w.'-]+)*) \(([^)]+)\): ")


def author(c):
    m = PREFIX.match(c.get("message", ""))
    if m:
        return {"name": m.group(1), "role": m.group(2)}, c["message"][m.end():]
    u = c.get("user") or {}
    return {"name": u.get("handle", "Someone"), "role": ""}, c.get("message", "")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    key = file_key(args[0])
    comments = call("GET", f"/files/{key}/comments")["comments"]
    threads, replies = {}, {}
    for c in comments:
        if c.get("parent_id"):
            replies.setdefault(c["parent_id"], []).append(c)
        else:
            threads[c["id"]] = c
    out = []
    for cid, c in sorted(threads.items(), key=lambda kv: kv[1].get("created_at", "")):
        if c.get("resolved_at") and "--all" not in sys.argv:
            continue
        who, msg = author(c)
        meta = c.get("client_meta") or {}
        out.append({
            "id": cid,
            "author": who,
            "message": msg,
            "node_id": meta.get("node_id"),
            "created_at": c.get("created_at"),
            "replies": [dict(zip(("author", "message"), author(r))) for r in
                        sorted(replies.get(cid, []), key=lambda r: r.get("created_at", ""))],
        })
    print(json.dumps({"file_key": key, "threads": out}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
