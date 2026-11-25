"""diffboxes.py finds what changed between two slide images."""
import json
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

from helpers import DOCS, SCRIPTS

TOOL = str(SCRIPTS / "diffboxes.py")


def boxes(folder, *pairs):
    r = subprocess.run([sys.executable, TOOL, folder, *pairs], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


class DiffBoxes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        base = Image.new("RGB", (1280, 720), (24, 33, 56))
        ImageDraw.Draw(base).text((80, 80), "Ecosystem mapping", fill=(240, 240, 240))
        base.save(f"{d}/before.png")
        base.save(f"{d}/same.png")
        one = base.copy()
        ImageDraw.Draw(one).rectangle([800, 100, 1199, 599], outline=(200, 200, 200), width=3)  # a new graphic
        one.save(f"{d}/after.png")
        two = base.copy()
        dr = ImageDraw.Draw(two)
        dr.rectangle([100, 500, 299, 599], fill=(230, 100, 50))
        dr.rectangle([1000, 100, 1099, 199], fill=(230, 100, 50))
        two.save(f"{d}/two.png")
        base.resize((640, 360)).save(f"{d}/small-before.png")
        speck = base.copy()
        speck.putpixel((640, 360), (255, 255, 255))
        speck.save(f"{d}/speck.png")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_change_no_boxes(self):
        self.assertEqual(boxes(self.tmp.name, "before", "same"), {"same": []})

    def test_one_change_one_box_around_it(self):
        [b] = boxes(self.tmp.name, "before", "after")["after"]
        x, y, w, h, cells = b
        # the rectangle spans 62.5-93.8% across and 13.9-83.3% down; the box adds about 1% padding
        self.assertAlmostEqual(x, 62.5, delta=2.5)
        self.assertAlmostEqual(y, 13.9, delta=2.5)
        self.assertAlmostEqual(x + w, 93.8, delta=2.5)
        self.assertAlmostEqual(y + h, 83.3, delta=2.5)
        self.assertLessEqual(x, 62.5)
        self.assertLessEqual(y, 13.9)
        self.assertGreaterEqual(x + w, 93.7)
        self.assertGreaterEqual(y + h, 83.2)
        self.assertGreater(cells, 3)

    def test_separate_changes_separate_boxes_top_first(self):
        out = boxes(self.tmp.name, "before", "two")["two"]
        self.assertEqual(len(out), 2)
        self.assertLess(out[0][1], out[1][1], "sorted top to bottom")

    def test_specks_are_dropped(self):
        self.assertEqual(boxes(self.tmp.name, "before", "speck")["speck"], [])

    def test_several_pairs_in_one_run(self):
        out = boxes(self.tmp.name, "before", "after", "before", "two")
        self.assertEqual(set(out), {"after", "two"})

    def test_different_sizes_are_scaled(self):
        self.assertEqual(boxes(self.tmp.name, "small-before", "same")["same"], [])

    def test_real_slides(self):
        # The ring map added to the right half of the real cover
        out = boxes(str(DOCS / "img"), "before-01", "after-02")["after-02"]
        self.assertTrue(out)
        self.assertTrue(all(b[0] >= 55 for b in out), "every box is on the right half of the slide")

    def test_usage(self):
        r = subprocess.run([sys.executable, TOOL, self.tmp.name, "before"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Usage", r.stderr)


if __name__ == "__main__":
    unittest.main()
