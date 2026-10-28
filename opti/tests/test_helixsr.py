import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_steam, load_backend, submodule, swap_dir

MARKER = ".invasor-opti.version"
SCALER = "helixsr-scaler"
SIZE = (1 << 20) + 64  # over helixsr.MIN_SIZE


def tailed(magic, at):
    """A DLL's bytes with `magic` `at` bytes from its end, as HelixSR's setup leaves it."""
    data = bytearray(SIZE)
    data[SIZE - at:SIZE - at + len(magic)] = magic
    return bytes(data)


def make_out(out, tag="v3.1.0"):
    """What helixsr-fetch.sh leaves in --out."""
    out = Path(out)
    (out / "OptiScaler/D3D12_OptiScaler").mkdir(parents=True, exist_ok=True)
    (out / "HelixSR").mkdir(exist_ok=True)
    (out / "Licenses").mkdir(exist_ok=True)
    (out / "OptiScaler.dll").write_bytes(b"scaler")
    (out / "OptiScaler.ini").write_text("[HelixSR]\r\nInt8=auto\r\n[Libraries]\r\nOptiDllPath=auto\r\nFfxDx12SRPath=auto\r\n")
    (out / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll").write_bytes(tailed(b"HLXFSR4D", 24))
    (out / "OptiScaler/amd_fidelityfx_upscaler_dx12.amd.dll").write_bytes(b"amd")
    (out / "OptiScaler/D3D12_OptiScaler/D3D12Core.dll").write_bytes(b"core")
    (out / "HelixSR/helixsr.dll").write_bytes(tailed(b"HXSRNET1", 32))
    (out / "HelixSR/helixsr.ini").write_text("[Upscaling]\n")
    (out / "Licenses/OptiScaler_LICENSE.txt").write_text("GPL")
    (out / "VERSION").write_text(tag + "\n")
    return out


class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = submodule(load_backend(), "helixsr")
        cls.n = submodule(load_backend(), "nightly")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_store(self):
        out = make_out(self.tmp / "out")
        self.h.store(out, self.tmp / "g/helixsr", swap_dir)
        kept = self.tmp / "g/helixsr"
        self.assertEqual(self.n.read_marker(kept)["tag"], "v3.1.0")
        self.assertEqual(self.n.read_marker(kept)["version"], "3.1.0")
        self.assertEqual(self.n.flavor(kept), SCALER)
        self.assertFalse((kept / "VERSION").exists())
        self.assertTrue((kept / "HelixSR/helixsr.dll").is_file())

    def test_check_refuses_an_incomplete_build(self):
        def broken(change):
            out = make_out(self.tmp / "out")
            change(out)
            with self.assertRaises(self.h.Error):
                self.h.check(out)
            shutil.rmtree(out)
        broken(lambda o: (o / "OptiScaler.dll").unlink())
        broken(lambda o: (o / "HelixSR/helixsr.dll").write_bytes(tailed(b"NOTHING!", 32)))
        broken(lambda o: (o / "HelixSR/helixsr.dll").write_bytes(b"HXSRNET1" + b"\0" * 24))  # too small
        broken(lambda o: (o / "OptiScaler/amd_fidelityfx_upscaler_dx12.dll").write_bytes(tailed(b"HXSRNET1", 24)))
        broken(lambda o: (o / "VERSION").unlink())
        broken(lambda o: (o / "VERSION").write_text("v1\nv2\n"))
        broken(lambda o: (o / "OptiScaler/link.dll").symlink_to("/etc/passwd"))
        self.assertEqual(self.h.check(make_out(self.tmp / "out")), "v3.1.0")

    def test_flavor_of_old_markers(self):
        self.assertEqual(self.n.flavor(self.tmp), "optiscaler")  # no marker
        (self.tmp / MARKER).write_text('{"tag": "nightly-1"}')
        self.assertEqual(self.n.flavor(self.tmp), "optiscaler")
        (self.tmp / MARKER).write_text("not json")
        self.assertEqual(self.n.flavor(self.tmp), "optiscaler")


def tree(folder):
    """{relative path: link target, "dir" or file bytes}."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        rel = str(p.relative_to(folder))
        out[rel] = ("-> " + os.readlink(p)) if p.is_symlink() else "dir" if p.is_dir() else p.read_bytes()
    return out


class ScalerInstallTest(unittest.TestCase):
    """install.py with a HelixSR Scaler build."""

    @classmethod
    def setUpClass(cls):
        b = load_backend()
        cls.i, cls.h = submodule(b, "install"), submodule(b, "helixsr")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.build = self.tmp / "helixsr"
        self.h.store(make_out(self.tmp / "out"), self.build, swap_dir)
        self.nightly = self.tmp / "nightly"
        (self.nightly / "OptiScaler").mkdir(parents=True)
        (self.nightly / "OptiScaler.dll").write_bytes(b"opti")
        (self.nightly / MARKER).write_text('{"tag": "nightly-1"}')
        self.ini = self.build / "OptiScaler.ini"
        self.umu = self.tmp / "umu"

    def test_install_update_uninstall(self):
        self.umu.mkdir()
        (self.umu / "keep.txt").write_text("k")
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        t = tree(self.umu)
        for p in ("OptiScaler.dll", "OptiScaler.ini", "OptiScaler/amd_fidelityfx_upscaler_dx12.amd.dll",
                  "HelixSR/helixsr.dll", "HelixSR/helixsr.ini", "Licenses/OptiScaler_LICENSE.txt", MARKER):
            self.assertIn(p, t)
        self.assertEqual(t["dxgi.dll"], "-> OptiScaler.dll")
        s = self.i.state(self.umu)
        self.assertEqual((s["flavor"], s["version"]["tag"]), (SCALER, "v3.1.0"))
        # A newer build: the game's settings stay.
        (self.umu / "HelixSR/helixsr.ini").write_text("[mine]\n")
        (self.umu / "HelixSR/helixsr.log").write_text("log")
        (self.build / "HelixSR/helixsr.dll").write_bytes(b"new dlss")
        (self.build / "HelixSR/helixsr.ini").write_text("[stock]\n")
        self.i.update(self.build, self.umu)
        t = tree(self.umu)
        self.assertEqual((t["HelixSR/helixsr.dll"], t["HelixSR/helixsr.ini"]), (b"new dlss", b"[mine]\n"))
        with self.assertRaises(self.i.Error):
            self.i.update(self.nightly, self.umu)  # another flavor
        for build in (self.nightly, self.build):  # installed already: update it, or uninstall first
            with self.assertRaises(self.i.Error):
                self.i.install(build, self.umu, self.ini, "dxgi", swap_dir)
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"keep.txt": b"k"})

    def test_ini(self):
        # An ini of the official OptiScaler left in the game is replaced by HelixSR Scaler's; one of its own stays.
        self.umu.mkdir()
        (self.umu / "OptiScaler.ini").write_text("[Libraries]\n")
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        self.assertEqual((self.umu / "OptiScaler.ini").read_bytes(), self.ini.read_bytes())
        self.assertEqual((self.umu / "OptiScaler.ini.bak").read_text(), "[Libraries]\n")  # kept aside
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"OptiScaler.ini.bak": b"[Libraries]\n"})  # the user's: it stays
        (self.umu / "OptiScaler.ini.bak").unlink()
        (self.umu / "OptiScaler.ini").write_text("[helixsr]\nInt8=on\n")
        self.i.install(self.build, self.umu, self.ini, "dxgi", swap_dir)
        self.assertEqual((self.umu / "OptiScaler.ini").read_text(), "[helixsr]\nInt8=on\n")

    def test_the_nightly_never_takes_helixsr_folders(self):
        self.umu.mkdir()
        (self.umu / "HelixSR").mkdir()
        (self.umu / "HelixSR/helixsr.dll").write_bytes(b"put by hand")
        self.i.install(self.nightly, self.umu, self.ini, "dxgi", swap_dir)
        self.i.uninstall(self.umu)
        self.assertEqual(tree(self.umu), {"HelixSR": "dir", "HelixSR/helixsr.dll": b"put by hand"})


class GameScalerTest(unittest.TestCase):
    """The backend: HelixSR Scaler in a game, with its two ini keys and the FSR 4 driver DLLs."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        steam = tmp / "Steam"
        (steam / "steamapps/compatdata/42/pfx/drive_c").mkdir(parents=True)
        self.umu = steam / "steamapps/compatdata/42/pfx/drive_c/windows/system32/umu"
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = tmp / "invasor-opti"
        fake_steam(steam)
        self.tmp = tmp

    def keys(self):
        text, _, _ = self.b._read(self.umu / "OptiScaler.ini")
        return [self.b._find_key(text, s, k)["value"] for s, k, _ in (*self.b.INSTALL_INI, *self.b.SCALER_INI)]

    def test_install(self):
        with self.assertRaisesRegex(Unavailable, "compile HelixSR"):
            self.b.game_install("42", "dxgi", SCALER)
        with self.assertRaises(InvalidArgument):
            self.b.game_install("42", "dxgi", "other")
        self.b.helixsr.store(make_out(self.tmp / "out"), self.b._helixsr_dir(), swap_dir)
        from tests.test_custom import fake_dll
        (self.tmp / "amdxcffx64.dll").write_bytes(fake_dll("AMD", (2, 4, 0, 4370)))
        self.b.custom_set("fsr4", str(self.tmp / "amdxcffx64.dll"))
        r = self.b.game_install("42", "dxgi", SCALER)
        self.assertEqual((r["install"]["flavor"], r["helixsr"]["tag"], r["install"]["extras"]), (SCALER, "v3.1.0", {"fsr4": True}))
        self.assertEqual(self.keys(), [v for _, _, v in (*self.b.INSTALL_INI, *self.b.SCALER_INI)])
        self.assertIn(b"\r\n", (self.umu / "OptiScaler.ini").read_bytes())  # its line ends stay
        text = (self.umu / "OptiScaler.ini").read_text()
        self.assertEqual(self.b._find_key(text, "HelixSR", "Int8")["value"], "auto")  # the package's own file
        self.assertTrue((self.umu / "HelixSR/helixsr.dll").is_file())
        self.b.game_uninstall("42")
        self.assertFalse(self.umu.exists())

    def test_a_half_install_is_undone(self):
        out = make_out(self.tmp / "out")
        (out / "OptiScaler.ini").write_text("[HelixSR]\n[Libraries]\nOptiDllPath=auto\n")  # no FfxDx12SRPath
        self.b.helixsr.store(out, self.b._helixsr_dir(), swap_dir)
        with self.assertRaisesRegex(Unavailable, "FfxDx12SRPath"):
            self.b.game_install("42", "dxgi", SCALER)
        self.assertFalse(self.umu.exists())
        with self.assertRaisesRegex(Unavailable, "isn't installed"):
            self.b.game_update("42")

    def test_update_by_flavor(self):
        self.b.helixsr.store(make_out(self.tmp / "out"), self.b._helixsr_dir(), swap_dir)
        self.b.game_install("42", "dxgi", SCALER)
        self.b.helixsr.store(make_out(self.tmp / "out2", "v3.2.0"), self.b._helixsr_dir(), swap_dir)
        r = self.b.game_update("42")
        self.assertEqual(r["install"]["version"]["tag"], "v3.2.0")


class HelixBuildTest(unittest.TestCase):
    """backend: "Download and compile" runs bin/helixsr-fetch.sh and keeps what it builds in HELIXSR_NAME."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.b = load_backend()
        self.ctx = FakeCtx()
        self.b.setup(self.ctx)
        self.b.GLOBAL_DIR = self.tmp / "invasor-opti"
        make_out(self.tmp / "made")
        self.addCleanup(self.b.teardown)

    def script(self, body):
        """A stand-in for helixsr-fetch.sh: $OUT is the --out folder."""
        path = self.tmp / "fetch.sh"
        path.write_text('while [ $# -gt 0 ]; do [ "$1" = --out ] && OUT=$2; shift; done\nmkdir -p "$OUT"\n' + body)
        self.b.HELIXSR_SCRIPT = path

    def wait(self):
        self.b._helix_thread.join(10)
        self.assertFalse(self.b._helix_thread.is_alive())
        return self.b.helixsr_status()

    def test_builds(self):
        # Its temporary files go under the build folder, removed with it.
        self.script(f'echo "[helixsr-fetch] building..."\ncase "$TMPDIR" in "$(dirname "$OUT")"/tmp) ;; *) exit 3 ;; esac\n'
                    f'mktemp -d >/dev/null\ncp -a "{self.tmp}/made/." "$OUT/"\n')
        self.assertEqual(self.b.helixsr_build(), {"started": True, "busy": False})
        s = self.wait()
        self.assertEqual((s["busy"], s["error"], s["installed"]["tag"]), (False, None, "v3.1.0"))
        self.assertEqual([p.name for p in self.b.GLOBAL_DIR.iterdir()], ["helixsr"])  # no build folder left

    def test_a_failed_build_keeps_the_last(self):
        self.b.helixsr.store(make_out(self.tmp / "out"), self.b._helixsr_dir(), swap_dir)
        self.script('echo "[helixsr-fetch] error: download failed: x" >&2\nexit 1\n')
        self.b.helixsr_build()
        s = self.wait()
        self.assertEqual((s["error"], s["installed"]["tag"]), ("Build failed: download failed: x", "v3.1.0"))

    def test_an_incomplete_build_is_refused(self):
        self.script(f'cp -a "{self.tmp}/made/." "$OUT/"\nrm "$OUT/HelixSR/helixsr.dll"\n')
        self.b.helixsr_build()
        s = self.wait()
        self.assertEqual((s["error"], s["installed"]), ("The build has no HLX DLSS with its network", None))

    def test_busy_then_teardown(self):
        self.script("sleep 30\n")
        self.assertTrue(self.b.helixsr_build()["started"])
        self.assertEqual(self.b.helixsr_build(), {"started": False, "busy": True})
        self.b.teardown()
        self.assertFalse(self.b._helix_thread.is_alive())
        self.assertEqual(self.b.helixsr_status(), {"installed": None, "busy": False, "error": "Build stopped"})

    def test_not_while_a_game_runs(self):
        self.script("exit 0\n")
        self.ctx.game = types.SimpleNamespace(selected=None, running={"appid": "1"})
        with self.assertRaises(Unavailable):
            self.b.helixsr_build()


class HelixCheckTest(unittest.TestCase):
    """backend: helixsr_check compares the kept build with GitHub's latest stable tag (cached)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = self.tmp / "invasor-opti"
        self.http = self.b.ctx.http
        self.answer("v3.1.0")

    def answer(self, tag):
        """GitHub's answer for the latest stable HelixSR (None: offline)."""
        self.http.answers[self.b.HELIXSR_RELEASE_API] = Unavailable("couldn't get it: offline") if tag is None else {"tag_name": tag}

    @property
    def asked(self):
        return len(self.http.asked)

    def keep(self, tag):
        self.b.helixsr.store(make_out(self.tmp / f"out-{tag}", tag), self.b._helixsr_dir(), swap_dir)

    def test_nothing_kept(self):
        self.assertEqual(self.b.helixsr_check(), {"installed": None, "latest": "v3.1.0", "update": False})

    def test_up_to_date_and_older(self):
        self.keep("v3.1.0")
        self.assertEqual(self.b.helixsr_check(), {"installed": "3.1.0", "latest": "v3.1.0", "update": False})
        self.b._latest_cache["ttl"] = 0  # the hour is over
        self.answer("v3.1.1")
        self.assertTrue(self.b.helixsr_check()["update"])

    def test_cached_and_offline(self):
        self.keep("v3.0.0")
        self.answer(None)
        self.assertEqual(self.b.helixsr_check(), {"installed": "3.0.0", "latest": None, "update": False})
        self.answer("v3.1.0")
        self.b.helixsr_check()
        self.assertEqual(self.asked, 1)  # a failed check waits before asking again
        self.b._latest_cache["ttl"] = 0  # the retry wait is over
        self.assertTrue(self.b.helixsr_check()["update"])
        self.b.helixsr_check()
        self.assertEqual(self.asked, 2)


if __name__ == "__main__":
    unittest.main()
