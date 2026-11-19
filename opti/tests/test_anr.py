import hashlib
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from tests.loader import FakeCtx, FakeHttp, InvalidArgument, Unavailable, archive, fake_game, load_backend, submodule, swap_dir, tree

NAME = "Apex Neural Rendering FG"
BASE = "https://github.com/apex-compute/ANR/releases/download/"
DLL = b"MZ anr provider"
INI_TEXT = "[Libraries]\nOptiDllPath=c:\\windows\\system32\\umu\\OptiScaler\n[FrameGen]\nFGInput=auto\nFGNvngxReplacement=auto\n"


def addon(version="V.B20", extra=None, package=None, dll=DLL):
    """An addon zip as ANR ships it: install scripts, nr_fg_package.json and the provider's folder."""
    files = {
        "install.sh": b"#!/bin/sh\necho never run\n",
        "install.ps1": b"",
        "uninstall.sh": b"",
        "README.txt": b"readme",
        "nr_fg_package.json": json.dumps(package if package is not None else {
            "product": "ANR FG Beta", "version": version, "kind": "addon",
            "provider_sha256": hashlib.sha256(dll).hexdigest()}).encode(),
        f"{NAME}/dlssg/nvngx_dlssg.dll": dll,
        f"{NAME}/dlssg/nr_fg.ini": b"[fg]\n",
        f"{NAME}/dlssg/path_table.json": b"{}",
        f"{NAME}/dlssg/kernels/k0.bin": b"kernel",
    }
    files.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            if data is None:
                continue
            if isinstance(data, tuple):  # (ZipInfo attributes, data): a link, say
                info = zipfile.ZipInfo(name)
                info.external_attr = data[0]
                z.writestr(info, data[1])
            else:
                z.writestr(name, data)
    return buf.getvalue()


