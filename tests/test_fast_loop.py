"""The fast apply loop: figjs.py writes the Figma code from a card's ops, next.py claims work,
posts replies itself and records how the Figma call went."""
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

from helpers import SCRIPTS

import figjs
import next as nextpy
import bl_state

NEXT = str(SCRIPTS / "next.py")
DATA = {"deck": {"title": "t", "url": "https://www.figma.com/slides/AAAAAAAAAAAAAAAAAAAAAA"}, "cards": [
    {"id": 1, "slide": 1, "kind": "reply", "commentId": "c1", "reply": "Thanks"},
    {"id": 2, "slide": 3, "kind": "change", "commentId": "c2",
     "edit": [{"find": "Keep happy", "after": "Keep satisfied"}],
     "ops": [{"op": "replace", "node": "3:16", "line": 0}, {"op": "set", "node": "3:20", "props": {"opacity": 0.5}}]},
    {"id": 3, "slide": 4, "kind": "pick", "commentId": "c3", "pick": "B",
     "options": [{"key": "A", "ops": []}, {"key": "B", "ops": [{"op": "js", "file": "apply/3.js"}]}]},
    {"id": 4, "slide": 5, "kind": "change", "commentId": "c4", "edit": [{"find": "x", "after": "y"}]},
]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "data.json").write_text(json.dumps(DATA))
        (self.root / "apply").mkdir()
        (self.root / "apply" / "3.js").write_text('const s = await k.node("4:1");\nk.made(figma.createRectangle());')

    def tearDown(self):
        self.tmp.cleanup()

    def acts(self, cards, later=()):
        (self.root / "acts.json").write_text(json.dumps({"n": 20, "cards": cards, "later": list(later), "drafts": []}))

    def prog(self):
        return bl_state.read(self.root / "progress.json")


