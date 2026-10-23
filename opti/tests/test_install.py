import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_steam, load_backend, submodule

MARKER = ".invasor-opti.version"


def tree(folder):
    """{relative path: link target, "dir" or file bytes}."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        rel = str(p.relative_to(folder))
        out[rel] = ("-> " + os.readlink(p)) if p.is_symlink() else "dir" if p.is_dir() else p.read_bytes()
    return out


class InstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i = submodule(load_backend(), "install")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.build = self.tmp / "build"
        (self.build / "OptiScaler").mkdir(parents=True)
        (self.build / "OptiScaler.dll").write_bytes(b"opti")
        (self.build / "OptiScaler/libxess.dll").write_bytes(b"xess")
        (self.build / "OptiScaler.ini").write_text("[stock]\n")
        (self.build / "setup_linux.sh").write_text("#!/bin/sh\n")
        (self.build / MARKER).write_text(json.dumps({"tag": "nightly-1", "version": "v10", "date": "20261023"}))
        self.global_ini = self.tmp / "global.ini"
        self.global_ini.write_text("[global]\n")
        self.umu = self.tmp / "pfx/umu"

    def test_install_into_nothing(self):
        self.assertEqual(self.i.state(self.umu), {"installed": False, "version": None, "dll": None})
        self.i.install(self.build, self.umu, self.global_ini, "dxgi")
        self.assertEqual(tree(self.umu), {
            MARKER: (self.build / MARKER).read_bytes(),
            "OptiScaler": "dir",
            "OptiScaler.dll": b"opti",
            "OptiScaler.ini": b"[global]\n",
            "OptiScaler/libxess.dll": b"xess",
            "dxgi.dll": "-> OptiScaler.dll",
        })
        s = self.i.state(self.umu)
        self.assertEqual((s["installed"], s["dll"], s["version"]["tag"]), (True, "dxgi", "nightly-1"))

    def test_keeps_the_games_ini_and_others_files(self):
        self.umu.mkdir(parents=True)
        (self.umu / "OptiScaler.ini").write_text("[mine]\n")
        (self.umu / "other.dll").write_bytes(b"x")
        (self.umu / "OptiScaler").mkdir()
        (self.umu / "OptiScaler/old.dll").write_bytes(b"old")
        self.i.install(self.build, self.umu, self.global_ini, "winmm")
        t = tree(self.umu)
        self.assertEqual(t["OptiScaler.ini"], b"[mine]\n")
        self.assertEqual(t["other.dll"], b"x")
        self.assertNotIn("OptiScaler/old.dll", t)  # the folder is the new build's
        self.assertEqual(t["winmm.dll"], "-> OptiScaler.dll")

    def test_set_dll_replaces_every_link(self):
        # As set up by hand in a real game: two names, no marker.
        self.umu.mkdir(parents=True)
        (self.umu / "OptiScaler.dll").write_bytes(b"opti")
        (self.umu / "dxgi.dll").symlink_to("OptiScaler.dll")
        (self.umu / "d3d12.dll").symlink_to("OptiScaler.dll")
        self.assertEqual(self.i.state(self.umu), {"installed": True, "version": None, "dll": "dxgi"})
        self.i.set_dll(self.umu, "dbghelp")
        self.assertEqual(sorted(tree(self.umu)), ["OptiScaler.dll", "dbghelp.dll"])
        self.assertEqual(self.i.state(self.umu)["dll"], "dbghelp")

    def test_a_copy_counts_as_optiscaler(self):
        self.umu.mkdir(parents=True)
        (self.umu / "OptiScaler.dll").write_bytes(b"opti")
        (self.umu / "dxgi.dll").write_bytes(b"opti")
        self.assertEqual(self.i.state(self.umu)["dll"], "dxgi")
        self.i.set_dll(self.umu, "d3d12")
        self.assertEqual(tree(self.umu), {"OptiScaler.dll": b"opti", "d3d12.dll": "-> OptiScaler.dll"})

    def test_someone_elses_dll_is_never_touched(self):
        self.umu.mkdir(parents=True)
        (self.umu / "dxgi.dll").write_bytes(b"dxvk")
        with self.assertRaises(self.i.Error):
            self.i.install(self.build, self.umu, self.global_ini, "dxgi")
        self.assertEqual(tree(self.umu), {"dxgi.dll": b"dxvk"})
        self.i.install(self.build, self.umu, self.global_ini, "d3d12")
        with self.assertRaises(self.i.Error):
            self.i.set_dll(self.umu, "dxgi")
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"dxgi.dll": b"dxvk"})

    def test_uninstall(self):
        self.umu.mkdir(parents=True)
        (self.umu / "keep.txt").write_text("k")
        self.i.install(self.build, self.umu, self.global_ini, "dxgi")
        (self.umu / "d3d12.dll").symlink_to("OptiScaler.dll")
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"keep.txt": b"k"})
        self.i.uninstall(self.umu)  # nothing left: no error

    def test_update_keeps_what_the_build_doesnt_bring(self):
        self.i.install(self.build, self.umu, self.global_ini, "d3d12")
        (self.umu / "OptiScaler.ini").write_text("[edited]\n")
        (self.umu / "OptiScaler/helixsr.dll").write_bytes(b"helix")
        (self.umu / "OptiScaler/plugins").mkdir()
        (self.umu / "OptiScaler/plugins/mine.asi").write_bytes(b"mine")
        # A newer build.
        (self.build / "OptiScaler.dll").write_bytes(b"opti2")
        (self.build / "OptiScaler/libxess.dll").write_bytes(b"xess2")
        (self.build / "OptiScaler/D3D12_OptiScaler").mkdir()
        (self.build / "OptiScaler/D3D12_OptiScaler/D3D12Core.dll").write_bytes(b"core")
        (self.build / MARKER).write_text(json.dumps({"tag": "nightly-2"}))
        self.i.update(self.build, self.umu)
        t = tree(self.umu)
        self.assertEqual((t["OptiScaler.dll"], t["OptiScaler/libxess.dll"], t["OptiScaler/D3D12_OptiScaler/D3D12Core.dll"]), (b"opti2", b"xess2", b"core"))
        self.assertEqual((t["OptiScaler.ini"], t["OptiScaler/helixsr.dll"], t["OptiScaler/plugins/mine.asi"]), (b"[edited]\n", b"helix", b"mine"))
        self.assertEqual(t["d3d12.dll"], "-> OptiScaler.dll")
        self.assertEqual(self.i.state(self.umu)["version"]["tag"], "nightly-2")

    def test_update_turns_a_copy_into_a_link(self):
        # Put there by hand: dxgi.dll a copy of OptiScaler.dll, no marker.
        self.umu.mkdir(parents=True)
        (self.umu / "OptiScaler.dll").write_bytes(b"old")
        (self.umu / "dxgi.dll").write_bytes(b"old")
        (self.umu / "winmm.dll").write_bytes(b"someone else's")
        self.i.update(self.build, self.umu)
        t = tree(self.umu)
        self.assertEqual((t["OptiScaler.dll"], t["dxgi.dll"], t["winmm.dll"]), (b"opti", "-> OptiScaler.dll", b"someone else's"))
        self.assertEqual(self.i.state(self.umu)["version"]["tag"], "nightly-1")

    def test_update_needs_both_ends(self):
        with self.assertRaises(self.i.Error):
            self.i.update(self.build, self.umu)  # not installed
        self.i.install(self.build, self.umu, self.global_ini, "dxgi")
        with self.assertRaises(self.i.Error):
            self.i.update(self.tmp / "nothing", self.umu)

    def test_bad_dll_and_no_build(self):
        with self.assertRaises(ValueError):
            self.i.install(self.build, self.umu, self.global_ini, "../evil")
        with self.assertRaises(self.i.Error):
            self.i.install(self.tmp / "nothing", self.umu, self.global_ini, "dxgi")
        with self.assertRaises(self.i.Error):
            self.i.set_dll(self.umu, "dxgi")  # not installed
        self.assertFalse(self.umu.exists())


class GameInstallTest(unittest.TestCase):
    """The backend's methods: a fake Steam, the Global build and file in a tmp."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        steam = tmp / "Steam"
        (steam / "steamapps/compatdata/42/pfx/drive_c").mkdir(parents=True)
        self.umu = steam / "steamapps/compatdata/42/pfx/drive_c/windows/system32/umu"
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = tmp / "invasor-opti"
        fake_steam(self.b, steam)

    def make_build(self):
        build = self.b.GLOBAL_DIR / "optiscaler"
        (build / "OptiScaler").mkdir(parents=True)
        (build / "OptiScaler.dll").write_bytes(b"opti")
        (build / MARKER).write_text('{"tag": "nightly-1"}')

    def test_flow(self):
        r = self.b.load("42")
        self.assertEqual((r["state"], r["install"]["installed"], r["build"]), ("no_ini", False, None))
        with self.assertRaises(Unavailable):
            self.b.game_install("42", "dxgi")  # nothing downloaded
        self.make_build()
        r = self.b.game_install("42", "d3d12")
        self.assertEqual((r["state"], r["install"]["dll"], r["build"]["tag"]), ("ok", "d3d12", "nightly-1"))
        # The game got the Global file, which starts as the module's own.
        self.assertEqual((self.umu / "OptiScaler.ini").read_bytes(), self.b.DEFAULTS["invasor"].read_bytes())
        self.assertEqual(self.b.game_set_dll("42", "winmm")["install"]["dll"], "winmm")
        with self.assertRaises(InvalidArgument):
            self.b.game_set_dll("42", "kernel32")
        r = self.b.game_uninstall("42")
        self.assertEqual((r["state"], r["install"]["installed"]), ("no_ini", False))
        self.assertFalse(self.umu.exists())  # it wasn't there before

    def test_no_prefix(self):
        r = self.b.load("7")
        self.assertEqual((r["state"], r["install"]), ("no_prefix", None))
        self.make_build()
        with self.assertRaises(Unavailable):
            self.b.game_install("7", "dxgi")


if __name__ == "__main__":
    unittest.main()
