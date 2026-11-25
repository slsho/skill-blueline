"""Wait until the owner gives Claude something to do on the review page, then print it.

Usage:
  python3 next.py <review-dir> [--timeout SECONDS] [--once]

Run it in the background while the review page is open. It ends the moment there is
work Claude hasn't picked up yet, and prints it as JSON:

  {"work": [
     {"do": "apply", "id": 2, "n": 4, "kind": "change", "edit": [{"find", "after", "claude"}],
      "pick": "B", "reply": "...", "commentId": "...", "apply": "...", "where": "Slides 3 and 5"},
     {"do": "redo",  "id": 5, "n": 6, "note": "use teal, not grey", ...},
     {"do": "undo",  "id": 2, "n": 9, ...},
     {"do": "post",  "id": 1, "n": 7, "text": "...", "commentId": "..."},    a later reply
     {"do": "unpost", "id": 1, "n": 11, "text": "...", "commentId": "..."},  take a posted reply back out
     {"do": "draft", "id": 4, "n": 8, "for": "reply", ...}                   write a reply
   ],
   "round": {"done": [1, 2], "with_claude": [5], "undecided": [3, 4, 6]}}

Work is anything the page recorded (acts.json) that progress.json hasn't answered yet.
"round" says where every card stands, so you can tell when the owner has finished.
--once prints what there is right now, even nothing, instead of waiting.
Exits with code 2 on timeout (default: 6 hours).

The fast loop (what SKILL.md uses):
  python3 next.py <review-dir> --claim --post [--done '<use_figma result>']

  --claim  marks each item started as it's handed out (applying, undoing, revising), so the page
           shows it at once and you don't need a progress.py call.
  --post   posts approved reply-only cards and later replies itself while it waits, and closes
           picks whose option changes nothing. It also takes replies back out of their threads when
           the owner presses Undo on them (unpost). None of these reach you.
  --done   records how the last use_figma call went ({"2": "ok", "5": {"ok": false, "why": ...}}):
           applied cards become "in" (their reply is posted first), undone cards go back to
           "open" (their reply is taken out of the thread first), failures show their reason.
  Apply and undo items for cards with `ops` come with "js": one use_figma call for all of them
  (figjs.py). Run it, then start next.py again with --done and its result.

  --done goes by the decision each item was claimed for, so a result is never recorded against a newer
  decision the owner made meanwhile (an Undo pressed in another window lands as the next item instead).
  An apply item with "undo_first" is a card approved again while its earlier version is still in the
  deck: "js" takes that version out first. Without js ("manual"), undo it yourself before applying, and
  take "prior_reply" (the earlier version's reply) out of the thread if the new reply differs.
"""
import json
import pathlib
import sys
import time

import figjs
import bl_state

TODO = {"approve": "apply", "back": "redo", "undo": "undo"}
DONE = {"in", "posted", "closed"}


read = bl_state.read


def card_state(act, prog):
    """Where a card stands: undecided, done, with_claude (waiting or being worked on)."""
    if not act or act["act"] == "reopen":
        return "undecided"
    if act["act"] in ("skip", "close"):
        return "done"
    if prog and prog.get("n") == act["n"]:
        st = prog.get("st")
        if st in DONE:
            return "done"
        if st == "failed" and act["act"] == "undo":
            return "done"        # couldn't come out: it's still in the deck, and the page shows it that way
        if st in ("open", "failed"):
            return "undecided"   # redone, taken back out, or didn't go in: the owner decides again
    return "with_claude"


def look(root):
    acts, prog = read(root / "acts.json"), read(root / "progress.json")
    data = read(root / "data.json")
    cards = {c["id"]: c for c in data.get("cards", [])}
    pc = prog.get("cards", {})
    work, rnd = [], {"done": [], "with_claude": [], "undecided": []}

    def base(cid):
        c = cards.get(cid, {})
        return {k: c[k] for k in ("kind", "where", "slide", "commentId", "apply", "who") if k in c}

    for cid in sorted(cards):
        act, p = acts.get("cards", {}).get(str(cid)), pc.get(str(cid))
        rnd[card_state(act, p)].append(cid)
        if not act or act["act"] not in TODO or (p and p.get("n", 0) >= act["n"]):
            continue
        if act["act"] == "undo" and p and p.get("st") == "posted":   # only a reply went out: no deck change to undo
            work.append(dict(do="unpost", id=cid, n=act["n"], text=act.get("reply", ""), **base(cid)))
            continue
        item = dict(do=TODO[act["act"]], id=cid, n=act["n"], **base(cid))
        if act["act"] == "approve" and p and p.get("st") == "in":
            # an earlier approval of this card is still in the deck (approved again from another window before
            # Claude took the first one out): take it out first, then put this one in
            item["undo_first"] = True
            if p.get("reply"):
                item["prior_reply"] = p["reply"]
        if act["act"] == "undo":
            item["reply"] = act.get("reply", "")
        if act["act"] == "approve":
            c = cards.get(cid, {})
            owner = act.get("edit") or []
            item["edit"] = [{"find": e.get("find", ""), "after": owner[j] if j < len(owner) else e["after"],
                             "claude": e["after"]} for j, e in enumerate(c.get("edit", []))]
            if c.get("kind") == "pick":
                item["pick"] = act.get("pick") or c.get("pick")
            item["reply"] = act.get("reply", "")
        elif act["act"] == "back":
            item["note"] = act.get("note", "")
        work.append(item)
    done_later, done_drafts = prog.get("later", {}), prog.get("drafts", {})
    for x in acts.get("later", []):
        if str(x["n"]) not in done_later:
            work.append(dict(do="post", id=x["id"], n=x["n"], text=x["text"], **base(x["id"])))
    later_by_n, done_unlater = {x["n"]: x for x in acts.get("later", [])}, prog.get("unlater", {})
    for u in acts.get("unlater", []):
        if str(u["n"]) not in done_unlater:
            text = later_by_n.get(u["of"], {}).get("text", "")
            work.append(dict(do="unpost", id=u["id"], n=u["n"], of=u["of"], text=text, **base(u["id"])))
    for x in acts.get("drafts", []):
        if str(x["n"]) not in done_drafts:
            work.append(dict(do="draft", id=x["id"], n=x["n"], **{"for": x["for"]}, **base(x["id"])))
    work.sort(key=lambda w: w["n"])
    return work, rnd