class FigJs(Base):
    def test_owner_wording_wins_and_claude_wording_is_the_default(self):
        card = DATA["cards"][1]
        self.assertIn('"repl": "Keep satisfied stakeholders"', figjs.ops_for(self.root, card, owner=["Keep satisfied stakeholders"]))
        ops = figjs.ops_for(self.root, card)
        self.assertIn('"find": "Keep happy"', ops)
        self.assertIn('"repl": "Keep satisfied"', ops)

    def test_pick_uses_the_chosen_option_and_custom_code(self):
        self.assertEqual(figjs.ops_for(self.root, DATA["cards"][2], pick="A"), "[]")
        js = figjs.ops_for(self.root, DATA["cards"][2])
        self.assertIn("fn: async (k) => {", js)
        self.assertIn('k.node("4:1")', js)

    def test_a_card_without_ops_is_refused(self):
        with self.assertRaises(figjs.NoOps):
            figjs.ops_for(self.root, DATA["cards"][3])

    def test_calls_are_short_and_check_the_stored_runtime(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": [], "reply": "", "pick": ""}})
        js = figjs.items_js(self.root, figjs.owner_items(self.root, [2], "apply"))
        self.assertIn(f'startsWith("//bl:{figjs.VER}")', js)
        self.assertIn("needLib", js)
        self.assertLess(len(js), 600)
        self.assertNotIn("function revert", js)

    def test_install_stores_the_runtime_without_comments(self):
        js = figjs.install_js()
        self.assertIn(f"//bl:{figjs.VER}", js)
        self.assertIn("function revert", js)
        self.assertNotIn("// Blueline runtime", js)


class Loop(Base):
    def next(self, *extra):
        r = subprocess.run([sys.executable, NEXT, str(self.root), "--once", *extra], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_claim_marks_work_started_and_hands_over_one_script(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": ["Keep them satisfied"], "reply": "", "pick": ""},
                   "3": {"act": "undo", "n": 5},
                   "4": {"act": "approve", "n": 6, "edit": [], "reply": "", "pick": ""}})
        out = self.next("--claim")
        self.assertEqual([w["do"] for w in out["work"]], ["apply", "undo", "apply"])
        self.assertTrue(out["work"][2]["manual"])
        self.assertIn('"repl": "Keep them satisfied"', out["js"])
        self.assertIn('{ id: 3, do: "undo" }', out["js"])
        self.assertNotIn("id: 4", out["js"])
        p = self.prog()["cards"]
        self.assertEqual({k: v["st"] for k, v in p.items()}, {"2": "applying", "3": "undoing", "4": "applying"})
        self.assertEqual(self.next("--claim")["work"], [])          # never handed out twice

    def test_done_records_in_failed_and_undone(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": [], "reply": "", "pick": ""},
                   "3": {"act": "undo", "n": 5},
                   "4": {"act": "approve", "n": 6, "edit": [], "reply": "", "pick": ""}})
        self.next("--claim")
        out = self.next("--done", json.dumps({"2": "ok", "3": "ok", "4": {"ok": False, "why": "doesn't fit"}}))
        p = self.prog()["cards"]
        self.assertEqual((p["2"]["st"], p["3"]["st"], p["4"]["st"], p["4"]["why"]), ("in", "open", "failed", "doesn't fit"))
        self.assertEqual(out["round"], {"done": [2], "with_claude": [], "undecided": [1, 3, 4]})

    def test_need_lib_result_changes_nothing(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": [], "reply": "", "pick": ""}})
        self.next("--claim")
        self.next("--done", '{"needLib": "abc"}')
        self.assertEqual(self.prog()["cards"]["2"]["st"], "applying")


class Posting(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.real = bl_state.post_reply
        bl_state.post_reply = lambda url, cid, text: self.sent.append((cid, text)) or (None if text != "boom" else "Figma said no")
        self.gone, self.real_del = [], bl_state.delete_reply
        bl_state.delete_reply = lambda url, cid, text: self.gone.append((cid, text)) or (None if text != "stuck" else "Figma said no")

    def tearDown(self):
        bl_state.post_reply = self.real
        bl_state.delete_reply = self.real_del
        super().tearDown()

    def test_replies_and_empty_picks_are_handled_without_claude(self):
        self.acts({"1": {"act": "approve", "n": 4, "edit": [], "reply": "Thanks, Dana", "pick": ""},
                   "3": {"act": "approve", "n": 5, "edit": [], "reply": "", "pick": "A"}},
                  later=[{"id": 2, "n": 6, "text": "One more thing"}, {"id": 2, "n": 7, "text": "boom"}])
        nextpy.auto_post(self.root)
        work, rnd = nextpy.look(self.root)
        self.assertEqual(work, [])
        self.assertEqual(self.sent, [("c1", "Thanks, Dana"), ("c2", "One more thing"), ("c2", "boom")])
        p = self.prog()
        self.assertEqual((p["cards"]["1"]["st"], p["cards"]["3"]["st"]), ("posted", "closed"))
        self.assertEqual(p["later"], {"6": {"st": "posted"}, "7": {"st": "failed", "why": "Figma said no"}})

    def test_applied_change_posts_its_reply_first(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": [], "reply": "Done, Marcus", "pick": ""}})
        nextpy.record_done(self.root, {"2": "ok"})
        self.assertEqual(self.sent, [("c2", "Done, Marcus")])
        self.assertEqual(self.prog()["cards"]["2"]["st"], "in")

    def test_a_reply_that_fails_still_shows_the_change_is_in(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": [], "reply": "boom", "pick": ""}})
        nextpy.record_done(self.root, {"2": "ok"})
        st = self.prog()["cards"]["2"]
        self.assertEqual(st["st"], "in")
        self.assertIn("reply didn't post", st["why"])


class Undo(Posting):
    def write(self, name, obj):
        (self.root / name).write_text(json.dumps(obj))

    def test_undo_on_a_posted_reply_takes_it_down_and_reopens_the_card(self):
        self.write("acts.json", {"n": 9, "cards": {"1": {"act": "undo", "n": 9, "reply": "Thanks, Dana"}}, "later": [], "drafts": []})
        self.write("progress.json", {"cards": {"1": {"n": 4, "st": "posted"}}})
        nextpy.auto_post(self.root)
        self.assertEqual(self.gone, [("c1", "Thanks, Dana")])
        self.assertEqual(self.prog()["cards"]["1"]["st"], "open")
        self.assertEqual(nextpy.look(self.root)[0], [], "Claude never sees it")

    def test_a_reply_that_wont_come_down_stays_posted_and_says_why(self):
        self.write("acts.json", {"n": 9, "cards": {"1": {"act": "undo", "n": 9, "reply": "stuck"}}, "later": [], "drafts": []})
        self.write("progress.json", {"cards": {"1": {"n": 4, "st": "posted"}}})
        nextpy.auto_post(self.root)
        st = self.prog()["cards"]["1"]
        self.assertEqual((st["st"], st["n"]), ("posted", 9))
        self.assertIn("Figma said no", st["why"])

    def test_undoing_a_change_also_takes_its_reply_down(self):
        self.write("acts.json", {"n": 9, "cards": {"2": {"act": "undo", "n": 9, "reply": "Done, Marcus"}}, "later": [], "drafts": []})
        work, _ = nextpy.look(self.root)
        self.assertEqual([w["do"] for w in work], ["undo"], "a change still goes through Claude's Figma call")
        nextpy.record_done(self.root, {"2": "ok"})
        self.assertEqual(self.gone, [("c2", "Done, Marcus")])
        self.assertEqual(self.prog()["cards"]["2"]["st"], "open")

    def test_a_failed_undo_leaves_the_reply_alone(self):
        self.write("acts.json", {"n": 9, "cards": {"2": {"act": "undo", "n": 9, "reply": "Done, Marcus"}}, "later": [], "drafts": []})
        nextpy.record_done(self.root, {"2": {"ok": False, "why": "node gone"}})
        self.assertEqual(self.gone, [])
        self.assertEqual(self.prog()["cards"]["2"]["st"], "failed")

    def test_undo_pressed_in_another_window_while_the_change_goes_in(self):
        """The Figma call was for the approval (4). Recording it against the Undo (6) would say "open" and
        delete the reply while the change sits in the deck."""
        self.write("acts.json", {"n": 4, "cards": {"2": {"act": "approve", "n": 4, "edit": [], "reply": "Done, Marcus", "pick": ""}}})
        work, _ = nextpy.look(self.root)
        nextpy.finish(self.root, work, claim=True)
        self.write("acts.json", {"n": 6, "cards": {"2": {"act": "undo", "n": 6, "reply": "Done, Marcus"}}})
        nextpy.record_done(self.root, {"2": "ok"})
        st = self.prog()["cards"]["2"]
        self.assertEqual((st["n"], st["st"]), (4, "in"))
        self.assertEqual((self.sent, self.gone), ([], []), "no reply for a decision the owner has replaced, none deleted")
        work, rnd = nextpy.look(self.root)
        self.assertEqual([(w["do"], w["n"]) for w in work], [("undo", 6)], "the Undo is handed out next")
        self.assertEqual(rnd["with_claude"], [2])

    def test_approved_again_while_the_first_version_is_in(self):
        """Approved, put in, then approved again from a window that still showed it open: the first version
        comes out in the same Figma call and its reply comes down."""
        self.write("acts.json", {"n": 7, "cards": {"2": {"act": "approve", "n": 7, "edit": ["Keep them satisfied"], "reply": "Done!", "pick": ""}}})
        self.write("progress.json", {"cards": {"2": {"n": 4, "st": "in", "reply": "Done"}}})
        work, _ = nextpy.look(self.root)
        self.assertTrue(work[0]["undo_first"])
        js = nextpy.finish(self.root, work, claim=True)
        self.assertLess(js.index('id: 2, do: "undo"'), js.index('id: 2, do: "apply"'))
        nextpy.record_done(self.root, {"2": "ok"})
        self.assertEqual(self.gone, [("c2", "Done")])
        self.assertEqual(self.sent, [("c2", "Done!")])
        self.assertEqual(self.prog()["cards"]["2"]["st"], "in")

    def test_a_reply_posted_again_with_other_words_replaces_the_first(self):
        self.write("acts.json", {"n": 8, "cards": {"1": {"act": "approve", "n": 8, "edit": [], "reply": "Thanks, Dana!", "pick": ""}}})
        self.write("progress.json", {"cards": {"1": {"n": 4, "st": "posted", "reply": "Thanks, Dana"}}})
        nextpy.auto_post(self.root)
        self.assertEqual(self.gone, [("c1", "Thanks, Dana")])
        self.assertEqual(self.sent, [("c1", "Thanks, Dana!")])
        self.assertEqual(self.prog()["cards"]["1"]["reply"], "Thanks, Dana!")

    def test_a_failed_undo_counts_as_done(self):
        """It's still in the deck and the page shows it that way, so the round mustn't wait on the owner."""
        self.write("acts.json", {"n": 9, "cards": {"2": {"act": "undo", "n": 9, "reply": ""}}})
        nextpy.record_done(self.root, {"2": {"ok": False, "why": "node gone"}})
        self.assertEqual(nextpy.look(self.root)[1]["done"], [2])

    def test_undo_on_a_later_reply(self):
        self.write("acts.json", {"n": 12, "cards": {}, "drafts": [],
                                 "later": [{"id": 2, "n": 6, "text": "One more thing"}, {"id": 2, "n": 7, "text": "boom"}],
                                 "unlater": [{"id": 2, "n": 11, "of": 6}, {"id": 2, "n": 12, "of": 7}]})
        self.write("progress.json", {"later": {"6": {"st": "posted"}, "7": {"st": "failed", "why": "Figma said no"}}})
        nextpy.auto_post(self.root)
        self.assertEqual(self.gone, [("c2", "One more thing")], "one that never posted has nothing to take down")
        p = self.prog()
        self.assertEqual(p["later"], {"6": {"st": "removed"}, "7": {"st": "removed"}})
        self.assertEqual(p["unlater"], {"11": {"st": "done"}, "12": {"st": "done"}})
        self.assertEqual(nextpy.look(self.root)[0], [])


class Lock(Base):
    def test_parallel_writers_keep_every_card(self):
        def w(i):
            bl_state.set_card(self.root, i, i, "in")
        ts = [threading.Thread(target=w, args=(i,)) for i in range(40)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.prog()["cards"]), 40)


if __name__ == "__main__":
    unittest.main()
