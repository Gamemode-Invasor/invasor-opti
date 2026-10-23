import shutil
import tempfile
import unittest
from pathlib import Path

from tests.loader import FakeCtx, HERE, InvalidArgument, Unavailable, fake_steam, load_backend

FIXTURE = HERE / "assets" / "OptiScaler.optiscaler.ini"



class BackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.steam = self.tmp / "Steam"
        (self.steam / "steamapps").mkdir(parents=True)
        self.b = load_backend()
        self.b.setup(FakeCtx())
        fake_steam(self.b, self.steam)

    def prefix(self, appid="123"):
        umu = self.steam / "steamapps/compatdata" / appid / "pfx/drive_c/windows/system32/umu"
        umu.mkdir(parents=True)
        return umu

    def test_states(self):
        self.assertEqual(self.b.load("123")["state"], "no_prefix")
        umu = self.prefix()
        self.assertEqual(self.b.load("123")["state"], "no_ini")
        shutil.copy(FIXTURE, umu / "OptiScaler.ini")
        r = self.b.load("123")
        self.assertEqual(r["state"], "ok")
        self.assertEqual(len(r["sections"]), 37)
        self.assertIsInstance(r["mtime"], str)

    def test_bad_appid(self):
        with self.assertRaises(InvalidArgument):
            self.b.load("../etc")

    def test_set(self):
        path = self.prefix() / "OptiScaler.ini"
        shutil.copy(FIXTURE, path)
        path.chmod(0o600)
        r = self.b.load("123")
        out = self.b.set_value("123", "FrameGen", "Enabled", "True", r["mtime"])
        self.assertEqual(out["value"], "true")  # the option's own spelling
        self.assertIn(b"\r\nEnabled=true\r\n", path.read_bytes())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.b.load("123")["mtime"], out["mtime"])

    def test_set_refuses(self):
        path = self.prefix() / "OptiScaler.ini"
        shutil.copy(FIXTURE, path)
        mtime = self.b.load("123")["mtime"]
        with self.assertRaises(InvalidArgument):
            self.b.set_value("123", "FrameGen", "Enabled", "maybe", mtime)
        with self.assertRaises(InvalidArgument):
            self.b.set_value("123", "FrameGen", "NoSuchKey", "1", mtime)
        with self.assertRaises(Unavailable):
            self.b.set_value("123", "FrameGen", "Enabled", "true", "1")  # someone saved meanwhile
        self.assertEqual(path.read_bytes(), FIXTURE.read_bytes())
        self.assertEqual(list(path.parent.glob(".OptiScaler.ini.*")), [])  # no temp file left


class GlobalTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = tmp / "invasor-opti"
        self.path = self.b.GLOBAL_DIR / "OptiScaler.ini"

    def test_starts_as_the_module_ini(self):
        r = self.b.global_load()
        self.assertEqual(r["state"], "ok")
        self.assertEqual(self.path.read_bytes(), self.b.DEFAULTS["invasor"].read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_set_changes_one_line(self):
        r = self.b.global_load()
        self.b.global_set("FSR", "UpscalerIndex", "1", r["mtime"])
        old = self.b.DEFAULTS["invasor"].read_bytes().splitlines(keepends=True)
        new = self.path.read_bytes().splitlines(keepends=True)
        self.assertEqual([(a, b) for a, b in zip(old, new) if a != b], [(b"UpscalerIndex=0\r\n", b"UpscalerIndex=1\r\n")])

    def test_resets(self):
        r = self.b.global_load()
        self.b.global_set("FSR", "UpscalerIndex", "2", r["mtime"])
        for source in ("optiscaler", "invasor"):
            r = self.b.global_reset(source)
            self.assertEqual(r["state"], "ok")
            self.assertEqual(self.path.read_bytes(), self.b.DEFAULTS[source].read_bytes(), source)
        with self.assertRaises(InvalidArgument):
            self.b.global_reset("../../etc/passwd")

    def test_uninstall(self):
        self.b.global_load()
        self.b.uninstall(FakeCtx(), purge=False)
        self.assertTrue(self.path.exists())
        self.b.uninstall(FakeCtx(), purge=True)
        self.assertFalse(self.b.GLOBAL_DIR.exists())

    def test_the_two_defaults_differ(self):
        self.assertNotEqual(self.b.DEFAULTS["optiscaler"].read_bytes(), self.b.DEFAULTS["invasor"].read_bytes())


if __name__ == "__main__":
    unittest.main()
