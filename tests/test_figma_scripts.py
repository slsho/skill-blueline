"""fetch_comments.py, post_replies.py and figma_api.py, with Figma's API faked."""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

import helpers  # noqa: F401  (puts the scripts on sys.path)
import fetch_comments
import figma_api
import post_replies

KEY = "cqs7vy27OXzMuKe0jLWlpu"


@contextlib.contextmanager
def argv(*args):
    old = sys.argv
    sys.argv = ["x", *args]
    try:
        yield
    finally:
        sys.argv = old


@contextlib.contextmanager
def cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run(fn, *args):
    out = io.StringIO()
    with argv(*args), contextlib.redirect_stdout(out):
        fn()
    return out.getvalue()


class FileKey(unittest.TestCase):
    def test_urls_and_bare_keys(self):
        for url in [f"https://www.figma.com/slides/{KEY}",
                    f"https://www.figma.com/slides/{KEY}/Ecosystem-mapping?node-id=0-20",
                    f"https://figma.com/design/{KEY}/x", f"https://www.figma.com/deck/{KEY}", KEY]:
            self.assertEqual(figma_api.file_key(url), KEY, url)

    def test_rejects_junk(self):
        for bad in ["", "https://example.com/slides/abc", "short", "https://www.figma.com/slides/"]:
            with self.assertRaises(SystemExit):
                figma_api.file_key(bad)


