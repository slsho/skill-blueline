"""The review page uses Figma's Simple Design System (SDS) and nothing else.

check_design_system.py holds the rules; these tests run it on the page and prove it catches each
kind of slip, so anything new added to the page has to be built from SDS tokens too.
"""
import contextlib
import io
import json
import unittest

from helpers import DOCS, SKILL

import check_design_system as ds

TOKENS = SKILL / "references" / "sds-tokens.json"

# A tiny page with a few real SDS tokens, for trying the checker on one snippet at a time
ROOT = """:root { --sds-color-text-default-default: #ffffff; --sds-size-space-200: 8px;
  --sds-typography-body-size-small: 14px; --sds-typography-body-font-weight-regular: 400;
  --sds-typography-body-font-family: "Inter", sans-serif; --sds-size-radius-full: 9999px;
  --sds-size-stroke-border: 1px; --sds-color-border-default-default: #444444; }"""


def page(css="", body=""):
    return f"<html><head><style>{ROOT}\n{css}</style></head><body>{body}</body></html>"


class ReviewPage(unittest.TestCase):
    def test_review_page_uses_only_sds_tokens(self):
        problems = ds.check(SKILL / "app" / "index.html")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_demo_page_is_the_same_page(self):
        self.assertEqual(ds.check(DOCS / "index.html"), [])

    def test_page_defines_real_sds_tokens(self):
        html = (SKILL / "app" / "index.html").read_text()
        names = {p for sel, p, _, _ in ds.declarations(html) if sel == ":root" and p.startswith("--sds-")}
        self.assertGreater(len(names), 40)
        want, _, _ = ds.load_tokens()
        self.assertFalse(names - set(want), "every --sds-* name on the page exists in SDS")


class ReplyBubbles(unittest.TestCase):
    """Two slips found by eye in the SDS restyle, kept from coming back."""

    def rule(self, selector):
        """Every declaration for a selector, including rules that list it among others."""
        html = (SKILL / "app" / "index.html").read_text()
        found = {p: v for sel, p, v, _ in ds.declarations(html) if selector in (s.strip() for s in sel.split(","))}
        self.assertTrue(found, f"no rule for {selector}")
        return found

    def test_your_avatar_stands_out_on_your_bubble(self):
        self.assertNotEqual(self.rule(".av.me")["background"], self.rule(".thread.me")["background"])

    def test_a_later_reply_stays_inside_its_column(self):
        # a 32px left margin plus width: 100% pushed the bubble past the card's edge
        for sel in (".act > .thread.me.later", ".act > .thread.me.later-box"):
            self.assertNotIn("width", self.rule(sel))
            self.assertEqual(self.rule(sel)["justify-self"], "stretch")
        self.assertIn("var(--sds-size-space-800)", self.rule(".thread.me")["margin"])

    def test_every_reply_box_is_the_same_box(self):
        # A reply on an open card and a reply to a done comment use one design; the done one adds a send button
        html = (SKILL / "app" / "index.html").read_text()
        self.assertIn('<div class="thread me later-box"><div class="msg out writing">', html)
        self.assertIn('<div class="composer sends${empty ? " empty" : ""}"><textarea class="field"', html)
        self.assertNotRegex(html, r"rb-field|rb-send|class=\"rbar")


class TokenFile(unittest.TestCase):
    def test_token_file_covers_the_sds_collections(self):
        data = json.loads(TOKENS.read_text())
        self.assertIn("figma.com/design/ABQ7Itjmi2WuD6YJ3suoOE", data["source"]["figma"])
        self.assertEqual(data["source"]["page_mode"], "dark")
        self.assertEqual(len(data["color"]), 136)
        self.assertEqual(len(data["primitive"]), 100)
        self.assertEqual(data["size"]["--sds-size-space-200"]["value"], 8)
        self.assertEqual(data["color"]["--sds-color-background-default-default"]["dark"], "#1e1e1e")
        self.assertEqual(data["typography"]["--sds-typography-body-font-family"]["value"], "Inter")
        for v in data["color"].values():
            self.assertRegex(v["light"] + v["dark"], r"^(#[0-9a-f]{6}([0-9a-f]{2})?){2}$")

    def test_blue_ramp_has_its_own_names(self):
        # In the Figma file the Blue primitives carry Pink's code names; here they must not collide
        prim = json.loads(TOKENS.read_text())["primitive"]
        self.assertEqual(prim["--sds-color-blue-500"]["value"], "#3f81ea")
        self.assertEqual(prim["--sds-color-pink-500"]["value"], "#ea3fb8")