def auto_post(root):
    """--post: handle the decisions that need no Figma edit, right here."""
    acts, prog, data = read(root / "acts.json"), read(root / "progress.json"), read(root / "data.json")
    url = (data.get("deck") or {}).get("url")
    if not url:
        return
    pc = prog.get("cards", {})
    for c in data.get("cards", []):
        a, p = acts.get("cards", {}).get(str(c["id"])), pc.get(str(c["id"]))
        if not a or a["act"] != "approve" or (p and p.get("n", 0) >= a["n"]):
            continue
        if c.get("kind") == "pick":
            opt = next((o for o in c.get("options", []) if o.get("key") == (a.get("pick") or c.get("pick"))), {})
            if opt.get("ops") != []:
                continue
        elif c.get("kind") != "reply":
            continue
        reply = (a.get("reply") or "").strip()
        old = (p or {}).get("reply") if (p or {}).get("st") in ("posted", "in") else None
        if old and old != reply:   # posted earlier, then approved again with other words: the old one comes down first
            err = bl_state.delete_reply(url, c.get("commentId"), old)
            if err:
                bl_state.set_card(root, c["id"], a["n"], "failed", f"Couldn't take the earlier reply down: {err}")
                continue
        if not reply:
            bl_state.set_card(root, c["id"], a["n"], "closed")
            continue
        bl_state.set_card(root, c["id"], a["n"], "posting")
        err = bl_state.post_reply(url, c.get("commentId"), reply)
        bl_state.set_card(root, c["id"], a["n"], "failed" if err else "posted", err, reply=None if err else reply)
    for x in acts.get("later", []):
        if str(x["n"]) in prog.get("later", {}):
            continue
        cid = next((c.get("commentId") for c in data.get("cards", []) if c["id"] == x["id"]), None)
        err = bl_state.post_reply(url, cid, x["text"])
        with bl_state.progress(root) as pr:
            pr.setdefault("later", {})[str(x["n"])] = {"st": "failed", "why": err} if err else {"st": "posted"}
    auto_unpost(root, url)


def auto_unpost(root, url):
    """Undo on a reply: take it back out of the thread. Nothing in the deck changes, so no Claude turn."""
    acts, prog, data = read(root / "acts.json"), read(root / "progress.json"), read(root / "data.json")
    thread = {c["id"]: c.get("commentId") for c in data.get("cards", [])}
    pc = prog.get("cards", {})
    for c in data.get("cards", []):
        a, p = acts.get("cards", {}).get(str(c["id"])), pc.get(str(c["id"]))
        if not a or a["act"] != "undo" or not p or p.get("n", 0) >= a["n"] or p.get("st") != "posted":
            continue
        bl_state.set_card(root, c["id"], a["n"], "unposting")
        err = bl_state.delete_reply(url, c.get("commentId"), a.get("reply"))
        # couldn't: it's still posted, and the page says why
        bl_state.set_card(root, c["id"], a["n"], "posted" if err else "open", f"Couldn't take it out of the thread: {err}" if err else None)
    later_by_n = {x["n"]: x for x in acts.get("later", [])}
    for u in acts.get("unlater", []):
        if str(u["n"]) in prog.get("unlater", {}):
            continue
        x, was = later_by_n.get(u["of"]), (prog.get("later", {}).get(str(u["of"])) or {}).get("st")
        err = bl_state.delete_reply(url, thread.get(u["id"]), x["text"]) if x and was == "posted" else None
        with bl_state.progress(root) as pr:
            pr.setdefault("unlater", {})[str(u["n"])] = {"st": "failed", "why": err} if err else {"st": "done"}
            if not err:
                pr.setdefault("later", {})[str(u["of"])] = {"st": "removed"}


