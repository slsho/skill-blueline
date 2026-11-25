"""docs/data.json follows the card format, and the demo page is the same page the skill ships."""
import json
import re
import unittest

from helpers import DOCS, SKILL

DATA = json.loads((DOCS / "data.json").read_text())
CARDS = DATA["cards"]
KINDS = {"change", "reply", "pick"}


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


class Cards(unittest.TestCase):
    def test_the_trimmed_round(self):
        self.assertEqual(sorted(c["id"] for c in CARDS), [1, 2, 4, 5, 8, 10, 14])
        self.assertEqual({c["who"] for c in CARDS} - {"owner"}, {"dana", "marcus", "priya", "tom"}, "all four reviewers")

    def test_ids_unique_and_kinds_known(self):
        ids = [c["id"] for c in CARDS]
        self.assertEqual(len(ids), len(set(ids)))
        for c in CARDS:
            self.assertIn(c["kind"], KINDS, c["id"])

    def test_required_fields(self):
        for c in CARDS:
            for f in ("id", "slide", "kind", "where", "who", "comment"):
                self.assertIn(f, c, (c["id"], f))
            self.assertIn(c["who"], DATA["people"], c["id"])
            if c.get("replyFrom"):
                self.assertIn(c["replyFrom"][0], DATA["people"])
            if c["kind"] == "change":
                self.assertTrue(c.get("shots") and c.get("change"), c["id"])
            if c["kind"] == "reply":
                self.assertTrue(c.get("image") and c.get("reply"), c["id"])
            if c["kind"] == "pick":
                self.assertTrue(c.get("range") and c.get("options") and c.get("pick"), c["id"])

    def test_every_image_exists(self):
        names = set()
        for c in CARDS:
            for s in c.get("shots", []):
                names.update(s[:2])
            if c.get("image"):
                names.add(c["image"])
            names.update(r[1] for r in c.get("range", []))
            for o in c.get("options", []):
                names.update((o.get("images") or {}).values())
        missing = sorted(n for n in names if not (DOCS / "img" / f"{n}.png").exists())
        self.assertEqual(missing, [])
        extra = sorted(p.stem for p in (DOCS / "img").glob("*.png") if p.stem not in names)
        self.assertEqual(extra, [], "images no card uses")

    def test_every_image_is_16_by_9(self):
        # The page shows each image in a 16:9 frame (object-fit: cover). Any other shape gets cut,
        # and its outlines no longer line up with what's on screen.
        from PIL import Image
        for f in sorted((DOCS / "img").glob("*.png")):
            w, h = Image.open(f).size
            self.assertAlmostEqual(w / h, 16 / 9, delta=0.01, msg=f"{f.name} is {w}x{h}")

    def test_outline_boxes(self):
        afters = {s[1] for c in CARDS for s in c.get("shots", [])}
        self.assertEqual(set(DATA["highlights"]), afters, "every proposed slide has outlines, and only those")
        for name, bs in DATA["highlights"].items():
            for x, y, w, h, *_ in bs:
                self.assertTrue(0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= 100.1 and y + h <= 100.1, name)

    def test_editable_lines_match_the_redline(self):
        for c in CARDS:
            if c.get("edit"):
                self.assertEqual(len(c["edit"]), len(c.get("redline", [])), c["id"])
                for e in c["edit"]:
                    self.assertTrue(e["after"].strip())

    def test_pick_options(self):
        for c in (c for c in CARDS if c["kind"] == "pick"):
            keys = [o["key"] for o in c["options"]]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertIn(c["pick"], keys)
            slides = {r[0] for r in c["range"]}
            self.assertEqual(c["slide"], c["range"][0][0])
            for o in c["options"]:
                self.assertLessEqual({int(k) for k in o.get("strip", {})}, slides, (c["id"], o["key"]))
                self.assertLessEqual(set(o.get("strip", {}).values()), {"", "cut", "fold", "plus"})
                # an option can show its own after-slides, so the owner sees what it adds before approving
                self.assertLessEqual({int(k) for k in o.get("images", {})}, slides, (c["id"], o["key"]))

    def test_also_on_points_at_other_slides(self):
        for c in CARDS:
            for n in c.get("alsoOn", []):
                self.assertIsInstance(n, int)
                self.assertNotEqual(n, c["slide"])

    def test_option_that_adds_content_shows_it(self):
        pick = next(c for c in CARDS if c["kind"] == "pick")
        c_opt = next(o for o in pick["options"] if o["key"] == "C")
        plus = sorted(int(k) for k, v in c_opt["strip"].items() if v == "plus")
        self.assertEqual(sorted(int(k) for k in c_opt["images"]), plus)

    def test_circle_count_matches_the_real_run(self):
        card = next(c for c in CARDS if c["id"] == 14)
        self.assertIn("36 circles on 10 slides", card["change"])
        self.assertEqual(len(card["alsoOn"]) + 1, 10)

    def test_applies_say_what_to_change(self):
        for c in CARDS:
            if c["kind"] != "reply":
                self.assertTrue(c.get("apply"), f"card {c['id']} has no apply note for Claude")

    def test_no_em_dashes(self):
        bad = [s for s in strings(DATA) if "—" in s]
        self.assertEqual(bad, [])

    def test_no_markup_outside_the_redline(self):
        for c in CARDS:
            for f in ("comment", "change", "why", "reply", "where"):
                self.assertNotRegex(c.get(f, ""), r"<\s*script", (c["id"], f))