class Token(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("FIGMA_TOKEN", None)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_from_environment_first(self):
        os.environ["FIGMA_TOKEN"] = " figd_env "
        pathlib.Path(self.tmp.name, ".env").write_text("FIGMA_TOKEN=figd_file\n")
        with cwd(self.tmp.name):
            self.assertEqual(figma_api.token(), "figd_env")

    def test_from_dotenv_with_quotes(self):
        pathlib.Path(self.tmp.name, ".env").write_text('# comment\nOTHER=1\nFIGMA_TOKEN="figd_quoted"\n')
        with cwd(self.tmp.name):
            self.assertEqual(figma_api.token(), "figd_quoted")

    def test_missing_token_explains_and_never_echoes(self):
        pathlib.Path(self.tmp.name, ".env").write_text("FIGMA_TOKEN=\n")
        with cwd(self.tmp.name), self.assertRaises(SystemExit) as e:
            figma_api.token()
        self.assertIn(".env.example", str(e.exception.code))


class Call(unittest.TestCase):
    def test_sends_token_header_method_and_json(self):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            seen.update(url=req.full_url, method=req.get_method(), token=req.get_header("X-figma-token"),
                        body=json.loads(req.data), ctype=req.get_header("Content-type"))
            return Resp(b'{"id": "99"}')

        with mock.patch.dict(os.environ, {"FIGMA_TOKEN": "figd_test"}), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            out = figma_api.call("POST", f"/files/{KEY}/comments", {"message": "hi", "comment_id": "1"})
        self.assertEqual(out, {"id": "99"})
        self.assertEqual(seen["url"], f"https://api.figma.com/v1/files/{KEY}/comments")
        self.assertEqual((seen["method"], seen["token"], seen["ctype"]), ("POST", "figd_test", "application/json"))
        self.assertEqual(seen["body"], {"message": "hi", "comment_id": "1"})

    def test_http_error_exits_with_status(self):
        def boom(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"err":"Invalid token"}'))

        with mock.patch.dict(os.environ, {"FIGMA_TOKEN": "figd_secret_value"}), \
                mock.patch("urllib.request.urlopen", boom), self.assertRaises(SystemExit) as e:
            figma_api.call("GET", "/files/x/comments")
        self.assertIn("403", str(e.exception.code))
        self.assertNotIn("figd_secret_value", str(e.exception.code))


COMMENTS = {"comments": [
    {"id": "3", "message": "Tom Becker (Research & Insights): Where does \"aim for 20 or more\" come from?",
     "created_at": "t6", "client_meta": {"node_id": "0:30", "node_offset": {"x": 1, "y": 2}},
     "user": {"handle": "Deck Owner"}},
    {"id": "1", "message": "Dana Whitfield (Head of Strategy): Hmm, not loving the cover. Can we rethink?",
     "created_at": "t2", "client_meta": {"node_id": "0:20"}, "user": {"handle": "Deck Owner"}},
    {"id": "2", "message": "Tom Becker (Research & Insights): Slides 5-9 repeat what's already on this overview.",
     "created_at": "t3", "client_meta": {"node_id": "0:24"}, "user": {"handle": "Deck Owner"}},
    {"id": "2b", "parent_id": "2", "created_at": "t5", "user": {"handle": "Deck Owner"},
     "message": "Dana Whitfield (Head of Strategy): Disagree. The Why slide is the one leaders actually read."},
    {"id": "2a", "parent_id": "2", "created_at": "t4", "user": {"handle": "Deck Owner"},
     "message": "first reply, no prefix"},
    {"id": "4", "message": "all number spacing is off", "created_at": "t7",
     "user": {"handle": "Deck Owner"}},
    {"id": "5", "message": "Old, already handled", "created_at": "t1", "resolved_at": "t1",
     "client_meta": {"node_id": "0:22"}, "user": {"handle": "Deck Owner"}},
]}


class FetchComments(unittest.TestCase):
    def fetch(self, *flags):
        with mock.patch.object(fetch_comments, "call", return_value=COMMENTS) as call:
            out = json.loads(run(fetch_comments.main, f"https://www.figma.com/slides/{KEY}", *flags))
        call.assert_called_once_with("GET", f"/files/{KEY}/comments")
        return out

    def test_threads_in_time_order_without_resolved(self):
        out = self.fetch()
        self.assertEqual(out["file_key"], KEY)
        self.assertEqual([t["id"] for t in out["threads"]], ["1", "2", "3", "4"])

    def test_all_includes_resolved(self):
        self.assertEqual([t["id"] for t in self.fetch("--all")["threads"]], ["5", "1", "2", "3", "4"])

    def test_name_prefix_becomes_the_author(self):
        t = self.fetch()["threads"][0]
        self.assertEqual(t["author"], {"name": "Dana Whitfield", "role": "Head of Strategy"})
        self.assertEqual(t["message"], "Hmm, not loving the cover. Can we rethink?")
        self.assertEqual(t["node_id"], "0:20")

    def test_plain_comment_keeps_figma_user(self):
        t = self.fetch()["threads"][3]
        self.assertEqual(t["author"], {"name": "Deck Owner", "role": ""})
        self.assertEqual(t["message"], "all number spacing is off")
        self.assertIsNone(t["node_id"])

    def test_replies_grouped_and_in_order(self):
        t = self.fetch()["threads"][1]
        self.assertEqual([r["message"] for r in t["replies"]],
                         ["first reply, no prefix", "Disagree. The Why slide is the one leaders actually read."])
        self.assertEqual(t["replies"][1]["author"]["name"], "Dana Whitfield")

    def test_no_url_prints_usage(self):
        with self.assertRaises(SystemExit) as e:
            run(fetch_comments.main)
        self.assertIn("Usage", str(e.exception.code))


class PostReplies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = pathlib.Path(self.tmp.name, "replies.json")
        self.file.write_text(json.dumps({"replies": [
            {"id": 1, "commentId": "1", "to": "dana", "slide": 1, "text": "Is it that the cover is all text?"},
            {"id": 8, "commentId": "3", "to": "tom", "slide": 10, "text": "It's from the kickoff workshop."},
            {"id": 10, "commentId": "6", "to": "marcus", "slide": 14, "text": "  "},
            {"id": 11, "to": "tom", "slide": 16, "text": "No thread for this one"},
        ]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_posts_each_reply_into_its_thread_and_skips_repeats(self):
        existing = {"comments": [{"id": "x", "parent_id": "3", "message": "It's from the kickoff workshop."}]}
        calls = []

        def fake(method, path, body=None):
            calls.append((method, path, body))
            return existing if method == "GET" else {"id": "new"}

        with mock.patch.object(post_replies, "call", fake):
            out = run(post_replies.main, KEY, str(self.file))
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(posts, [("POST", f"/files/{KEY}/comments",
                                  {"message": "Is it that the cover is all text?", "comment_id": "1"})])
        summary = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(summary, {"posted": 1, "skipped": 1, "no_thread": [11]})

    def test_dry_run_never_calls_figma(self):
        with mock.patch.object(post_replies, "call", side_effect=AssertionError("called Figma")):
            out = run(post_replies.main, KEY, str(self.file), "--dry-run")
        self.assertIn("Is it that the cover is all text?", out)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["posted"], 0)

    def test_needs_both_arguments(self):
        with self.assertRaises(SystemExit):
            run(post_replies.main, KEY)


if __name__ == "__main__":
    unittest.main()
