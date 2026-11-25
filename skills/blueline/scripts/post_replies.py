"""Post approved replies into their Figma comment threads.

Usage:
  python3 post_replies.py <figma-url-or-key> --thread <commentId> --text TEXT [--dry-run]
  python3 post_replies.py <figma-url-or-key> --thread <commentId> --text TEXT --delete
  python3 post_replies.py <figma-url-or-key> <replies.json> [--dry-run]

The first form posts one reply, the way the review page sends them: one card at a
time, as the owner approves it. --text - reads the reply from stdin, which is safer
for text with quotes in it. With --delete it takes that reply back out of the thread
instead (the owner pressed Undo): the newest reply there with exactly that text.

The second form posts a batch:
  {"replies": [{"id": 6, "commentId": "123456", "to": "tom", "slide": 6, "text": "..."}]}

A reply whose exact text is already in that thread is skipped, so running it twice is
safe. Replies are posted from the token owner's Figma account. Prints
{"posted": n, "skipped": n, "no_thread": [ids]}.
"""
import json
import pathlib
import sys

from figma_api import call, file_key


def opt(name):
    if name not in sys.argv:
        return None
    i = sys.argv.index(name) + 1
    if i >= len(sys.argv):
        sys.exit(f"{name} needs a value")
    return sys.stdin.read() if sys.argv[i] == "-" else sys.argv[i]


def main():
    dry = "--dry-run" in sys.argv
    values = {sys.argv.index(n) + 1 for n in ("--thread", "--text") if n in sys.argv}
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and i not in values]
    if not args:
        sys.exit(__doc__)
    key = file_key(args[0])
    thread = opt("--thread")
    if thread is not None:
        text = opt("--text")
        if text is None:
            sys.exit("--thread needs --text")
        if "--delete" in sys.argv:
            import bl_state
            err = bl_state.delete_reply(args[0], thread, text)
            print(json.dumps({"deleted": not err, **({"why": err} if err else {})}))
            sys.exit(1 if err else 0)
        replies = [{"commentId": thread, "text": text}]
    elif len(args) >= 2:
        replies = json.loads(pathlib.Path(args[1]).read_text()).get("replies", [])
    else:
        sys.exit(__doc__)
    existing = set()
    if not dry:
        for c in call("GET", f"/files/{key}/comments")["comments"]:
            existing.add((c.get("parent_id"), c.get("message")))
    posted, skipped, missing = 0, 0, []
    for r in replies:
        cid, text = r.get("commentId"), (r.get("text") or "").strip()
        if not text:
            continue
        if not cid:
            missing.append(r.get("id"))
            continue
        print(f"  slide {r.get('slide', '?'):>2}  -> {r.get('to', '?')}: {text[:80]}")
        if dry:
            continue
        if (cid, text) in existing:
            skipped += 1
            continue
        call("POST", f"/files/{key}/comments", {"message": text, "comment_id": cid})
        posted += 1
    print(json.dumps({"posted": posted, "skipped": skipped, "no_thread": missing}))


if __name__ == "__main__":
    main()