class Page(unittest.TestCase):
    def test_demo_page_is_the_shipped_page(self):
        self.assertEqual((DOCS / "index.html").read_text(), (SKILL / "app" / "index.html").read_text(),
                         "copy skills/blueline/app/index.html to docs/index.html")

    def test_demo_is_marked_so_the_page_never_calls_a_server(self):
        self.assertIs(DATA.get("demo"), True)
        self.assertIn("if (!DATA.demo)", (SKILL / "app" / "index.html").read_text())

    def test_demo_says_it_sends_nothing(self):
        html = (SKILL / "app" / "index.html").read_text()
        self.assertIn("Demo: nothing is sent to Figma.", html)
        self.assertIn("${LIVE ? \"\" : `<p class=\"tally\">Demo: nothing is sent to Figma.</p>`}", html)

    def test_no_claim_that_threads_get_resolved(self):
        self.assertNotRegex((SKILL / "app" / "index.html").read_text(), r"(?i)threads? (is|are) resolved")

    def test_page_posts_json_only(self):
        html = (SKILL / "app" / "index.html").read_text()
        calls = list(re.finditer(r'fetch\("api/(act|reset)"[^)]*\)', html))
        self.assertEqual(sorted({m.group(1) for m in calls}), ["act", "reset"])
        for m in calls:
            self.assertIn('"Content-Type": "application/json"', m.group(0), m.group(0))

    def test_replies_on_change_requests_wait_until_the_owner_wants_one(self):
        html = (SKILL / "app" / "index.html").read_text()
        self.assertIn('const startReply = c => c.kind === "reply" ? (c.reply || "") : "";', html)
        self.assertIn("const replyOf = c => S.replies[c.id] ?? startReply(c);", html)

    def test_several_comments_on_a_slide_share_one_picture(self):
        html = (SKILL / "app" / "index.html").read_text()
        self.assertIn("function comboHTML(c, sp)", html)
        self.assertIn('const pin = e.target.closest("[data-pin]");', html)
        for n, sp in (DATA.get("stops") or {}).items():
            for img in (sp["before"], sp["after"]):
                self.assertTrue((DOCS / "img" / f"{img}.png").exists(), img)
            self.assertLessEqual({int(k) for k in sp["pins"]}, {c["id"] for c in CARDS}, n)

    def test_every_reply_starts_as_a_button_and_can_be_closed(self):
        html = (SKILL / "app" / "index.html").read_text()
        self.assertIn('data-act="openlater"', html, "a done card offers Reply to ..., not an open box")
        self.assertIn('data-act="closelater"', html)
        self.assertIn('aria-label="Close the reply to', html)
        self.assertIn('j === "r" && e.key === "Escape"', html, "Escape closes an empty reply box")
        # approving doesn't take the reply away: it stays while Claude works and after
        self.assertIn('const canReplyLater = c => !isOpen(c) && !["back", "revising"].includes(stOf(c));', html)

    def test_blue_dashes_only_mark_changes_on_the_slide(self):
        html = (SKILL / "app" / "index.html").read_text()
        dashed = [l.split("{")[0].strip() for l in html.splitlines() if "dashed var(--bl-change)" in l]
        self.assertEqual(dashed, [".stage .hl", ".six .hl"])

    def test_every_decision_goes_to_the_server_when_live(self):
        html = (SKILL / "app" / "index.html").read_text()
        for act in ("approve", "close", "back", "skip", "later", "draft"):
            self.assertIn(f'act: "{act}"', html, act)
        self.assertIn('act: back ? "undo" : "reopen"', html)
        self.assertIn('act: "unlater", of: k', html, "Undo on a later reply reaches Claude")

    def test_every_finished_card_and_reply_can_be_undone(self):
        html = (SKILL / "app" / "index.html").read_text()
        for st in ("in", "posted", "closed"):
            line = next(l for l in html.splitlines() if l.strip().startswith(f'if (st === "{st}") return'))
            self.assertIn("${undo}", line, st)
        self.assertIn("${undo}", next(l for l in html.splitlines() if 'if (st === "skip") return' in l))
        self.assertIn('data-act="unlater"', html)
        self.assertRegex(html, r"if \(LIVE\) setInterval\(poll, \d+\)", "every open copy keeps itself up to date")


if __name__ == "__main__":
    unittest.main()
