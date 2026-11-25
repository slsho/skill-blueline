"""Plugin manifests, the skill's own references, and nothing private in the repo."""
import json
import os
import pathlib
import py_compile
import re
import subprocess
import unittest

from helpers import REPO, SCRIPTS, SKILL

TEXT = (".md", ".py", ".json", ".html", ".txt", ".example", ".gitignore", "")


def repo_files():
    for p in REPO.rglob("*"):
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
            yield p


class Manifests(unittest.TestCase):
    def test_plugin_and_marketplace(self):
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(plugin["name"], "blueline")
        for f in ("name", "owner", "plugins"):
            self.assertIn(f, market)
        self.assertIn("name", market["owner"])
        [entry] = market["plugins"]
        self.assertEqual((entry["name"], entry["source"]), ("blueline", "./"))

    def test_skill_front_matter(self):
        text = (SKILL / "SKILL.md").read_text()
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertTrue(m)
        fm = dict(line.split(": ", 1) for line in m.group(1).splitlines())
        self.assertEqual(fm["name"], "blueline")
        self.assertGreater(len(fm["description"]), 80)

    def test_every_bundled_path_the_skill_names_exists(self):
        text = (SKILL / "SKILL.md").read_text()
        paths = set(re.findall(r"\$\{CLAUDE_SKILL_DIR\}/([\w./-]+\w)", text))
        self.assertTrue(paths)
        for rel in paths:
            self.assertTrue((SKILL / rel).exists(), rel)

    def test_readme_lists_real_files(self):
        readme = (REPO / "README.md").read_text()
        for rel in re.findall(r"^\s+((?:scripts|app|references)/[\w./-]+)", readme, re.M):
            self.assertTrue((SKILL / rel).exists(), rel)
        self.assertIn("/blueline:blueline", readme, "plugin skills are invoked as /plugin:skill")

    def test_scripts_compile(self):
        for p in SCRIPTS.glob("*.py"):
            py_compile.compile(str(p), doraise=True)


class Private(unittest.TestCase):
    def test_env_is_ignored_and_example_has_no_token(self):
        ignore = (REPO / ".gitignore").read_text().splitlines()
        self.assertIn(".env", ignore)
        self.assertIn("blueline-review/", ignore)
        self.assertIn("FIGMA_TOKEN=paste-your-own-token-here", (REPO / ".env.example").read_text())

    def test_git_would_not_add_secrets(self):
        r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--ignored", "--untracked-files=all"],
                           capture_output=True, text=True)
        tracked = [l[3:] for l in r.stdout.splitlines() if not l.startswith("!!")]
        self.assertFalse([t for t in tracked if t == ".env" or t.endswith("/.env") or "blueline-review/" in t])

    def test_no_tokens_or_personal_details(self):
        real = ""
        env = REPO.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("FIGMA_TOKEN="):
                    real = line.split("=", 1)[1].strip().strip("\"'")
        patterns = [re.compile(r"figd_[A-Za-z0-9_-]{20,}"), re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
                    re.compile(r"[\w.+-]+@[\w-]+\.(?:com|ai|io|org|net|co)\b"), re.compile(r"/Users/[a-z]+")]
        hits = []
        for p in repo_files():
            if p.suffix not in TEXT or p.name == ".env":
                continue
            text = p.read_text(errors="ignore")
            if real and real in text:
                hits.append(f"{p}: the real Figma token")
            hits += [f"{p.relative_to(REPO)}: {pat.pattern}" for pat in patterns if pat.search(text)]
        self.assertEqual(hits, [])

    def test_no_real_deck_links_in_the_demo(self):
        # The demo deck link would only show reviewers a "no access" page
        self.assertEqual(json.loads((REPO / "docs" / "data.json").read_text())["deck"]["url"], "")


class BeforePublishing(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("BLUELINE_PREPUBLISH"), "set BLUELINE_PREPUBLISH=1 before publishing")
    def test_placeholders_filled(self):
        left = [f"{p.relative_to(REPO)}: {w}" for p in repo_files() if p.suffix in TEXT and p.name != "test_repo.py"
                for w in ("OWNER_NAME", "OWNER/", "DEMO_URL") if w in p.read_text(errors="ignore")]
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
