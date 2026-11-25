"""Shared paths for the tests."""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "blueline"
SCRIPTS = SKILL / "scripts"
DOCS = REPO / "docs"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
