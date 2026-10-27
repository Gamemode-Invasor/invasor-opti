"""OptiScaler.ini files that aren't tidy: the module must read any of them, show what it can and
change one value without breaking anything else."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_steam, load_backend, submodule


def keys(ini, text):
    return {(s["name"], k["key"]): k["value"] for s in ini.parse(text) for k in s["keys"]}


class BrokenIniTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ini = submodule(load_backend(), "ini")

    def roundtrip(self, raw, section, key, value):
        """decode -> set_value -> encode, as the backend does."""
        text, encoding = self.ini.decode(raw)
        return self.ini.set_value(text, section, key, value).encode(encoding)

    def test_empty(self):
        for text in ("", "\n\n", "; only a comment\n", "[Empty]\n"):
            sections = self.ini.parse(text)
            self.assertEqual(sum(len(s["keys"]) for s in sections), 0, text)
            self.assertEqual(self.ini.describe(sections), [{"name": s["name"], "keys": []} for s in sections])

    def test_no_section_headers(self):
        text = "; Enables it\n; true or false\nEnabled=auto\nLevel=3\n"
        self.assertEqual(keys(self.ini, text), {("", "Enabled"): "auto", ("", "Level"): "3"})
        d = self.ini.describe(self.ini.parse(text))[0]["keys"][0]
        self.assertEqual(d["kind"], "select")
        self.assertEqual(self.ini.set_value(text, "", "Level", "4"), text.replace("Level=3", "Level=4"))

    def test_keys_before_the_first_section(self):
        text = "Top=1\n[A]\nTop=2\n"
        self.assertEqual(keys(self.ini, text), {("", "Top"): "1", ("A", "Top"): "2"})
        self.assertEqual(self.ini.set_value(text, "A", "Top", "9"), "Top=1\n[A]\nTop=9\n")
        self.assertEqual(self.ini.set_value(text, "", "Top", "9"), "Top=9\n[A]\nTop=2\n")

    def test_section_twice(self):
        text = "[A]\nX=1\n[B]\nZ=0\n[A]\nY=2\n"
        sections = self.ini.parse(text)
        self.assertEqual([s["name"] for s in sections], ["A", "B"])
        self.assertEqual([k["key"] for k in sections[0]["keys"]], ["X", "Y"])
        self.assertEqual(self.ini.set_value(text, "A", "Y", "5"), text.replace("Y=2", "Y=5"))

    def test_key_twice(self):
        text = "[A]\nX=1\nX=2\n"
        self.assertEqual(keys(self.ini, text), {("A", "X"): "1"})  # the one set_value changes
        self.assertEqual(self.ini.set_value(text, "A", "X", "7"), "[A]\nX=7\nX=2\n")

    def test_junk_lines_are_skipped(self):
        text = "[A\nthis is junk\n=5\nKey With Space=1\n[]\n\x00\x01\nX=1\n[B]\n  Y = 2  \n"
        self.assertEqual(keys(self.ini, text), {("", "X"): "1", ("B", "Y"): "2"})
        # Spacing around the value is kept.
        self.assertEqual(self.ini.set_value(text, "B", "Y", "3"), text.replace("  Y = 2  ", "  Y = 3  "))

    def test_comments_of_a_junk_line_dont_stick(self):
        text = "[A]\n; true or false\nnot a key\nX=hello\n"
        self.assertEqual(self.ini.describe(self.ini.parse(text))[0]["keys"][0]["kind"], "text")

    def test_value_with_equals_and_semicolon(self):
        text = "[A]\nPath=C:\\a=b;c\n"
        self.assertEqual(keys(self.ini, text), {("A", "Path"): "C:\\a=b;c"})
        self.assertEqual(self.ini.set_value(text, "A", "Path", "x=y"), "[A]\nPath=x=y\n")

    def test_empty_value(self):
        text = "[A]\nX=\n"
        self.assertEqual(keys(self.ini, text), {("A", "X"): ""})
        self.assertEqual(self.ini.set_value(text, "A", "X", "1"), "[A]\nX=1\n")

    def test_line_ends(self):
        for nl in ("\n", "\r\n", "\r"):
            text = f"[A]{nl}X=1{nl}Y=2"  # and no newline at the end
            self.assertEqual(keys(self.ini, text), {("A", "X"): "1", ("A", "Y"): "2"}, repr(nl))
            self.assertEqual(self.ini.set_value(text, "A", "X", "3"), text.replace("X=1", "X=3"))
            self.assertEqual(self.ini.set_value(text, "A", "Y", "4"), text.replace("Y=2", "Y=4"))

    def test_bom(self):
        raw = "\ufeff[A]\r\nX=1\r\n".encode("utf-8")
        text, encoding = self.ini.decode(raw)
        self.assertEqual(keys(self.ini, text), {("A", "X"): "1"})
        self.assertEqual(self.roundtrip(raw, "A", "X", "2"), raw.replace(b"X=1", b"X=2"))

    def test_latin1(self):
        raw = "[A]\n; café\nX=é\n".encode("latin-1")
        text, encoding = self.ini.decode(raw)
        self.assertEqual(encoding, "latin-1")
        self.assertEqual(keys(self.ini, text), {("A", "X"): "é"})
        self.assertEqual(self.roundtrip(raw, "A", "X", "1"), raw.replace("X=é".encode("latin-1"), b"X=1"))

    def test_odd_comments_never_break_infer(self):
        weird = [
            [], [""], ["0 = "], ["| | |"], ["From 5 to 1"], ["0 - 100000"], ["1e5 - 2e5"], [",,,"],
            ["true or false", "0 = A | 1 = B"], ["x" * 10000], ["0.0000001 - 0.9"], ["-5 to -1"],
        ]
        for comments in weird:
            control = self.ini.infer(comments, "auto")
            self.assertIn(control["kind"], ("select", "text"), comments)
            if control["kind"] == "select":
                self.assertEqual(control["options"][0]["value"], "auto")
                self.assertLessEqual(len(control["options"]), self.ini.MAX_STEPS + 2)

    def test_set_value_missing(self):
        with self.assertRaises(KeyError):
            self.ini.set_value("[A]\n; X=1\n", "A", "X", "2")  # commented out is not a key
        with self.assertRaises(KeyError):
            self.ini.set_value("[A]\nX=1\n", "B", "X", "2")



class BrokenFileTest(unittest.TestCase):
    """The file itself: missing, a folder, a symlink, huge, unreadable."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        steam = tmp / "Steam"
        self.umu = steam / "steamapps/compatdata/42/pfx/drive_c/windows/system32/umu"
        self.umu.mkdir(parents=True)
        self.path = self.umu / "OptiScaler.ini"
        self.b = load_backend()
        self.b.setup(FakeCtx())
        fake_steam(steam)

    def test_folder_named_like_the_ini(self):
        self.path.mkdir()
        self.assertEqual(self.b.load("42")["state"], "no_ini")

    def test_empty_file(self):
        self.path.write_bytes(b"")
        r = self.b.load("42")
        self.assertEqual((r["state"], r["sections"]), ("ok", []))

    def test_symlink_stays_a_symlink(self):
        target = self.umu / "real.ini"
        target.write_text("[A]\nX=1\n")
        self.path.symlink_to(target)
        r = self.b.load("42")
        self.b.set_value("42", "A", "X", "2", r["mtime"])
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_text(), "[A]\nX=2\n")

    def test_too_big(self):
        self.path.write_bytes(b"[A]\n" + b";" * (self.b.MAX_BYTES + 1))
        with self.assertRaises(Unavailable):
            self.b.load("42")

    @unittest.skipIf(os.geteuid() == 0, "root reads anything")
    def test_unreadable(self):
        self.path.write_text("[A]\nX=1\n")
        self.path.chmod(0)
        self.addCleanup(self.path.chmod, 0o600)
        with self.assertRaises(Unavailable):
            self.b.load("42")

    def test_value_the_encoding_cant_hold(self):
        self.path.write_bytes("[A]\nX=é\n".encode("latin-1"))
        r = self.b.load("42")
        with self.assertRaises(InvalidArgument):
            self.b.set_value("42", "A", "X", "日本", r["mtime"])
        self.assertEqual(self.path.read_bytes(), "[A]\nX=é\n".encode("latin-1"))

    def test_binary_garbage(self):
        self.path.write_bytes(bytes(range(256)) * 4)
        r = self.b.load("42")
        self.assertEqual(r["state"], "ok")  # read as Latin-1, nothing usable, no error


if __name__ == "__main__":
    unittest.main()
