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
        fake_steam(self.steam)

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

    def test_each_key_says_its_default(self):
        path = self.prefix() / "OptiScaler.ini"
        path.write_bytes(FIXTURE.read_bytes().replace(b"UpscalerIndex=auto", b"UpscalerIndex=0", 1))
        keys = {(s["name"], k["key"]): k for s in self.b.load("123")["sections"] for k in s["keys"]}
        k = keys[("FSR", "UpscalerIndex")]
        self.assertEqual((k["value"], k["default"]), ("0", "auto"))  # changed from OptiScaler's
        self.assertEqual(keys[("FrameGen", "Enabled")]["default"], "auto")

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


class UninstallTest(unittest.TestCase):
    def test_purge(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        b = load_backend()
        b.setup(FakeCtx())
        b.GLOBAL_DIR = tmp / "invasor-opti"
        (b.GLOBAL_DIR / "optiscaler").mkdir(parents=True)
        b.uninstall(FakeCtx(), purge=False)
        self.assertTrue(b.GLOBAL_DIR.exists())
        b.uninstall(FakeCtx(), purge=True)
        self.assertFalse(b.GLOBAL_DIR.exists())


if __name__ == "__main__":
    unittest.main()