def record_done(root, result):
    """--done: turn a use_figma result into card states, posting replies of applied cards.

    Each result is recorded against the decision that was claimed for that Figma call (its n, what it was
    and its reply, saved by --claim), not against whatever the owner decided since. If they pressed Undo
    in another window while the change was going in, the change is recorded as in, under its own n, and
    the Undo is handed out next. Without a claim (next.py ran without --claim) the latest decision is used."""
    acts, data, prog = read(root / "acts.json"), read(root / "data.json"), read(root / "progress.json")
    url = (data.get("deck") or {}).get("url")
    for cid, r in result.items():
        a = acts.get("cards", {}).get(str(cid))
        p = prog.get("cards", {}).get(str(cid)) or {}
        if p.get("st") in ("applying", "undoing") and p.get("act") in ("approve", "undo"):
            what, n, reply = p["act"], p["n"], (p.get("reply") or "").strip()
            stale = not a or a.get("n") != n
        elif a and a["act"] in ("approve", "undo"):
            what, n, reply, stale = a["act"], a["n"], (a.get("reply") or "").strip(), False
        else:
            continue
        ok = r == "ok" or (isinstance(r, dict) and r.get("ok"))
        why = r.get("why") if isinstance(r, dict) else None
        card = next((c for c in data.get("cards", []) if str(c["id"]) == str(cid)), {})
        if what == "undo":
            if ok and reply and url:      # the change is out; its reply comes out of the thread too
                bl_state.set_card(root, cid, n, "unposting")
                err = bl_state.delete_reply(url, card.get("commentId"), reply)
                bl_state.set_card(root, cid, n, "open", f"Out of the deck, but the reply is still in the thread: {err}" if err else None)
                continue
            bl_state.set_card(root, cid, n, "open" if ok else "failed", None if ok else why)
            continue
        if not ok:
            bl_state.set_card(root, cid, n, "failed", why or "it didn't go in")
            continue
        if stale:                         # the owner has moved on: no reply for a decision they've since replaced
            bl_state.set_card(root, cid, n, "in")
            continue
        err, prior = None, (p.get("prior_reply") or "").strip()
        if prior and prior != reply and url:   # an earlier approval's reply, for the version just taken out
            err = bl_state.delete_reply(url, card.get("commentId"), prior)
        if reply and url and not err:
            bl_state.set_card(root, cid, n, "posting")
            err = bl_state.post_reply(url, card.get("commentId"), reply)
        bl_state.set_card(root, cid, n, "in", f"In the deck, but the reply didn't post: {err}" if err else None,
                          reply=reply if reply and not err else None)


CLAIM = {"apply": "applying", "undo": "undoing", "redo": "revising"}


def finish(root, work, claim):
    """Attach one use_figma script for every apply/undo item that has ops; claim items if asked."""
    batch = []
    for w in work:
        if w["do"] == "apply":
            try:
                figjs.ops_for(root, figjs.cards_by_id(root)[w["id"]], w.get("pick"), [e["after"] for e in w.get("edit", [])])
                if w.get("undo_first"):   # the earlier version comes out in the same call, just before
                    batch.append({"id": w["id"], "do": "undo"})
                batch.append({"id": w["id"], "do": "apply", "pick": w.get("pick"),
                              "edit": [e["after"] for e in w.get("edit", [])]})
            except (figjs.NoOps, KeyError):
                w["manual"] = True        # no ops in data.json: write the change yourself
        elif w["do"] == "undo":
            batch.append({"id": w["id"], "do": "undo"})
        if claim and w["do"] in CLAIM:
            # what this claim is for, so --done records the result against it even if the owner decides again meanwhile
            bl_state.set_card(root, w["id"], w["n"], CLAIM[w["do"]], act={"apply": "approve", "undo": "undo", "redo": "back"}[w["do"]],
                              reply=w.get("reply") if w["do"] in ("apply", "undo") else None,
                              prior_reply=w.get("prior_reply"))
    return figjs.items_js(root, batch) if batch else None


def main():
    argv = sys.argv[1:]
    val = lambda f: argv[argv.index(f) + 1] if f in argv and argv.index(f) + 1 < len(argv) else None
    taken = {val(f) for f in ("--timeout", "--done") if val(f) is not None}
    args = [a for a in argv if not a.startswith("--") and a not in taken]
    if not args:
        sys.exit(__doc__)
    root = pathlib.Path(args[0])
    timeout = float(val("--timeout") or 6 * 3600)
    if val("--done"):
        raw = sys.stdin.read() if val("--done") == "-" else val("--done")
        try:
            result = json.loads(raw)
        except ValueError:
            sys.exit("--done needs the use_figma result as JSON")
        if isinstance(result, dict) and "needLib" not in result:
            record_done(root, result)
    end = time.monotonic() + timeout
    while True:
        if "--post" in argv:
            auto_post(root)
        work, rnd = look(root)
        if work or "--once" in argv:
            break
        if time.monotonic() > end:
            print(f"Timed out waiting for work in {root}", file=sys.stderr)
            sys.exit(2)
        time.sleep(0.3)
    out = {"work": work, "round": rnd}
    js = finish(root, work, "--claim" in argv)
    if js:
        out["js"] = js
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
