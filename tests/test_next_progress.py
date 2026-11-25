"""next.py hands Claude each decision once; progress.py reports back against the right decision."""
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from helpers import SCRIPTS

NEXT, PROGRESS = str(SCRIPTS / "next.py"), str(SCRIPTS / "progress.py")
DATA = {"cards": [
    {"id": 1, "slide": 1, "kind": "reply", "where": "Slide 1", "who": "dana", "commentId": "c1", "reply": "Thanks"},
    {"id": 2, "slide": 3, "kind": "change", "where": "Slides 3 and 5", "who": "marcus", "commentId": "c2",
     "edit": [{"find": "Keep happy", "after": "Keep satisfied"}], "apply": "Slide 5 label"},
    {"id": 3, "slide": 4, "kind": "pick", "where": "Slides 3 and 4", "who": "tom", "commentId": "c3", "pick": "B", "apply": "A or B"},
]}


class Round(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "data.json").write_text(json.dumps(DATA))

    def tearDown(self):
        self.tmp.cleanup()

    def acts(self, cards=None, later=(), drafts=(), n=None):
        a = {"n": n or 10, "cards": cards or {}, "later": list(later), "drafts": list(drafts)}
        (self.root / "acts.json").write_text(json.dumps(a))

    def next(self, *extra, timeout=10):
        r = subprocess.run([sys.executable, NEXT, str(self.root), *extra], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None), r.stderr

    def progress(self, *args, stdin=None):
        return subprocess.run([sys.executable, PROGRESS, str(self.root), *args], capture_output=True, text=True,
                              input=stdin, timeout=10)

    def test_nothing_to_do_yet(self):
        code, out, _ = self.next("--once")
        self.assertEqual(code, 0)
        self.assertEqual(out, {"work": [], "round": {"done": [], "with_claude": [], "undecided": [1, 2, 3]}})

    def test_hands_over_the_owners_wording_and_pick(self):
        self.acts({"2": {"act": "approve", "n": 4, "edit": ["Keep satisfied stakeholders"], "reply": "Done, Marcus.", "pick": ""},
                   "3": {"act": "approve", "n": 5, "edit": [], "reply": "", "pick": "A"},
                   "1": {"act": "skip", "n": 3}})
        _, out, _ = self.next("--once")
        two, three = out["work"]
        self.assertEqual((two["do"], two["id"], two["n"], two["commentId"], two["reply"]), ("apply", 2, 4, "c2", "Done, Marcus."))
        self.assertEqual(two["edit"], [{"find": "Keep happy", "after": "Keep satisfied stakeholders", "claude": "Keep satisfied"}])
        self.assertEqual((three["id"], three["pick"]), (3, "A"))
        self.assertEqual(out["round"], {"done": [1], "with_claude": [2, 3], "undecided": []})

    def test_each_item_is_handed_out_once(self):
        self.acts({"2": {"act": "approve", "n": 4}})
        self.assertEqual(self.progress("card", "2", "applying").returncode, 0)
        _, out, _ = self.next("--once")
        self.assertEqual(out["work"], [])
        self.assertEqual(out["round"]["with_claude"], [2])
        self.progress("card", "2", "in")
        self.assertEqual(self.next("--once")[1]["round"]["done"], [2])

    def test_a_newer_decision_is_new_work(self):
        self.acts({"2": {"act": "approve", "n": 4}})
        self.progress("card", "2", "in")
        self.acts({"2": {"act": "undo", "n": 7}})
        _, out, _ = self.next("--once")
        self.assertEqual([(w["do"], w["n"]) for w in out["work"]], [("undo", 7)])
        self.progress("card", "2", "undoing")
        self.progress("card", "2", "open")
        self.assertEqual(self.next("--once")[1]["round"]["undecided"], [1, 2, 3])

    def test_sent_back_failed_and_redone(self):
        self.acts({"2": {"act": "back", "n": 4, "note": "use teal"}})
        _, out, _ = self.next("--once")
        self.assertEqual((out["work"][0]["do"], out["work"][0]["note"]), ("redo", "use teal"))
        self.progress("card", "2", "revising")
        self.progress("card", "2", "open")
        self.assertIn(2, self.next("--once")[1]["round"]["undecided"])
        self.acts({"2": {"act": "approve", "n": 5}})
        r = self.progress("card", "2", "failed", "--why", "text box too narrow")
        self.assertEqual(r.returncode, 0, r.stderr)
        prog = json.loads((self.root / "progress.json").read_text())
        self.assertEqual({k: prog["cards"]["2"][k] for k in ("n", "st", "why")}, {"n": 5, "st": "failed", "why": "text box too narrow"})
        self.assertIn(2, self.next("--once")[1]["round"]["undecided"], "a failed card is the owner's to decide again")

    def test_states_must_fit_what_the_owner_asked(self):
        self.acts({"2": {"act": "back", "n": 4, "note": "x"}})
        r = self.progress("card", "2", "in")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("revising", r.stderr)
        self.assertNotEqual(self.progress("card", "3", "in").returncode, 0, "no decision on card 3 yet")
        self.assertFalse((self.root / "progress.json").exists())

    def test_late_replies_and_drafts(self):
        self.acts(later=[{"id": 1, "n": 6, "text": "One more thing"}], drafts=[{"id": 2, "n": 7, "for": "reply"}])
        _, out, _ = self.next("--once")
        self.assertEqual([(w["do"], w["n"]) for w in out["work"]], [("post", 6), ("draft", 7)])
        self.assertEqual((out["work"][0]["text"], out["work"][0]["commentId"]), ("One more thing", "c1"))
        self.assertEqual(self.progress("later", "6", "posted").returncode, 0)
        r = self.progress("draft", "7", "--text", "-", stdin="It's done, Marcus. \"Keep satisfied\" everywhere.\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        prog = json.loads((self.root / "progress.json").read_text())
        self.assertEqual(prog["drafts"]["7"]["text"], "It's done, Marcus. \"Keep satisfied\" everywhere.")
        self.assertEqual(self.next("--once")[1]["work"], [])
        self.assertNotEqual(self.progress("later", "99", "posted").returncode, 0)

    def test_undo_of_a_posted_reply_is_handed_out_as_unpost(self):
        a = {"n": 12, "cards": {"1": {"act": "undo", "n": 9, "reply": "Thanks, Dana"}},
             "later": [{"id": 2, "n": 6, "text": "One more thing"}], "drafts": [], "unlater": [{"id": 2, "n": 11, "of": 6}]}
        (self.root / "acts.json").write_text(json.dumps(a))
        (self.root / "progress.json").write_text(json.dumps({"cards": {"1": {"n": 4, "st": "posted"}}, "later": {"6": {"st": "posted"}}}))
        _, out, _ = self.next("--once")
        self.assertEqual([(w["do"], w["id"], w["text"]) for w in out["work"]], [("unpost", 1, "Thanks, Dana"), ("unpost", 2, "One more thing")])
        self.assertEqual(self.progress("card", "1", "unposting").returncode, 0)
        self.assertEqual(self.progress("card", "1", "open").returncode, 0)
        self.assertEqual(self.progress("unlater", "11", "done").returncode, 0)
        prog = json.loads((self.root / "progress.json").read_text())
        self.assertEqual(prog["later"]["6"], {"st": "removed"})
        self.assertEqual(self.next("--once")[1]["work"], [])
        self.assertNotEqual(self.progress("unlater", "99", "done").returncode, 0)

    def test_waits_until_the_owner_decides(self):
        threading.Timer(0.8, lambda: self.acts({"1": {"act": "approve", "n": 1, "reply": "Thanks"}})).start()
        t = time.monotonic()
        code, out, err = self.next("--timeout", "20", timeout=30)
        took = time.monotonic() - t
        self.assertEqual(code, 0, err)
        self.assertEqual(out["work"][0]["id"], 1)
        self.assertGreater(took, 0.7)
        self.assertLess(took, 5)

    def test_times_out_with_code_2(self):
        code, _, err = self.next("--timeout", "1")
        self.assertEqual(code, 2)
        self.assertIn("Timed out", err)

    def test_a_damaged_round_file_stops_the_loop(self):
        """Never read as empty: an empty progress.json would hand every finished card out again."""
        self.acts({"2": {"act": "approve", "n": 4}})
        for name, broken in (("progress.json", '{"cards": {"2": {"n": 4, "st": "in"}'), ("progress.json", "[]"), ("acts.json", '{"n": 3, "car')):
            (self.root / name).write_text(broken)
            code, out, err = self.next("--once")
            self.assertNotEqual(code, 0, name)
            self.assertIsNone(out)
            self.assertIn(name, err)
            r = self.progress("card", "2", "in")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((self.root / name).read_text(), broken, "nothing written over it")
            self.acts({"2": {"act": "approve", "n": 4}}) if name == "acts.json" else (self.root / name).unlink()

    def test_progress_for_the_decision_claude_worked_on(self):
        """A second window replaced decision 4 with 6 while Claude worked on 4: 4 is done, 6 is still to do."""
        self.acts({"2": {"act": "undo", "n": 6, "reply": ""}})
        r = self.progress("card", "2", "in", "--n", "4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((self.root / "progress.json").read_text())["cards"]["2"]["n"], 4)
        _, out, _ = self.next("--once")
        self.assertEqual([(w["do"], w["n"]) for w in out["work"]], [("undo", 6)])
        self.assertNotEqual(self.progress("card", "2", "in", "--n", "7").returncode, 0, "no decision 7 yet")


if __name__ == "__main__":
    unittest.main()