def release(data, version="V.B20", sums=True, digest=True, sums_digest=True, sums_text=None, **more):
    """GitHub's answer for one release, and the files it points to: (release, {url: bytes})."""
    tag = f"FG-{version}"
    zname = f"anr-fg-{version}-addon.zip"
    text = sums_text if sums_text is not None else (
        f"{hashlib.sha256(data).hexdigest()}  {zname}\n{'0' * 64}  anr-fg-{version}-full.zip\n").encode()
    assets = [
        {"name": f"anr-fg-{version}-full.zip", "browser_download_url": BASE + f"{tag}/anr-fg-{version}-full.zip"},
        {"name": zname, "browser_download_url": BASE + f"{tag}/{zname}"},
    ]
    if digest:
        assets[1]["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    files = {assets[1]["browser_download_url"]: data}
    if sums:
        s = {"name": "SHA256SUMS.txt", "browser_download_url": BASE + f"{tag}/SHA256SUMS.txt"}
        if sums_digest:
            s["digest"] = "sha256:" + hashlib.sha256(text).hexdigest()
        assets.append(s)
        files[s["browser_download_url"]] = text
    return {"tag_name": tag, "name": f"FG {version}", "published_at": "2026-11-19T12:40:00Z", "assets": assets, **more}, files


class Task:
    def __init__(self):
        self.steps = []

    def update(self, fraction, text):
        self.steps.append(text)


class AnrTest(unittest.TestCase):
    """anr.py: which release, its checksums, and unpacking only ANR FG's folder."""

    @classmethod
    def setUpClass(cls):
        cls.a = submodule(load_backend(), "anr")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.folder = self.tmp / "g" / "anr"

    def server(self, *releases):
        http = FakeHttp({self.a.API: [r for r, _ in releases]})
        for _, files in releases:
            http.files.update(files)
        return http

    def test_pick(self):
        r, _ = release(b"zip")
        p = self.a.pick([{"tag_name": "nr-record-1.2", "assets": []}, {"draft": True, **r}, r])
        self.assertEqual((p["tag"], p["version"], p["date"], p["asset"]), ("FG-V.B20", "V.B20", "20261119", "anr-fg-V.B20-addon.zip"))
        self.assertEqual(p["sha256"], hashlib.sha256(b"zip").hexdigest())
        self.assertTrue(p["sums_url"].endswith("/FG-V.B20/SHA256SUMS.txt"))
        pre, _ = release(b"zip", version="V.B21", prerelease=True)
        self.assertEqual(self.a.pick([pre, r])["version"], "V.B20")  # a prerelease isn't taken

    def test_pick_refuses(self):
        r, _ = release(b"zip")
        elsewhere = json.loads(json.dumps(r))
        for a in elsewhere["assets"]:
            a["browser_download_url"] = a["browser_download_url"].replace("apex-compute", "someone")
        no_sums, _ = release(b"zip", sums=False)
        for bad in (None, {}, [], [1, "x"], [elsewhere], [no_sums], [{**r, "tag_name": "V.B20"}]):
            with self.assertRaises(self.a.Error, msg=repr(bad)[:80]):
                self.a.pick(bad)

    def test_sha256_in(self):
        h = "a" * 64
        text = f"{'b' * 64}  other.zip\n{h.upper()} *anr.zip\nnot a line\n"
        self.assertEqual(self.a.sha256_in(text, "anr.zip"), h)
        self.assertIsNone(self.a.sha256_in(text, "missing.zip"))
        self.assertIsNone(self.a.sha256_in("xyz  anr.zip\n", "anr.zip"))

    def test_download(self):
        data = addon()
        http = self.server(release(data))
        task = Task()
        r, downloaded = self.a.update(self.folder, http, archive, task=task)
        self.assertTrue(downloaded)
        self.assertEqual(r["version"], "V.B20")
        t = tree(self.folder)
        self.assertEqual(t[f"{NAME}/dlssg/nvngx_dlssg.dll"], DLL)
        # Only ANR FG's folder and its package file: never the install scripts or the README.
        self.assertEqual(sorted(k for k in t if "/" not in k), [".invasor-opti.version", NAME, "nr_fg_package.json"])
        self.assertEqual(self.a.status(self.folder), {"version": "V.B20", "tag": "FG-V.B20", "date": "20261119"})
        self.assertEqual([d["checksum"][1] for d in http.downloads],
                         [hashlib.sha256(http.files[r["sums_url"]]).hexdigest(), hashlib.sha256(data).hexdigest()])
        self.assertIn("Unpacking", task.steps)
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["anr"])  # no temp left
        # The same release again: nothing downloaded.
        http.downloads.clear()
        self.assertFalse(self.a.update(self.folder, http, archive)[1])
        self.assertEqual(http.downloads, [])
        # A newer one replaces it whole.
        data2 = addon("V.B21", dll=b"MZ new")
        self.assertTrue(self.a.update(self.folder, self.server(release(data2, "V.B21")), archive)[1])
        self.assertEqual((self.folder / NAME / "dlssg/nvngx_dlssg.dll").read_bytes(), b"MZ new")
        self.assertEqual(self.a.version(self.folder), "V.B21")

    def test_no_checksum_no_download(self):
        data = addon()
        for kw in ({"sums_digest": False}, {"sums_text": b"nothing here\n"}):
            http = self.server(release(data, **kw))
            with self.assertRaisesRegex(self.a.Error, "checksum|SHA256SUMS"):
                self.a.update(self.folder, http, archive)
            self.assertNotIn(release(data)[0]["assets"][1]["browser_download_url"], [d["url"] for d in http.downloads])
            self.assertFalse(self.folder.exists())

    def test_sums_and_github_disagree(self):
        data = addon()
        r, files = release(data)
        r["assets"][1]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(self.a.Error, "different"):
            self.a.update(self.folder, self.server((r, files)), archive)
        self.assertFalse(self.folder.exists())

    def test_a_zip_that_isnt_the_one_in_sha256sums(self):
        data = addon()
        r, files = release(data, digest=False)
        files[r["assets"][1]["browser_download_url"]] = addon(dll=b"MZ tampered")
        with self.assertRaises(InvalidArgument):  # the core's checksum check
            self.a.update(self.folder, self.server((r, files)), archive)
        self.assertFalse(self.folder.exists())

    def assertRefused(self, data, errors=(InvalidArgument,)):
        with self.assertRaises(errors):
            self.a.update(self.folder, self.server(release(data)), archive)
        self.assertFalse(self.folder.exists())
        self.assertEqual([p.name for p in self.folder.parent.iterdir()] if self.folder.parent.exists() else [], [])
        self.assertEqual(list(self.tmp.rglob("evil*")), [])

    def test_zip_slip_is_refused(self):
        self.assertRefused(addon(extra={"../evil.txt": b"x"}))
        self.assertRefused(addon(extra={f"{NAME}/../../evil.txt": b"x"}))
        self.assertRefused(addon(extra={"/tmp/evil.txt": b"x"}))
        self.assertRefused(addon(extra={f"{NAME}\\..\\evil.txt": b"x"}))

    def test_a_link_is_refused(self):
        self.assertRefused(addon(extra={f"{NAME}/dlssg/evil.dll": ((0o120777 << 16), b"/etc/passwd")}))

    def test_too_big(self):
        saved = self.a.MAX_UNPACKED
        self.a.MAX_UNPACKED = 64
        self.addCleanup(setattr, self.a, "MAX_UNPACKED", saved)
        self.assertRefused(addon(extra={f"{NAME}/dlssg/big.bin": b"0" * 4096}))

    def test_incomplete_or_wrong_archive(self):
        self.assertRefused(addon(extra={f"{NAME}/dlssg/nvngx_dlssg.dll": None}), self.a.Error)
        self.assertRefused(addon(package={"product": "ANR"}), self.a.Error)  # no version
        self.assertRefused(addon(package={"version": "V.B20", "provider_sha256": "0" * 64}), self.a.Error)
        self.assertRefused(b"not a zip" * 10, (InvalidArgument, Unavailable))

    def test_stop_before_the_swap_keeps_the_old_copy(self):
        self.a.update(self.folder, self.server(release(addon())), archive)
        stop = threading.Event()

        class Lock:  # the module unloads while waiting for the swap
            def __enter__(self):
                stop.set()

            def __exit__(self, *exc):
                return False
        with self.assertRaisesRegex(self.a.Error, "stopped"):
            self.a.update(self.folder, self.server(release(addon("V.B21"), "V.B21")), archive, stop=stop, swap_lock=Lock())
        self.assertEqual(self.a.version(self.folder), "V.B20")
        self.assertEqual(sorted(p.name for p in self.folder.parent.iterdir()), ["anr"])


