#!/usr/bin/env python3
"""Check that a page uses Figma's Simple Design System (SDS) and nothing else.

    python3 check_design_system.py [page.html ...]      (default: the review page, app/index.html)

The tokens come from references/sds-tokens.json, read from the SDS Figma file. Prints each problem
with its line number and exits 1 if there are any. A page passes when:

  1. Every --sds-* token it defines has the value the SDS file gives it (Dark mode for colours).
  2. Its own names (--bl-*) are built from SDS tokens only, with no raw colours or lengths in them.
  3. No raw colour appears anywhere else: not in the CSS, a style="" attribute, or the script.
  4. Spacing, corners, borders, type sizes, weights, families and shadows use tokens, not numbers.
     0 is fine, percentages are fine for positions, and line heights are the SDS text styles'.
  5. Every var() it uses is defined, and every font it loads is an SDS family.

Widths, heights, transforms and timings are layout and motion, which SDS has no tokens for, so they
aren't checked.
"""
import json
import pathlib
import re
import sys

SKILL = pathlib.Path(__file__).resolve().parent.parent
TOKENS = SKILL / "references" / "sds-tokens.json"
PAGE = SKILL / "app" / "index.html"

# Positions the page works out as it runs (the Before/After knob, pin nudges, room for the bar).
LAYOUT_VARS = {"--p", "--nudge", "--extra"}

COLOR_PROPS = {"color", "background-color", "border-color", "border-top-color", "border-right-color",
               "border-bottom-color", "border-left-color", "outline-color", "caret-color", "fill", "stroke",
               "text-decoration-color", "accent-color", "column-rule-color"}
COLOR_SHORTHANDS = {"background", "border", "border-top", "border-right", "border-bottom", "border-left",
                    "outline", "box-shadow", "text-shadow", "text-decoration", "column-rule"}
LENGTH_PROPS = re.compile(r"^(gap|row-gap|column-gap|padding(-\w+)?|margin(-\w+)?|top|right|bottom|left|inset(-\w+)?"
                          r"|outline(-width|-offset)?|border(-(top|right|bottom|left))?(-width)?|border-radius|"
                          r"border-(top|bottom)-(left|right)-radius|box-shadow|text-shadow|font|font-size|"
                          r"letter-spacing|text-underline-offset|text-decoration(-thickness)?|column-rule)$")
NAMED = {"black", "white", "red", "green", "blue", "yellow", "orange", "purple", "pink", "gray", "grey", "silver",
         "maroon", "navy", "teal", "olive", "lime", "aqua", "cyan", "magenta", "fuchsia", "brown", "gold", "indigo",
         "violet", "tomato", "coral", "salmon", "crimson", "beige", "ivory", "khaki", "lavender", "tan", "wheat",
         "whitesmoke", "gainsboro", "lightgray", "lightgrey", "darkgray", "darkgrey", "dimgray", "dimgrey"}
COLOR_OK = {"transparent", "currentcolor", "inherit", "initial", "unset", "none"}
ALIAS_WORDS = {"0", "inset", "solid", "dashed", "dotted", "transparent", "currentcolor", "none"}
SDS_FONT_HOSTS = ("fonts.googleapis.com",)

HEX = r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b"
RAW_COLOR = re.compile(rf"(?<![\w&]){HEX}|%23[0-9a-fA-F]{{3,8}}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", re.I)
SCRIPT_COLOR = re.compile(rf"""["'`]{HEX}["'`]|\b(?:rgba?|hsla?)\(""")
SCRIPT_STYLE = re.compile(r"\.style\.(color|background\w*|border\w*|outline\w*|boxShadow|fill|stroke|caretColor)\s*=")
LENGTH = re.compile(r"(?<![\w.#-])-?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|pt|pc|vh|vw|ch|ex)\b")
VAR = re.compile(r"var\(\s*(--[\w-]+)")


def load_tokens(path=TOKENS):
    """Every SDS token as {css name: expected value}, colours in the page's mode, sizes in px."""
    data = json.loads(pathlib.Path(path).read_text())
    mode = data["source"]["page_mode"]
    want = {k: v[mode] for k, v in data["color"].items()}
    want.update({k: v["value"] for k, v in data["primitive"].items()})
    want.update({k: v["value"] for k, v in data["size"].items()})
    want.update({k: v["value"] for k, v in data["typography"].items()})
    want.update({k: v["shadows"] for k, v in data["effects"].items()})
    line_heights = sorted({s["line_height"] for s in data["text_styles"]})
    families = sorted({v["value"] for v in data["typography"].values() if "Family" in v["name"]})
    return want, line_heights, families


def _hex(c):
    c = c.lower().lstrip("#")
    if len(c) in (3, 4):
        c = "".join(ch * 2 for ch in c)
    return "#" + (c[:6] if c.endswith("ff") and len(c) == 8 else c)


def _split(text, sep):
    """Split on sep outside brackets and quotes."""
    out, depth, quote, cur = [], 0, None, ""
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    return out


