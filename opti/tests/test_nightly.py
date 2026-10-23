import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.loader import FakeCtx, load_backend, submodule

NAME = "OptiScaler_v10.0.0-pre1_20261023.7z"


def release(data, name=NAME, digest=True, tag="nightly-20261023"):
    asset = {"name": name, "size": len(data), "browser_download_url": f"https://github.com/optiscaler/OptiScaler-nightly/releases/download/{tag}/{name}"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": tag, "assets": [{"name": "notes.txt", "browser_download_url": "x"}, asset]}


def make_7z(folder, files):
    src = Path(folder) / "src"
    src.mkdir()
    for name, content in files.items():
        (src / name).write_bytes(content)
    out = Path(folder) / "build.7z"
    subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(out), "-C", str(src), *files], check=True)
    return out.read_bytes()


class Server:
    """open_url for the tests: the API answer and the .7z, and how many times each was asked."""

    def __init__(self, releases, data):
        self.releases, self.data, self.asked = releases, data, []

    def __call__(self, url, timeout):
        self.asked.append(url)
        if url.startswith("https://api.github.com/"):
            return io.BytesIO(json.dumps(self.releases).encode())
        return io.BytesIO(self.data)


class NightlyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = submodule(load_backend(), "nightly")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_pick(self):
        r = self.n.pick(release(b"abc"))
        self.assertEqual((r["tag"], r["version"], r["date"], r["asset"]), ("nightly-20261023", "v10.0.0-pre1", "20261023", NAME))
        self.assertEqual(r["sha256"], hashlib.sha256(b"abc").hexdigest())
        odd = self.n.pick(release(b"abc", name="Something.7z", digest=False))
        self.assertEqual((odd["version"], odd["date"], odd["sha256"]), ("Something", "", None))

    def test_only_the_nightly_repo_is_followed(self):
        for url in ("http://github.com/optiscaler/OptiScaler-nightly/releases/download/t/a.7z",
                    "https://example.com/optiscaler/OptiScaler-nightly/releases/download/t/a.7z",
                    "https://github.com/someone/OptiScaler-nightly/releases/download/t/a.7z"):
            r = {"tag_name": "t", "assets": [{"name": "a.7z", "browser_download_url": url}]}
            with self.assertRaises(self.n.Error, msg=url):
                self.n.pick(r)

    def test_no_checksum_no_download(self):
        server = Server([release(b"abc", digest=False)], b"abc")
        with self.assertRaisesRegex(self.n.Error, "checksum"):
            self.n.update(self.tmp / "optiscaler", server)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_7z_symlinks_are_refused(self):
        listing = "Path = a.dll\nSymbolic Link = \n\nPath = evil\nSymbolic Link = /etc\n\nPath = evil/x\n"
        found = self.n.names(listing, True)
        self.assertEqual(found, ["a.dll", None, "evil/x"])
        self.assertEqual([self.n.safe(n) for n in found], [True, False, True])

    def test_unpacking_stops_when_asked(self):
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        started = time.monotonic()
        with self.assertRaisesRegex(self.n.Error, "stopped"):
            self.n._run(["sleep", "30"], stop)
        self.assertLess(time.monotonic() - started, 5)

    def test_pick_refuses(self):
        for bad in (None, [], {}, {"assets": None}, {"assets": [1, "x", {"name": "a.zip", "browser_download_url": "u"}]}):
            with self.assertRaises(self.n.Error, msg=bad):
                self.n.pick(bad)
        for answer in ([], {}, "x"):
            with self.assertRaises(self.n.Error):
                self.n.latest(Server(answer, b""))

    def test_installed(self):
        self.assertIsNone(self.n.read_marker(self.tmp))
        marker = self.tmp / self.n.MARKER
        for bad in ("", "{", "[]", '{"tag": 3}'):
            marker.write_text(bad)
            self.assertIsNone(self.n.read_marker(self.tmp), bad)
        marker.write_text('{"tag": "t", "version": "v1", "extra": 1}')
        self.assertEqual(self.n.read_marker(self.tmp), {"tag": "t", "version": "v1", "date": None, "asset": None, "sha256": None})

    def test_safe_paths(self):
        for ok in ("OptiScaler.dll", "./OptiScaler/a.dll", "a..b/c"):
            self.assertTrue(self.n.safe(ok), ok)
        for bad in ("/etc/passwd", "../x", "a/../../x", "C:\\Windows\\x", "a\\..\\..\\x"):
            self.assertFalse(self.n.safe(bad), bad)
        self.assertEqual(self.n.names("Path = /tmp/a.7z\nType = 7z\n\nPath = x.dll\nSize = 1\n", True), ["/tmp/a.7z", "x.dll"])

    def test_bad_checksum_leaves_nothing(self):
        data = b"not the real thing"
        server = Server([release(b"x" * len(data))], data)
        folder = self.tmp / "optiscaler"
        with self.assertRaisesRegex(self.n.Error, "checksum"):
            self.n.update(folder, server)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_cut_short(self):
        r = release(b"0123456789")
        server = Server([r], b"01234")
        with self.assertRaisesRegex(self.n.Error, "incomplete"):
            self.n.update(self.tmp / "optiscaler", server)

    def test_stop(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(self.n.Error, "stopped"):
            self.n.update(self.tmp / "optiscaler", Server([release(b"x")], b"x"), stop=stop)
        self.assertEqual(list(self.tmp.iterdir()), [])


@unittest.skipUnless(shutil.which("bsdtar"), "needs bsdtar")
class UnpackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = submodule(load_backend(), "nightly")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.folder = self.tmp / "data" / "optiscaler"

    def test_download_and_swap(self):
        data = make_7z(self.tmp, {"OptiScaler.dll": b"dll", "OptiScaler.ini": b"[A]\nX=1\n"})
        server = Server([release(data)], data)
        steps = []
        r, downloaded = self.n.update(self.folder, server, lambda f, t: steps.append((f, t)))
        self.assertTrue(downloaded)
        self.assertEqual((self.folder / "OptiScaler.dll").read_bytes(), b"dll")
        self.assertEqual(self.n.read_marker(self.folder)["version"], "v10.0.0-pre1")
        self.assertIn((1.0, f"Downloading: 0 of 0 MB"), steps)
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["optiscaler"])  # no temp left

        # The same tag again: nothing downloaded.
        server.asked.clear()
        self.assertFalse(self.n.update(self.folder, server)[1])
        self.assertEqual(len(server.asked), 1)

        # A newer one replaces the old folder whole.
        data2 = make_7z(self.tmp / "data", {"OptiScaler.dll": b"dll2"})
        r2, downloaded = self.n.update(self.folder, Server([release(data2, tag="nightly-20261024")], data2))
        self.assertTrue(downloaded)
        self.assertEqual((self.folder / "OptiScaler.dll").read_bytes(), b"dll2")
        self.assertFalse((self.folder / "OptiScaler.ini").exists())
        self.assertEqual(self.n.read_marker(self.folder)["tag"], "nightly-20261024")

    def test_archive_without_the_dll(self):
        data = make_7z(self.tmp, {"readme.txt": b"hi"})
        with self.assertRaisesRegex(self.n.Error, "no OptiScaler.dll"):
            self.n.update(self.folder, Server([release(data)], data))
        self.assertFalse(self.folder.exists())

    def test_asset_name_is_never_a_path(self):
        data = make_7z(self.tmp, {"OptiScaler.dll": b"dll"})
        r = release(data, name="../../escape.7z")
        self.assertTrue(self.n.update(self.folder, Server([r], data))[1])
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("escape*")), [])

    def test_symlink_in_the_archive_is_refused(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "OptiScaler.dll").write_bytes(b"dll")
        (src / "evil.dll").symlink_to("/etc/passwd")
        out = self.tmp / "build.7z"
        subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(out), "-C", str(src), "OptiScaler.dll", "evil.dll"], check=True)
        data = out.read_bytes()
        with self.assertRaisesRegex(self.n.Error, "symbolic link"):
            self.n.update(self.folder, Server([release(data)], data))
        self.assertFalse(self.folder.exists())

    def test_not_an_archive(self):
        data = b"garbage" * 100
        with self.assertRaises(self.n.Error):
            self.n.update(self.folder, Server([release(data)], data))
        self.assertFalse(self.folder.exists())


if __name__ == "__main__":
    unittest.main()


class DownloadThreadTest(unittest.TestCase):
    """backend: one download at a time, and teardown waits for it to stop."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = tmp / "invasor-opti"
        self.n = submodule(self.b, "nightly")
        self.release = threading.Event()

        def slow_update(folder, progress, stop, swap_lock):
            while not stop.is_set() and not self.release.is_set():
                time.sleep(0.01)
            raise self.n.Error("Download stopped")
        self.n.update = slow_update

    def test_busy_then_teardown(self):
        self.assertEqual(self.b.nightly_download(), {"started": True, "busy": False})
        self.assertEqual(self.b.nightly_download(), {"started": False, "busy": True})
        self.assertTrue(self.b.nightly_status()["busy"])
        self.b.teardown()
        self.assertFalse(self.b._thread.is_alive())
        self.assertEqual(self.b.nightly_status(), {"installed": None, "busy": False, "error": "Download stopped"})
