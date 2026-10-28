import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_game, fake_steam, load_backend, submodule, swap_dir, tree

MARKER = ".invasor-opti.version"


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
        self.ini = self.tmp / "stock.ini"
        self.ini.write_text("[stock]\n")
        self.umu = self.tmp / "pfx/umu"

    def test_install_into_nothing(self):
        self.assertEqual(self.i.state(self.umu), {"installed": False, "version": None, "dll": None, "flavor": None})
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        self.assertEqual(tree(self.umu), {
            MARKER: (self.build / MARKER).read_bytes(),
            "OptiScaler": "dir",
            "OptiScaler.dll": b"opti",
            "OptiScaler.ini": b"[stock]\n",
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
        self.i.install(self.build, self.umu, self.ini, "winmm", swap_dir)
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
        self.assertEqual(self.i.state(self.umu), {"installed": True, "version": None, "dll": "dxgi", "flavor": "optiscaler"})
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
            self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        self.assertEqual(tree(self.umu), {"dxgi.dll": b"dxvk"})
        self.i.install(self.build, self.umu, self.ini, "d3d12", swap_dir)
        with self.assertRaises(self.i.Error):
            self.i.set_dll(self.umu, "dxgi")
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"dxgi.dll": b"dxvk"})

    def test_uninstall(self):
        self.umu.mkdir(parents=True)
        (self.umu / "keep.txt").write_text("k")
        (self.umu / ".OptiScaler.mine").mkdir()  # the user's, despite its name
        (self.umu / ".OptiScaler.mine/x").write_text("x")
        (self.umu / ".OptiScaler.old-1a2b3c4d").mkdir()  # left by a crash in swap_dir
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        self.assertFalse((self.umu / ".OptiScaler.old-1a2b3c4d").exists())
        (self.umu / ".OptiScaler.copy").mkdir()  # left by a crash before the swap
        (self.umu / "d3d12.dll").symlink_to("OptiScaler.dll")
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"keep.txt": b"k", ".OptiScaler.mine": "dir", ".OptiScaler.mine/x": b"x"})
        self.i.uninstall(self.umu)  # nothing left: no error

    def test_update_keeps_what_the_build_doesnt_bring(self):
        self.i.install(self.build, self.umu, self.ini, "d3d12", swap_dir)
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
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        with self.assertRaises(self.i.Error):
            self.i.update(self.tmp / "nothing", self.umu)

    def test_bad_dll_and_no_build(self):
        with self.assertRaises(ValueError):
            self.i.install(self.build, self.umu, self.ini, "../evil", swap_dir)
        with self.assertRaises(self.i.Error):
            self.i.install(self.tmp / "nothing", self.umu, self.ini, "dxgi", swap_dir)
        with self.assertRaises(self.i.Error):
            self.i.set_dll(self.umu, "dxgi")  # not installed
        self.assertFalse(self.umu.exists())


class LaunchOptionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i = submodule(load_backend(), "install")

    def test_the_line_is_found_among_others(self):
        has = self.i.in_launch_options
        self.assertTrue(has("WINE_OPTISCALER_NAME=dxgi.dll %command%", "dxgi"))
        self.assertTrue(has("WINE_OPTISCALER_NAME=dxgi.dll MAKO_PROFILE='x' ~/mako-launch %command% --skip", "dxgi"))
        self.assertTrue(has('PROTON_LOG=1 WINE_OPTISCALER_NAME="DXGI.DLL" gamemoderun %command%', "dxgi"))
        for options in ("", "%command%", "WINE_OPTISCALER_NAME=dxgi.dll", "%command% WINE_OPTISCALER_NAME=dxgi.dll",
                        "WINE_OPTISCALER_NAME=d3d12.dll %command%", "XWINE_OPTISCALER_NAME=dxgi.dll %command%",
                        "WINE_OPTISCALER_NAME=dxgi.dll.bak %command%", None):
            self.assertFalse(has(options, "dxgi"), options)

    def test_with_dll_keeps_the_rest_of_the_line(self):
        w = self.i.with_dll
        self.assertEqual(w("", "dxgi"), "WINE_OPTISCALER_NAME=dxgi.dll %command%")
        self.assertEqual(w(None, "dxgi"), "WINE_OPTISCALER_NAME=dxgi.dll %command%")
        # Without %command% the line is the game's arguments: they stay after it.
        self.assertEqual(w("-novid -dx12", "winmm"), "WINE_OPTISCALER_NAME=winmm.dll %command% -novid -dx12")
        self.assertEqual(w("PROTON_LOG=1 gamemoderun %command% --skip", "dxgi"),
                         "WINE_OPTISCALER_NAME=dxgi.dll PROTON_LOG=1 gamemoderun %command% --skip")
        self.assertEqual(w('A=1 WINE_OPTISCALER_NAME="d3d12.dll" %command%', "dxgi"), "A=1 WINE_OPTISCALER_NAME=dxgi.dll %command%")
        self.assertEqual(w("WINE_OPTISCALER_NAME=dxgi.dll %command%", "dxgi"), "WINE_OPTISCALER_NAME=dxgi.dll %command%")
        # One after %command% is the game's argument, not ours.
        self.assertEqual(w("%command% WINE_OPTISCALER_NAME=x", "dxgi"), "WINE_OPTISCALER_NAME=dxgi.dll %command% WINE_OPTISCALER_NAME=x")
        self.assertEqual(w("XWINE_OPTISCALER_NAME=a %command%", "dxgi"), "WINE_OPTISCALER_NAME=dxgi.dll XWINE_OPTISCALER_NAME=a %command%")
        for options in ("", "-novid", "A=1 %command%", 'WINE_OPTISCALER_NAME="winmm.dll" %command%'):
            self.assertTrue(self.i.in_launch_options(w(options, "d3d12"), "d3d12"), options)
        with self.assertRaises(ValueError):
            w("", "kernel32")

    def test_launch_options_from_steams_files(self):
        """Without Steam's answer, the backend reads shortcuts.vdf and localconfig.vdf."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        b = load_backend()
        b.setup(FakeCtx())
        fake_steam(tmp)
        config = tmp / "userdata/1/config"
        config.mkdir(parents=True)

        def entry(appid, options):  # one shortcut, binary VDF
            return (b"\x00" + b"0\x00" + b"\x02appid\x00" + struct.pack("<i", appid) +
                    b"\x01LaunchOptions\x00" + options.encode() + b"\x00" + b"\x08")
        (config / "shortcuts.vdf").write_bytes(b"\x00shortcuts\x00" + entry(-399969654, "A=1 %command%") + b"\x08\x08")
        (config / "localconfig.vdf").write_text(
            '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {'
            ' "100" { "LaunchOptions" "WINE_OPTISCALER_NAME=dxgi.dll %command%" } "200" { "Playtime" "5" } } } } } }')
        self.assertEqual(b._launch_options(str(-399969654 & 0xFFFFFFFF)), "A=1 %command%")
        self.assertEqual(b._launch_options("100"), "WINE_OPTISCALER_NAME=dxgi.dll %command%")
        self.assertEqual(b._launch_options("200"), "")  # known, without options
        self.assertIsNone(b._launch_options("300"))
        # Files Steam didn't write: nothing to tell, never an error.
        for junk in (b"\x01shortcuts\x00text\x00\x08", b"\x00a\x00" * 5000, b"\xff"):
            (config / "shortcuts.vdf").write_bytes(junk)
            self.assertEqual(b._launch_options("100"), "WINE_OPTISCALER_NAME=dxgi.dll %command%")
        # Steam's own answer wins: it doesn't lag as localconfig.vdf does.
        b.ctx.steam.options = {"100": "B=2 %command%"}
        self.assertEqual(b._launch_options("100"), "B=2 %command%")


class GameInstallTest(unittest.TestCase):
    """The backend's methods: a fake Steam, the Global build and file in a tmp."""

    def setUp(self):
        self.b, tmp, self.umu = fake_game(self)

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
        # The game got OptiScaler's own file (the build has none) with only OptiDllPath set.
        stock = self.b.OPTISCALER_INI.read_bytes()
        self.assertEqual((self.umu / "OptiScaler.ini").read_bytes(),
                         stock.replace(b"OptiDllPath=auto", rb"OptiDllPath=c:\windows\system32\umu\OptiScaler", 1))
        # A game's own file stays as it is.
        self.b.game_uninstall("42")
        self.umu.mkdir(parents=True)
        (self.umu / "OptiScaler.ini").write_text("[FrameGen]\nEnabled=false\n")
        self.b.game_install("42", "d3d12")
        self.assertEqual((self.umu / "OptiScaler.ini").read_text(), "[FrameGen]\nEnabled=false\n")
        self.assertEqual(self.b.game_set_dll("42", "winmm")["install"]["dll"], "winmm")
        with self.assertRaises(InvalidArgument):
            self.b.game_set_dll("42", "kernel32")
        r = self.b.game_uninstall("42")
        self.assertEqual((r["state"], r["install"]["installed"]), ("no_ini", False))
        self.assertFalse(self.umu.exists())  # it wasn't there before

    def test_apply_launch_options(self):
        steam = self.b.ctx.steam
        steam.options = {"42": "-novid"}
        self.make_build()
        with self.assertRaises(Unavailable):
            self.b.game_apply_launch("42")  # not installed yet
        r = self.b.game_install("42", "dxgi")
        self.assertFalse(r["install"]["can_apply"])  # this Steam has no call to write them
        self.assertFalse(r["install"]["launch_ok"])
        steam.can_set = True
        self.assertFalse(self.b.load("42")["install"]["can_apply"])  # Steam's "no" is remembered a while
        self.b._supports_cache["ttl"] = 0  # the wait is over
        self.assertTrue(self.b.load("42")["install"]["can_apply"])
        r = self.b.game_apply_launch("42")
        self.assertEqual(steam.set, [("42", "WINE_OPTISCALER_NAME=dxgi.dll %command% -novid")])
        self.assertTrue(r["verified"])
        self.assertTrue(r["install"]["launch_ok"])
        # Without Steam's own answer nothing is written: the files may lag behind what the user just set.
        steam.options = None
        with self.assertRaisesRegex(Unavailable, "didn't tell"):
            self.b.game_apply_launch("42")
        self.assertEqual(len(steam.set), 1)

    def test_no_prefix(self):
        r = self.b.load("7")
        self.assertEqual((r["state"], r["install"]), ("no_prefix", None))
        self.make_build()
        with self.assertRaises(Unavailable):
            self.b.game_install("7", "dxgi")


if __name__ == "__main__":
    unittest.main()