def declarations(html):
    """(selector, property, value, line) for every declaration in every <style> block."""
    found = []
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S):
        css, base = m.group(1), m.start(1)
        css = re.sub(r"/\*.*?\*/", lambda c: re.sub(r"[^\n]", " ", c.group()), css, flags=re.S)
        stack, start = [], 0
        for i, ch in enumerate(css):
            if ch == "{":
                stack.append((css[start:i].strip(), i + 1))
                start = i + 1
            elif ch == "}":
                selector, body_start = stack.pop()
                body = css[body_start:i]
                if "{" not in body:
                    pos = body_start
                    for part in _split(body, ";"):
                        if ":" in part:
                            prop, value = part.split(":", 1)
                            lead = len(part) - len(part.lstrip())
                            line = html.count("\n", 0, base + pos + lead) + 1
                            found.append((selector, prop.strip().lower(), " ".join(value.split()), line))
                        pos += len(part) + 1
                start = i + 1
    return found


def _strip_vars(value):
    while VAR.search(value):
        value = re.sub(r"var\([^()]*\)", " ", value)
    return value


def _check_value(prop, value, line_heights, where, problems, inline=False):
    """Rules 3 and 4 for one ordinary declaration."""
    if RAW_COLOR.search(value):
        problems.append(f"{where}: {prop}: {value} -- a raw colour; use an SDS colour token")
    bare = _strip_vars(value).lower()
    if inline and "${" in value and (prop in COLOR_PROPS or prop in COLOR_SHORTHANDS):
        problems.append(f"{where}: {prop} is set from the script; use a class with an SDS colour token")
    elif prop in COLOR_PROPS:
        if bare.strip() and bare.strip() not in COLOR_OK:
            problems.append(f"{where}: {prop}: {value} -- use an SDS colour token")
    if prop in COLOR_SHORTHANDS and set(re.findall(r"[a-z]+", bare)) & NAMED:
        problems.append(f"{where}: {prop}: {value} -- a named colour; use an SDS colour token")
    if LENGTH_PROPS.match(prop):
        nums = LENGTH.findall(bare)
        if nums:
            problems.append(f"{where}: {prop}: {value} -- {', '.join(nums)} isn't a token; use an SDS space, "
                            f"radius, stroke or type token")
    if prop.startswith("border") and "radius" in prop and "%" in bare:
        problems.append(f"{where}: {prop}: {value} -- use var(--sds-size-radius-full) for round, not a percentage")
    if prop in ("font-weight", "font-family", "font-size") and bare.strip() not in ("", "inherit"):
        problems.append(f"{where}: {prop}: {value} -- use the SDS typography token")
    if prop == "font":
        if re.search(r"(?<![\w.])[1-9]00(?![\w.])", bare):
            problems.append(f"{where}: font: {value} -- the weight should be an SDS weight token")
        lh = re.search(r"/\s*([\d.]+)(?![\w%])", bare)
        if lh and float(lh.group(1)) not in line_heights:
            problems.append(f"{where}: font: {value} -- line height {lh.group(1)} isn't an SDS text style's")
        if value != "inherit" and "var(" not in value.split("/")[-1].split()[-1]:
            problems.append(f"{where}: font: {value} -- the family should be an SDS font family token")
    if prop == "line-height":
        v = bare.strip()
        if v not in ("", "inherit", "normal") and not (re.fullmatch(r"[\d.]+", v) and float(v) in line_heights):
            problems.append(f"{where}: line-height: {value} -- use an SDS text style's line height {line_heights}")
    if prop == "letter-spacing" and bare.strip() not in ("", "0", "normal", "inherit"):
        problems.append(f"{where}: letter-spacing: {value} -- SDS text styles use 0 here")


def _shadows(value, root):
    out = []
    for part in _split(value, ","):
        part = part.strip()
        for _ in range(5):
            part = re.sub(r"var\((--[\w-]+)\)", lambda m: root.get(m.group(1), m.group(0)), part)
        inner = part.startswith("inset")
        bits = part.replace("inset", "").split()
        nums = [float(b[:-2]) if b.endswith("px") else float(b) for b in bits[:-1]]
        nums += [0] * (4 - len(nums))
        out.append({"type": "inner" if inner else "drop", "x": nums[0], "y": nums[1], "blur": nums[2],
                    "spread": nums[3], "color": _hex(bits[-1])})
    return out


def _check_token(name, value, want, root, where, problems):
    """Rule 1: an --sds-* token must exist in SDS and carry the file's value."""
    if name not in want:
        problems.append(f"{where}: {name} isn't an SDS token")
        return
    exp = want[name]
    if isinstance(exp, list):
        try:
            got = _shadows(value, root)
        except (ValueError, IndexError):
            got = None
        if got != [{**s, "color": _hex(s["color"])} for s in exp]:
            problems.append(f"{where}: {name}: {value} doesn't match the SDS effect style")
    elif isinstance(exp, str) and exp.startswith("#"):
        if not re.fullmatch(HEX, value) or _hex(value) != _hex(exp):
            problems.append(f"{where}: {name}: {value} -- SDS has {exp}")
    elif isinstance(exp, str):
        first = value.split(",")[0].strip().strip("\"'")
        if first.lower() != exp.lower():
            problems.append(f"{where}: {name}: {value} -- SDS has {exp}")
    else:
        weight = "weight" in name
        ok = value == str(exp) if weight else value in (f"{exp}px",) + (("0",) if exp == 0 else ())
        if not ok:
            problems.append(f"{where}: {name}: {value} -- SDS has {exp}{'' if weight else 'px'}")


