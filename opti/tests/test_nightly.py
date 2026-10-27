import hashlib
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.loader import FakeCtx, FakeHttp, InvalidArgument, Unavailable, archive, load_backend, submodule

NAME = "OptiScaler_v10.0.0-pre1_20261023.7z"


def release(data, name=NAME, digest=True, tag="nightly-20261023"):
    asset = {"name": name, "browser_download_url": f"https://github.com/optiscaler/OptiScaler-nightly/releases/download/{tag}/{name}"}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": tag, "assets": [{"name": "notes.txt", "browser_download_url": "x"}, asset]}


def server(n, rel, data):
    """ctx.http with GitHub's answer (a list holding rel) and the .7z it points to."""
    url = next(a["browser_download_url"] for a in rel["assets"] if a["name"].endswith(".7z"))
    return FakeHttp({n.API: [rel]}, {url: data})


def make_7z(folder, files):
    src = Path(folder) / "src"
    src.mkdir()
    for name, content in files.items():
        (src / name).write_bytes(content)
    out = Path(folder) / "build.7z"
    subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(out), "-C", str(src), *files], check=True)
    return out.read_bytes()


class Task:
    """A ctx.task's update, recorded."""

    def __init__(self):
        self.steps = []

    def update(self, fraction, text):
        self.steps.append(text)


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
        http = server(self.n, release(b"abc", digest=False), b"abc")
        with self.assertRaisesRegex(self.n.Error, "checksum"):
            self.n.update(self.tmp / "optiscaler", http, archive)
        self.assertEqual(http.downloads, [])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_pick_refuses(self):
        for bad in (None, [], {}, {"assets": None}, {"assets": [1, "x", {"name": "a.zip", "browser_download_url": "u"}]}):
            with self.assertRaises(self.n.Error, msg=bad):
                self.n.pick(bad)
        for answer in ([], {}, "x"):
            with self.assertRaises(self.n.Error):
                self.n.latest(FakeHttp({self.n.API: answer}))

    def test_installed(self):
        self.assertIsNone(self.n.read_marker(self.tmp))
        marker = self.tmp / self.n.MARKER
        for bad in ("", "{", "[]", '{"tag": 3}'):
            marker.write_text(bad)
            self.assertIsNone(self.n.read_marker(self.tmp), bad)
        marker.write_text('{"tag": "t", "version": "v1", "extra": 1}')
        self.assertEqual(self.n.read_marker(self.tmp), {"tag": "t", "version": "v1", "date": None, "asset": None, "sha256": None})

    def test_bad_checksum_leaves_nothing(self):
        data = b"not the real thing"
        http = server(self.n, release(b"x" * len(data)), data)
        with self.assertRaisesRegex(InvalidArgument, "checksum"):
            self.n.update(self.tmp / "optiscaler", http, archive)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_offline(self):
        http = FakeHttp({self.n.API: Unavailable("couldn't get it: no network")})
        with self.assertRaises(Unavailable):
            self.n.update(self.tmp / "optiscaler", http, archive)
        self.assertEqual(list(self.tmp.iterdir()), [])


@unittest.skipUnless(shutil.which("bsdtar"), "needs bsdtar")
class UnpackTest(unittest.TestCase):
    """update() with the core's own ctx.archive."""

    @classmethod
    def setUpClass(cls):
        cls.n = submodule(load_backend(), "nightly")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.folder = self.tmp / "data" / "optiscaler"

    def second(self):
        """Where a second .7z is made (not next to the build)."""
        d = self.tmp / "second"
        d.mkdir()
        return d

    def test_download_and_swap(self):
        data = make_7z(self.tmp, {"OptiScaler.dll": b"dll", "OptiScaler.ini": b"[A]\nX=1\n"})
        rel = release(data)
        http, task = server(self.n, rel, data), Task()
        r, downloaded = self.n.update(self.folder, http, archive, task)
        self.assertTrue(downloaded)
        self.assertEqual((self.folder / "OptiScaler.dll").read_bytes(), b"dll")
        self.assertEqual(self.n.read_marker(self.folder)["version"], "v10.0.0-pre1")
        self.assertEqual(task.steps, ["Looking for the latest nightly", "Unpacking"])
        d = http.downloads[0]
        self.assertEqual((d["checksum"], d["max_bytes"], d["task"]), (("sha256", r["sha256"]), self.n.MAX_SIZE, task))
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["optiscaler"])  # no temp left

        # The same tag again: nothing downloaded.
        http.asked.clear()
        self.assertFalse(self.n.update(self.folder, http, archive)[1])
        self.assertEqual(http.asked, [self.n.API])

        # A newer one replaces the old folder whole.
        data2 = make_7z(self.second(), {"OptiScaler.dll": b"dll2"})
        r2, downloaded = self.n.update(self.folder, server(self.n, release(data2, tag="nightly-20261024"), data2), archive)
        self.assertTrue(downloaded)
        self.assertEqual((self.folder / "OptiScaler.dll").read_bytes(), b"dll2")
        self.assertFalse((self.folder / "OptiScaler.ini").exists())
        self.assertEqual(self.n.read_marker(self.folder)["tag"], "nightly-20261024")
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["optiscaler"])

    def test_archive_without_the_dll(self):
        data = make_7z(self.tmp, {"readme.txt": b"hi"})
        with self.assertRaisesRegex(self.n.Error, "no OptiScaler.dll"):
            self.n.update(self.folder, server(self.n, release(data), data), archive)
        self.assertFalse(self.folder.exists())

    def test_asset_name_is_never_a_path(self):
        data = make_7z(self.tmp, {"OptiScaler.dll": b"dll"})
        self.assertTrue(self.n.update(self.folder, server(self.n, release(data, name="../../escape.7z"), data), archive)[1])
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("escape*")), [])

    def test_symlink_in_the_archive_is_refused(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "OptiScaler.dll").write_bytes(b"dll")
        (src / "evil.dll").symlink_to("/etc/passwd")
        out = self.tmp / "build.7z"
        subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(out), "-C", str(src), "OptiScaler.dll", "evil.dll"], check=True)
        data = out.read_bytes()
        with self.assertRaises(InvalidArgument):
            self.n.update(self.folder, server(self.n, release(data), data), archive)
        self.assertFalse(self.folder.exists())

    def test_not_an_archive(self):
        data = b"garbage" * 100
        with self.assertRaises((InvalidArgument, Unavailable)):
            self.n.update(self.folder, server(self.n, release(data), data), archive)
        self.assertFalse(self.folder.exists())

    def test_stop_before_the_swap_keeps_the_old_build(self):
        data = make_7z(self.tmp, {"OptiScaler.dll": b"old"})
        self.n.update(self.folder, server(self.n, release(data), data), archive)
        data2 = make_7z(self.second(), {"OptiScaler.dll": b"new"})
        stop = threading.Event()

        class Lock:  # the module unloads while waiting for the swap
            def __enter__(self):
                stop.set()

            def __exit__(self, *exc):
                return False
        with self.assertRaisesRegex(self.n.Error, "stopped"):
            self.n.update(self.folder, server(self.n, release(data2, tag="t2"), data2), archive, stop=stop, swap_lock=Lock())
        self.assertEqual((self.folder / "OptiScaler.dll").read_bytes(), b"old")
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["optiscaler"])


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

        def slow_update(folder, http, archive, task, stop, swap_lock):
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
