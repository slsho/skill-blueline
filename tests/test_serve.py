"""The review server: serves the page, passes decisions to Claude, turns away other sites."""
import http.client
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import SCRIPTS


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        (base / "secret.txt").write_text("outside the review folder")
        cls.root = base / "review"
        (cls.root / "img").mkdir(parents=True)
        (cls.root / "index.html").write_text("<!doctype html><title>Blueline</title>")
        (cls.root / "data.json").write_text(json.dumps({"deck": {"title": "T"}, "cards": [{"id": i} for i in range(1, 15)]}))
        (cls.root / "img" / "before-01.png").write_bytes(b"\x89PNG fake")
        cls.port = free_port()
        cls.proc = subprocess.Popen([sys.executable, str(SCRIPTS / "serve.py"), str(cls.root), "--port", str(cls.port)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.communicate(timeout=5)
        cls.tmp.cleanup()

    def setUp(self):
        self.req("POST", "/api/reset", b"{}")
        (self.root / "acts.json").unlink(missing_ok=True)   # reset keeps the running number; each test starts from none

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"localhost:{self.port}"}
        if body is not None and "Content-Type" not in (headers or {}):
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def status(self):
        return json.loads(self.req("GET", "/api/status")[2])

    def test_serves_the_page_and_images_uncached(self):
        code, headers, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Blueline", body)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(self.req("GET", "/img/before-01.png")[0], 200)
        self.assertEqual(self.req("GET", "/data.json")[0], 200)

    def test_never_serves_files_outside_the_folder(self):
        for path in ["/../secret.txt", "/%2e%2e/secret.txt", "/img/../../secret.txt"]:
            code, _, body = self.req("GET", path)
            self.assertNotIn(b"outside the review folder", body, path)

    def test_status_starts_in_review(self):
        self.assertEqual(self.status(), {"phase": "review", "acts": {}, "progress": {}})

    def act(self, body):
        code, _, data = self.req("POST", "/api/act", json.dumps(body).encode(), {"Origin": f"http://localhost:{self.port}"})
        return code, json.loads(data)

    def test_each_decision_is_recorded_with_a_running_number(self):
        self.assertEqual(self.act({"id": 2, "act": "approve", "edit": ["Keep satisfied"], "pick": "", "reply": ""}), (200, {"ok": True, "n": 1}))
        self.assertEqual(self.act({"id": 5, "act": "back", "note": "use teal"})[1]["n"], 2)
        self.assertEqual(self.act({"id": 1, "act": "later", "text": "Thanks again"})[1]["n"], 3)
        self.assertEqual(self.act({"id": 4, "act": "draft", "for": "reply"})[1]["n"], 4)
        acts = json.loads((self.root / "acts.json").read_text())
        self.assertEqual(acts["n"], 4)
        self.assertEqual({k: acts["cards"]["2"][k] for k in ("act", "n", "edit")}, {"act": "approve", "n": 1, "edit": ["Keep satisfied"]})
        self.assertEqual(acts["cards"]["5"]["note"], "use teal")
        self.assertEqual([(x["id"], x["text"]) for x in acts["later"]], [(1, "Thanks again")])
        self.assertEqual([(x["id"], x["for"]) for x in acts["drafts"]], [(4, "reply")])
        self.assertEqual(self.status()["acts"], acts)
        self.assertEqual(list(self.root.glob("*.tmp")), [], "no half-written files left behind")

    def test_a_newer_decision_on_a_card_replaces_the_older_one(self):
        self.act({"id": 2, "act": "approve"})
        self.act({"id": 2, "act": "undo"})
        card = json.loads((self.root / "acts.json").read_text())["cards"]["2"]
        self.assertEqual((card["act"], card["n"]), ("undo", 2))

    def test_undo_carries_the_reply_that_has_to_come_back_out(self):
        self.act({"id": 2, "act": "approve", "edit": [], "pick": "", "reply": "  Done, Marcus. "})
        self.act({"id": 2, "act": "undo"})
        self.assertEqual(json.loads((self.root / "acts.json").read_text())["cards"]["2"]["reply"], "Done, Marcus.")
        self.act({"id": 3, "act": "skip"})
        self.act({"id": 3, "act": "undo"})
        self.assertEqual(json.loads((self.root / "acts.json").read_text())["cards"]["3"]["reply"], "")

    def test_a_later_reply_can_be_taken_back(self):
        n = self.act({"id": 1, "act": "later", "text": "One more thing"})[1]["n"]
        self.assertEqual(self.act({"id": 1, "act": "unlater", "of": n})[0], 200)
        self.assertEqual(self.act({"id": 1, "act": "unlater"})[0], 400)
        self.assertEqual(self.act({"id": 1, "act": "unlater", "of": "2"})[0], 400)
        acts = json.loads((self.root / "acts.json").read_text())
        self.assertEqual([(u["id"], u["of"]) for u in acts["unlater"]], [(1, n)])

    def test_claudes_progress_reaches_the_page(self):
        self.act({"id": 2, "act": "approve"})
        prog = {"cards": {"2": {"n": 1, "st": "in"}}}
        (self.root / "progress.json").write_text(json.dumps(prog))
        self.assertEqual(self.status()["progress"], prog)

    def test_acts_that_make_no_sense_are_refused(self):
        for body in ({"id": 2, "act": "publish"}, {"id": "2", "act": "approve"}, {"id": True, "act": "skip"},
                     {"id": 2, "act": "back", "note": "  "}, {"id": 2, "act": "later", "text": ""},
                     {"id": 2, "act": "draft", "for": "everyone"}, {"id": 2, "act": "approve", "edit": "not a list"},
                     {"id": 2, "act": "approve", "edit": ["x" * 20_001]}):
            self.assertEqual(self.act(body)[0], 400, body)
        self.assertFalse((self.root / "acts.json").exists())

    def test_reset_keeps_counting(self):
        self.act({"id": 2, "act": "approve"})
        self.act({"id": 3, "act": "skip"})
        (self.root / "progress.json").write_text('{"cards": {"2": {"n": 1, "st": "in"}}}')
        self.req("POST", "/api/reset", b"{}")
        self.assertEqual(self.status()["progress"], {})
        self.assertEqual(self.status()["acts"]["cards"], {})
        self.assertEqual(self.act({"id": 2, "act": "approve"})[1]["n"], 3, "a late progress entry for n=1 can't match")

    def test_approve_writes_decisions_for_claude(self):
        decisions = {"approved": [{"id": 5, "edit": [{"after": "the core user"}]}], "sentBack": [], "skipped": [8], "replies": []}
        code, _, body = self.req("POST", "/api/decisions", json.dumps(decisions).encode(),
                                 {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        self.assertEqual(json.loads((self.root / "decisions.json").read_text()), decisions)
        self.assertEqual(self.status()["phase"], "applying")
        self.assertEqual(list(self.root.glob("*.tmp")), [], "no half-written files left behind")

    def test_claude_moves_the_phase_on_with_status_json(self):
        st = {"phase": "applied", "applied": [2, 5], "failed": [{"id": 14, "slide": 2, "why": "text box too narrow"}]}
        (self.root / "status.json").write_text(json.dumps(st))
        self.assertEqual({k: v for k, v in self.status().items() if k in st}, st)

    def test_half_written_status_reads_as_review(self):
        (self.root / "status.json").write_text('{"phase": "app')
        self.assertEqual(self.status(), {"phase": "review", "acts": {}, "progress": {}})

    def test_a_damaged_round_file_is_never_read_as_empty(self):
        """Read as {}, the count would start again at 1 and Claude's progress (higher numbers) would hide new decisions."""
        for name, broken in (("acts.json", '{"n": 7, "ca'), ("acts.json", "[]"), ("progress.json", '{"cards": {')):
            (self.root / name).write_text(broken)
            code, _, body = self.req("GET", "/api/status")
            self.assertEqual(code, 500, name)
            self.assertIn(name, json.loads(body)["error"])
            if name == "acts.json":
                code, body = self.act({"id": 2, "act": "skip"})
                self.assertEqual(code, 500)
                self.assertIn("acts.json", body["error"])
                self.assertEqual((self.root / name).read_text(), broken, "left as it was, for the owner to fix")
            (self.root / name).unlink()

    def test_reset_keeps_counting_past_claudes_progress(self):
        (self.root / "acts.json").write_text('{"n": 3, "ca')
        (self.root / "progress.json").write_text(json.dumps({"cards": {"2": {"n": 9, "st": "in"}}}))
        self.req("POST", "/api/reset", b"{}")
        self.assertEqual(self.act({"id": 2, "act": "skip"})[1]["n"], 10)

    def test_refusals_say_what_to_fix(self):
        code, body = self.act({"id": 2, "act": "approve", "edit": [], "pick": "", "reply": "x" * 20_001})
        self.assertEqual(code, 400)
        self.assertIn("20,001 characters", body["error"])
        code, body = self.act({"id": 2, "act": "approve", "edit": ["ok", "y" * 20_001], "pick": "", "reply": ""})
        self.assertIn("Line 2", body["error"])
        self.assertEqual(self.act({"id": 99, "act": "skip"})[0], 400)
        self.assertEqual(self.req("POST", "/api/act", b"{}", headers={"Content-Length": "-5"})[0], 400)

    def test_post_writes_replies(self):
        replies = {"replies": [{"id": 1, "commentId": "123", "text": "Is it the title?"}]}
        self.assertEqual(self.req("POST", "/api/replies", json.dumps(replies).encode())[0], 200)
        self.assertEqual(json.loads((self.root / "replies.json").read_text()), replies)
        self.assertEqual(self.status()["phase"], "posting")

    def test_reset_clears_the_round(self):
        self.req("POST", "/api/decisions", b'{"approved": []}')
        self.req("POST", "/api/replies", b'{"replies": []}')
        self.assertEqual(self.req("POST", "/api/reset", b"{}")[0], 200)
        for name in ("decisions.json", "replies.json", "status.json", "progress.json"):
            self.assertFalse((self.root / name).exists(), name)
        self.assertEqual(self.status()["phase"], "review")

    def test_bad_requests(self):
        self.assertEqual(self.req("POST", "/api/nope", b"{}")[0], 404)
        self.assertEqual(self.req("POST", "/api/decisions", b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/decisions", b"[1, 2]")[0], 400)
        self.assertEqual(self.req("POST", "/api/decisions", b"{}", {"Content-Length": "abc"})[0], 400)
        # refused from the declared size alone, before reading the body
        self.assertEqual(self.req("POST", "/api/decisions", b"{}", {"Content-Length": "2100000"})[0], 413)
        self.assertFalse((self.root / "decisions.json").exists())

    def test_turns_away_other_websites(self):
        body = b'{"approved": [{"id": 1, "edit": [{"after": "injected"}]}]}'
        # a page on another site posting to us
        self.assertEqual(self.req("POST", "/api/decisions", body, {"Origin": "https://evil.example"})[0], 403)
        # a plain form or text POST, which browsers send cross-site without asking
        self.assertEqual(self.req("POST", "/api/decisions", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/decisions", b"a=1",
                                  {"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        # DNS rebinding: another site's name pointed at 127.0.0.1
        self.assertEqual(self.req("POST", "/api/decisions", body, host=f"evil.example:{self.port}")[0], 403)
        self.assertEqual(self.req("GET", "/data.json", host=f"evil.example:{self.port}")[0], 403)
        self.assertFalse((self.root / "decisions.json").exists())
        # the same for the one-card-at-a-time endpoint
        approve = b'{"id": 2, "act": "approve"}'
        self.assertEqual(self.req("POST", "/api/act", approve, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/act", approve, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/act", approve, host=f"evil.example:{self.port}")[0], 403)
        self.assertFalse((self.root / "acts.json").exists())

    def test_accepts_its_own_origin_on_either_name(self):
        for name in ("localhost", "127.0.0.1"):
            code = self.req("POST", "/api/replies", b'{"replies": []}', {"Origin": f"http://{name}:{self.port}"},
                            host=f"{name}:{self.port}")[0]
            self.assertEqual(code, 200, name)

    def test_only_listens_on_this_computer(self):
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
        self.assertIn(f"127.0.0.1:{self.port}", out)
        self.assertNotIn(f"*:{self.port}", out)


class Refuses(unittest.TestCase):
    def test_needs_index_and_data(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(SCRIPTS / "serve.py"), d, "--port", str(free_port())],
                               capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs index.html and data.json", r.stderr)


if __name__ == "__main__":
    unittest.main()