class GameAnrTest(unittest.TestCase):
    """The backend: ANR FG kept in Global, put in a game's OptiScaler/custom and taken out, OptiScaler.ini untouched."""

    def setUp(self):
        self.b, tmp, self.umu = fake_game(self)
        build = self.b.GLOBAL_DIR / "optiscaler"
        (build / "OptiScaler").mkdir(parents=True)
        (build / "OptiScaler.dll").write_bytes(b"opti")
        (build / "OptiScaler.ini").write_text(INI_TEXT)
        (build / ".invasor-opti.version").write_text(json.dumps({"tag": "t"}))
        self.b.game_install("42", "dxgi")
        self.ini = self.umu / "OptiScaler.ini"
        self.a = self.b.anr
        self.http = self.b.ctx.http
        self.http.answers[self.a.API] = []

    def serve(self, version="V.B20", dll=DLL):
        r, files = release(addon(version, dll=dll), version)
        self.http.answers[self.a.API] = [r]
        self.http.files.update(files)
        self.b._anr_latest_cache.update(at=0.0, ttl=0)

    def download(self):
        self.assertEqual(self.b.anr_download(), {"started": True, "busy": False})
        self.b._anr_thread.join(10)
        self.assertFalse(self.b._anr_thread.is_alive())
        return self.b.anr_status()

    def test_global(self):
        self.assertEqual(self.b.anr_status(), {"installed": None, "busy": False, "error": None})
        self.assertEqual(self.b.anr_check(), {"installed": None, "latest": None, "update": False})  # no release
        self.serve()
        s = self.download()
        self.assertEqual((s["installed"]["version"], s["error"]), ("V.B20", None))
        self.assertTrue((self.b.GLOBAL_DIR / "anr" / NAME / "dlssg/nvngx_dlssg.dll").is_file())
        self.assertEqual(self.b.anr_check(), {"installed": "V.B20", "latest": "V.B20", "update": False})
        self.serve("V.B21")
        self.assertEqual(self.b.anr_check(), {"installed": "V.B20", "latest": "V.B21", "update": True})
        self.assertIsNone(self.b.anr_remove()["installed"])
        self.assertFalse((self.b.GLOBAL_DIR / "anr").exists())
        self.b.anr_remove()  # nothing left: fine

    def test_check_is_cached_and_offline_is_none(self):
        self.http.answers[self.a.API] = Unavailable("offline")
        self.assertIsNone(self.b.anr_check()["latest"])
        self.serve()
        self.assertEqual(self.b.anr_check()["latest"], "V.B20")
        self.http.asked.clear()
        self.b.anr_check()
        self.assertEqual(self.http.asked, [])  # cached

    def test_a_failed_download_says_why(self):
        self.serve()
        r = self.http.answers[self.a.API][0]
        self.http.files[r["assets"][1]["browser_download_url"]] = b"not the zip"
        s = self.download()
        self.assertIsNone(s["installed"])
        self.assertIn("checksum", s["error"])

    def test_on_and_off(self):
        r = self.b.load("42")
        self.assertEqual((r["anr"], r["install"]["anr"]), (None, {"on": False, "current": None}))
        with self.assertRaises(Unavailable):
            self.b.game_anr("42", True)  # nothing in Global
        self.serve()
        self.download()
        ini = self.ini.read_bytes()
        (self.umu / "OptiScaler/custom/HLX FSR 4 Full").mkdir(parents=True)  # another provider: never touched
        (self.umu / "OptiScaler/custom/HLX FSR 4 Full/x.ini").write_text("x")
        r = self.b.game_anr("42", True)
        self.assertEqual(r["install"]["anr"], {"on": True, "current": True})
        game = self.umu / "OptiScaler/custom" / NAME
        self.assertEqual(tree(game), tree(self.b.GLOBAL_DIR / "anr" / NAME))
        self.assertFalse((game.parent / "nr_fg_package.json").exists())
        self.assertFalse(list(game.parent.glob(".*")))  # no copy left
        self.assertEqual(self.ini.read_bytes(), ini)  # OptiScaler.ini isn't touched
        # A newer copy in Global: the game's isn't current until it's put in again.
        self.serve("V.B21", dll=b"MZ newer")
        self.download()
        self.assertFalse(self.b.load("42")["install"]["anr"]["current"])
        (game / "dlssg/extra.log").write_text("game's")
        self.assertTrue(self.b.game_anr("42", True)["install"]["anr"]["current"])
        self.assertEqual((game / "dlssg/nvngx_dlssg.dll").read_bytes(), b"MZ newer")
        self.assertFalse((game / "dlssg/extra.log").exists())  # replaced whole
        r = self.b.game_anr("42", False)
        self.assertEqual(r["install"]["anr"], {"on": False, "current": None})
        self.assertFalse(game.exists())
        self.assertEqual((self.umu / "OptiScaler/custom/HLX FSR 4 Full/x.ini").read_text(), "x")
        self.assertEqual(self.ini.read_bytes(), ini)
        self.b.game_anr("42", False)  # off again: nothing to do

    def test_a_game_with_it_already_shows_it_on(self):
        game = self.umu / "OptiScaler/custom" / NAME / "dlssg"
        game.mkdir(parents=True)
        (game / "nvngx_dlssg.dll").write_bytes(b"put by hand")
        self.assertEqual(self.b.load("42")["install"]["anr"], {"on": True, "current": None})
        self.serve()
        self.download()
        self.assertEqual(self.b.load("42")["install"]["anr"], {"on": True, "current": False})

    def test_what_isnt_ours_stays(self):
        custom = self.umu / "OptiScaler/custom"
        custom.mkdir(parents=True)
        target = self.umu.parent / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("k")
        (custom / NAME).symlink_to(target)
        self.b.game_anr("42", False)
        self.assertFalse((custom / NAME).is_symlink())  # the link goes, never what it points to
        self.assertEqual((target / "keep.txt").read_text(), "k")
        (custom / NAME).write_text("a file of that name")
        self.b.game_anr("42", False)
        self.assertTrue((custom / NAME).is_file())  # not ANR FG's: it stays
        self.serve()
        self.download()
        with self.assertRaises(Unavailable):
            self.b.game_anr("42", True)
        self.assertEqual((custom / NAME).read_text(), "a file of that name")

    def test_uninstalling_optiscaler_takes_it_too(self):
        self.serve()
        self.download()
        self.b.game_anr("42", True)
        self.b.game_uninstall("42")
        self.assertFalse((self.umu / "OptiScaler").exists())
        with self.assertRaises(Unavailable):
            self.b.game_anr("42", True)  # no OptiScaler in the game

    def test_refuses(self):
        with self.assertRaises(InvalidArgument):
            self.b.game_anr("42", "yes")
        with self.assertRaises(InvalidArgument):
            self.b.game_anr("x", True)

    def test_busy_then_teardown(self):
        release_ = threading.Event()

        def slow(folder, http, archive, task, stop, swap_lock):
            while not stop.is_set() and not release_.is_set():
                time.sleep(0.01)
            raise self.a.Error("Download stopped")
        self.a.update = slow
        self.assertEqual(self.b.anr_download(), {"started": True, "busy": False})
        self.assertEqual(self.b.anr_download(), {"started": False, "busy": True})
        with self.assertRaises(Unavailable):
            self.b.anr_remove()  # not while it downloads
        self.b.teardown()
        self.assertFalse(self.b._anr_thread.is_alive())
        self.assertEqual(self.b.anr_status(), {"installed": None, "busy": False, "error": "Download stopped"})


if __name__ == "__main__":
    unittest.main()
