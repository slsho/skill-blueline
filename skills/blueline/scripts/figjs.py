"""Build the use_figma code for previews, applying and undoing cards, from data.json.

Usage:
  python3 figjs.py <review-dir> install           store the Blueline runtime in the deck (once per round)
  python3 figjs.py <review-dir> preview <ids>     apply cards to clones of their slides, for screenshots
  python3 figjs.py <review-dir> apply <ids>       apply cards to the deck in the owner's wording
  python3 figjs.py <review-dir> undo <ids>        take applied cards back out
  python3 figjs.py <review-dir> drop <clone-ids>  remove preview clones

<ids> is a comma list like 2,5 (a pick option: 7:B). It prints the code; paste it into use_figma
as is. Every call except install is a few lines: the runtime (blueline.js) lives in the deck. If a
call returns {"needLib": ...}, run install and the call again.

Each card says what to change in data.json, as `ops` (pick cards: on each option):
  {"op": "replace", "node": "3:16", "line": 0}            edit line 0: its find -> the owner's wording
  {"op": "replace", "node": "3:16", "find": "a", "repl": "b"}
  {"op": "set", "node": "3:20", "props": {"textAlignHorizontal": "CENTER"}}
  {"op": "js", "file": "apply/4.js"}                     custom code, the body of async (k) => {...}
Custom code changes nodes only through k (k.text, k.set, k.resize, k.made, k.node), so it can
be previewed on a clone and undone. See references/figma-recipes.md.
"""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
LIB = (HERE / "blueline.js").read_text()
VER = hashlib.sha1(LIB.encode()).hexdigest()[:8]


def read(path):
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        return {}


def install_js():
    body = "\n".join(l for l in LIB.splitlines() if l.strip() and not l.lstrip().startswith("//"))
    stored = f"//bl:{VER}\n" + body
    return (f'figma.root.setSharedPluginData("blueline", "lib", {json.dumps(stored)});\n'
            f'return {{ installed: "{VER}" }};')


def head():
    return (f'const L = figma.root.getSharedPluginData("blueline", "lib");\n'
            f'if (!L.startsWith("//bl:{VER}")) return {{ needLib: "{VER}" }};\n'
            f'const BL = new Function(L)();\n')


class NoOps(Exception):
    pass


def ops_for(root, card, pick=None, owner=None):
    """The card's ops as JS source, with the owner's wording put in. NoOps if the card has none."""
    src = card
    if card.get("kind") == "pick":
        key = pick or card.get("pick")
        src = next((o for o in card.get("options", []) if o.get("key") == key), {})
    if "ops" not in src:
        raise NoOps(card.get("id"))
    edit, owner = card.get("edit", []), owner or []
    out = []
    for op in src["ops"]:
        op = dict(op)
        if op.get("op") == "replace" and "line" in op:
            j = op.pop("line")
            line = edit[j] if j < len(edit) else {}
            op.setdefault("find", line.get("find", ""))
            if "repl" not in op:
                mine = owner[j] if j < len(owner) and isinstance(owner[j], str) else None
                op["repl"] = mine if mine is not None else line.get("after", "")
        if op.get("op") == "js":
            body = (root / op["file"]).read_text()
            out.append("{ op: \"js\", fn: async (k) => {\n" + body.rstrip() + "\n} }")
        else:
            out.append(json.dumps(op, ensure_ascii=False))
    return "[" + ", ".join(out) + "]"


def cards_by_id(root):
    return {c["id"]: c for c in read(root / "data.json").get("cards", [])}


def items_js(root, items):
    """items: [{"id", "do": "apply"|"undo", "pick"?, "edit"?: owner wording}] -> BL.run(...) source."""
    cards, parts = cards_by_id(root), []
    for it in items:
        if it["do"] == "undo":
            parts.append(f'{{ id: {it["id"]}, do: "undo" }}')
        else:
            ops = ops_for(root, cards[it["id"]], it.get("pick"), it.get("edit"))
            parts.append(f'{{ id: {it["id"]}, do: "apply", ops: {ops} }}')
    return head() + "return await BL.run([\n  " + ",\n  ".join(parts) + "\n]);"


def owner_items(root, ids, do):
    acts = read(root / "acts.json").get("cards", {})
    items = []
    for cid in ids:
        a = acts.get(str(cid), {})
        items.append({"id": cid, "do": do, "pick": a.get("pick") or None, "edit": a.get("edit") or []})
    return items


def preview_js(root, specs):
    cards, parts = cards_by_id(root), []
    for cid, pick in specs:
        parts.append(f'{{ id: {cid}, ops: {ops_for(root, cards[cid], pick)} }}')
    return head() + "return await BL.preview([\n  " + ",\n  ".join(parts) + "\n]);"


def parse_ids(s):
    out = []
    for part in s.split(","):
        cid, _, pick = part.strip().partition(":")
        out.append((int(cid), pick or None))
    return out


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    root, what = pathlib.Path(a[0]), a[1]
    try:
        if what == "install":
            print(install_js())
        elif what in ("apply", "undo") and len(a) > 2:
            print(items_js(root, owner_items(root, [i for i, _ in parse_ids(a[2])], what)))
        elif what == "preview" and len(a) > 2:
            print(preview_js(root, parse_ids(a[2])))
        elif what == "drop" and len(a) > 2:
            ids = [i for x in a[2:] for i in x.split(",") if i]
            print(head() + f"return await BL.drop({json.dumps(ids)});")
        else:
            sys.exit(__doc__)
    except NoOps as e:
        sys.exit(f"card {e} has no ops in data.json: add them, or make this change by hand")
    except KeyError as e:
        sys.exit(f"no card {e} in data.json")


if __name__ == "__main__":
    main()