def check_text(html, tokens=None):
    want, line_heights, families = tokens or load_tokens()
    problems = []
    decls = declarations(html)
    root = {p: v for sel, p, v, _ in decls if sel == ":root" and p.startswith("--")}
    defined = {p for _, p, _, _ in decls if p.startswith("--")}

    for sel, prop, value, line in decls:
        where = f"line {line}"
        if prop.startswith("--sds-"):
            if sel != ":root":
                problems.append(f"{where}: {prop} is redefined in {sel}; SDS tokens live in :root only")
            _check_token(prop, value, want, root, where, problems)
        elif prop.startswith("--bl-"):
            extra = [t for t in re.findall(r"[^\s,()]+", _strip_vars(value).lower()) if t not in ALIAS_WORDS
                     and not (re.fullmatch(r"/?\d*\.?\d+", t) and float(t.lstrip("/")) in line_heights)]
            if extra or RAW_COLOR.search(value):
                problems.append(f"{where}: {prop}: {value} -- page names may only combine SDS tokens "
                                f"(found {', '.join(extra) or 'a raw colour'})")
        elif prop.startswith("--"):
            if prop not in LAYOUT_VARS:
                problems.append(f"{where}: {prop} -- SDS tokens are --sds-*, page names --bl-*; "
                                f"layout values are {sorted(LAYOUT_VARS)}")
        else:
            _check_value(prop, value, line_heights, where, problems)
        for used in VAR.findall(value):
            if used not in defined and used not in LAYOUT_VARS:
                problems.append(f"{where}: var({used}) is never defined")

    outside = re.sub(r"<style[^>]*>.*?</style>", lambda m: re.sub(r"[^\n]", " ", m.group()), html, flags=re.S)
    for m in re.finditer(r"""\sstyle=(["'])(.*?)\1""", outside, re.S):
        line = html.count("\n", 0, m.start()) + 1
        for part in _split(m.group(2), ";"):
            if ":" not in part:
                continue
            prop, value = (s.strip() for s in part.split(":", 1))
            prop = prop.lower()
            if prop.startswith("--"):
                if prop not in LAYOUT_VARS:
                    problems.append(f"line {line}: style sets {prop}; only layout values may be set inline")
                continue
            _check_value(prop, re.sub(r"\$\{[^}]*\}", "0", value) if prop not in COLOR_PROPS | COLOR_SHORTHANDS
                         else value, line_heights, f"line {line} (style attribute)", problems, inline=True)
            for used in VAR.findall(value):
                if used not in defined and used not in LAYOUT_VARS:
                    problems.append(f"line {line}: var({used}) is never defined")
    for m in SCRIPT_COLOR.finditer(outside):
        problems.append(f"line {html.count(chr(10), 0, m.start()) + 1}: {m.group()} -- a raw colour in the markup "
                        f"or script; give the element a class that uses an SDS colour token")
    for m in SCRIPT_STYLE.finditer(outside):
        problems.append(f"line {html.count(chr(10), 0, m.start()) + 1}: the script sets {m.group(1)} directly; "
                        f"use a class with an SDS token")
    # A data: URI (the tab icon) can't use var(), so its colours must be SDS values written out
    sds_hex = {_hex(v) for v in want.values() if isinstance(v, str) and v.startswith("#")}
    for m in re.finditer(r"%23([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b", outside):
        if _hex(m.group(1)) not in sds_hex:
            problems.append(f"line {html.count(chr(10), 0, m.start()) + 1}: #{m.group(1)} in a data URI "
                            f"isn't an SDS colour value")
    for m in re.finditer(r"""<link[^>]+href=["']([^"']+)""", html):
        if any(h in m.group(1) for h in SDS_FONT_HOSTS):
            for fam in re.findall(r"family=([^:&;]+)", m.group(1)):
                fam = fam.replace("+", " ")
                if fam not in families:
                    problems.append(f"line {html.count(chr(10), 0, m.start()) + 1}: loads {fam}; "
                                    f"SDS fonts are {', '.join(families)}")
    return sorted(set(problems), key=lambda p: int(re.search(r"\d+", p).group()))


def check(path=PAGE, tokens=None):
    return check_text(pathlib.Path(path).read_text(), tokens)


def main(argv):
    pages = argv or [PAGE]
    bad = 0
    for page in pages:
        problems = check(page)
        bad += len(problems)
        for p in problems:
            print(f"{page}: {p}")
    print(f"{bad} problem{'s' if bad != 1 else ''}" if bad else "Uses only Simple Design System tokens.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
