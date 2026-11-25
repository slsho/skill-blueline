"""Tell the review page how Claude is getting on with a card. The page shows it within a second or two.

Usage:
  python3 progress.py <review-dir> card <id> <state> [--why TEXT] [--n N]
  python3 progress.py <review-dir> later <n> posted|failed [--why TEXT]
  python3 progress.py <review-dir> unlater <n> done|failed [--why TEXT]   (a later reply taken back out)
  python3 progress.py <review-dir> draft <n> --text TEXT      (--text - reads it from stdin)

Card states, by what the owner asked for (scripts/next.py says which):
  apply   applying -> posting (only if it has a reply) -> in, or posted for a reply-only card
          failed --why "..." if it didn't go in; the owner can approve it again or send it back
  redo    revising -> open           update data.json and img/ first: the page reloads the card
  undo    undoing  -> unposting (only if it posted a reply) -> open
          failed --why "..." if it couldn't come out
  unpost  unposting -> open          a reply-only card: take the reply out of the thread
          (post_replies.py --delete); posted --why "..." if it couldn't

Each entry is tied to one decision on that card (its "n" in acts.json), so a newer decision on the
same card shows as new work again instead of looking finished. Pass --n with the n of the work item
you handled: if the owner has decided again since (another window, say), the state is recorded
against the decision you actually worked on and the newer one stays in the queue. Without --n it's
recorded against the latest decision.
Only Claude writes progress.json; the review server reads it and never writes it.
"""
import json
import pathlib
import sys
import time

import bl_state

CARD_STATES = {
    "approve": {"applying", "posting", "in", "posted", "closed", "failed"},
    "back": {"revising", "open"},
    "undo": {"undoing", "unposting", "open", "posted", "failed"},
}


read = bl_state.read


def opt(name):
    if name not in sys.argv:
        return None
    v = sys.argv[sys.argv.index(name) + 1]
    return sys.stdin.read().strip() if v == "-" else v


def main():
    args, skip = [], set()
    for i, a in enumerate(sys.argv[1:], 1):
        if a in ("--why", "--text", "--n"):
            skip.add(i + 1)
        elif i not in skip:
            args.append(a)
    if len(args) < 3:
        sys.exit(__doc__)
    root, what = pathlib.Path(args[0]), args[1]
    acts = read(root / "acts.json")
    why = opt("--why")
    with bl_state.progress(root) as prog:
        entry = change(args, acts, prog, why)
    print(json.dumps({"ok": True, what: entry}, ensure_ascii=False))


def change(args, acts, prog, why):
    what = args[1]
    if what == "card":
        if len(args) < 4:
            sys.exit(__doc__)
        cid, st = args[2], args[3]
        act = acts.get("cards", {}).get(cid)
        if not act:
            sys.exit(f"card {cid}: the owner hasn't decided anything on it")
        n = act["n"]
        if opt("--n") is not None:
            try:
                n = int(opt("--n"))
            except ValueError:
                sys.exit("--n is the work item's number")
            if n > act["n"]:
                sys.exit(f"card {cid}: there's no decision {n} yet (the latest is {act['n']})")
        # for an older decision the owner has since replaced, its act is gone from acts.json: any state goes
        allowed = CARD_STATES.get(act["act"], set()) if n == act["n"] else set().union(*CARD_STATES.values())
        if st not in allowed:
            sys.exit(f"card {cid}: the owner asked to {act['act']}; the state is one of {sorted(allowed)}")
        entry = {"n": n, "st": st, "at": time.time()}
        if why:
            entry["why"] = why
        prog.setdefault("cards", {})[cid] = entry
    elif what == "unlater":
        n = args[2] if len(args) > 2 else ""
        u = next((x for x in acts.get("unlater", []) if str(x["n"]) == n), None)
        if not u:
            sys.exit(f"no unlater number {n} in acts.json")
        st = args[3] if len(args) > 3 else ""
        if st not in ("done", "failed"):
            sys.exit("a later reply taken back out is done or failed")
        entry = {"st": st, **({"why": why} if why else {})}
        prog.setdefault("unlater", {})[n] = entry
        if st == "done":
            prog.setdefault("later", {})[str(u["of"])] = {"st": "removed"}
    elif what in ("later", "draft"):
        n = args[2]
        listed = {str(x["n"]) for x in acts.get("later" if what == "later" else "drafts", [])}
        if n not in listed:
            sys.exit(f"no {what} number {n} in acts.json")
        if what == "later":
            st = args[3] if len(args) > 3 else ""
            if st not in ("posted", "failed"):
                sys.exit("a later reply is posted or failed")
            entry = {"st": st, **({"why": why} if why else {})}
        else:
            text = opt("--text")
            if not text:
                sys.exit("draft needs --text")
            entry = {"text": text}
        prog.setdefault(what if what == "later" else "drafts", {})[n] = entry
    else:
        sys.exit(__doc__)
    return entry


if __name__ == "__main__":
    main()