class Checker(unittest.TestCase):
    def test_tokens_pass(self):
        css = """.a { color: var(--sds-color-text-default-default); padding: var(--sds-size-space-200) 0;
                 font: var(--sds-typography-body-font-weight-regular) var(--sds-typography-body-size-small)/1.4
                 var(--sds-typography-body-font-family); border-radius: var(--sds-size-radius-full);
                 border: var(--sds-size-stroke-border) solid var(--sds-color-border-default-default);
                 width: 196px; transform: translateY(1px); line-height: 1; }
                 :root { --bl-ring: 0 0 0 var(--sds-size-stroke-border) var(--sds-color-border-default-default); }"""
        self.assertEqual(ds.check_text(page(css, '<i style="left:${x}%;--nudge:${n}px"></i>')), [])

    def test_each_kind_of_slip_is_caught(self):
        slips = {
            "hex colour": page(".a { color: #4c8dff; }"),
            "rgba colour": page(".a { box-shadow: 0 0 0 var(--sds-size-stroke-border) rgba(255, 255, 255, .1); }"),
            "named colour": page(".a { color: white; }"),
            "colour in a data URI": page(".a { background: url(\"data:image/svg+xml,%3Csvg fill='%23123456'/%3E\"); }"),
            "off-scale padding": page(".a { padding: 10px; }"),
            "on-scale number instead of the token": page(".a { gap: 8px; }"),
            "font size as a number": page(".a { font-size: 15px; }"),
            "weight as a number": page(".a { font-weight: 500; }"),
            "font shorthand with numbers": page(".a { font: 500 13px/1.4 var(--sds-typography-body-font-family); }"),
            "line height not in SDS": page(".a { line-height: 1.55; }"),
            "letter spacing": page(".a { letter-spacing: .02em; }"),
            "radius as a percentage": page(".a { border-radius: 50%; }"),
            "raw shadow": page(".a { box-shadow: 0 6px 16px var(--sds-color-border-default-default); }"),
            "wrong token value": page(":root { --sds-size-space-300: 10px; }"),
            "made-up token": page(":root { --sds-size-space-250: 10px; }"),
            "token redefined outside :root": page(".a { --sds-size-space-200: 8px; }"),
            "old-style variable": page(":root { --pencil: var(--sds-color-text-default-default); }"),
            "page name with a raw value": page(":root { --bl-x: 0 0 0 3px var(--sds-color-text-default-default); }"),
            "undefined var": page(".a { color: var(--sds-color-text-brand-on-brand); }"),
            "inline colour": page(body='<span style="background:#5B8DEF"></span>'),
            "colour set from the script": page(body='<script>x = `<b style="background:${AV[n]}">`;</script>'),
            "colour in the script": page(body='<script>const AV = ["#5B8DEF", "#E07A5F"];</script>'),
            "script sets a colour": page(body="<script>el.style.background = c;</script>"),
            "font outside SDS": '<link href="https://fonts.googleapis.com/css2?family=Lora:wght@400">' + page(),
        }
        for what, html in slips.items():
            with self.subTest(what):
                self.assertTrue(ds.check_text(html), f"missed: {what}")

    def test_problems_name_the_line(self):
        html = page("\n\n.a { padding: 10px; }")
        line = html[:html.index(".a {")].count("\n") + 1
        [problem] = ds.check_text(html)
        self.assertTrue(problem.startswith(f"line {line}:"), problem)
        self.assertIn("10px", problem)

    def test_wrong_token_value_names_the_sds_value(self):
        [problem] = ds.check_text(page(":root { --sds-color-background-default-default: #1d1d1f; }"))
        self.assertIn("#1e1e1e", problem)

    def test_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ds.main([str(SKILL / "app" / "index.html")]), 0)
        self.assertIn("Uses only Simple Design System tokens", out.getvalue())


if __name__ == "__main__":
    unittest.main()
