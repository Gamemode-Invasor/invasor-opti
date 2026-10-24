import io
import json
import shutil
import struct
import tempfile
import types
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_steam, load_backend, submodule


def fake_dll(product="HelixSR", version=(2, 0, 0, 0), machine=0x8664, dll=True):
    """The few bytes pe.info reads: MZ header, PE signature and machine, a version resource."""
    head = bytearray(b"MZ" + b"\0" * 0x3E)
    struct.pack_into("<I", head, 0x3C, 0x40)
    # COFF header: machine, then characteristics (IMAGE_FILE_DLL unless it's an EXE) at its byte 18.
    head += b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 16 + struct.pack("<H", 0x2000 if dll else 0x0002)
    res = b""
    if version:
        a, b, c, d = version
        res += struct.pack("<IIII", 0xFEEF04BD, 0x10000, (a << 16) | b, (c << 16) | d)
    if product:
        key = "ProductName".encode("utf-16-le") + b"\0\0"
        pad = -(len(head) + len(res) + len(key)) % 4
        res += key + b"\0" * pad + product.encode("utf-16-le") + b"\0\0"
    return bytes(head + res)


class PeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pe = submodule(load_backend(), "pe")

    def test_info(self):
        self.assertEqual(self.pe.info(fake_dll("AMD Driver Based FidelityFX", (2, 4, 0, 4370))),
                         {"product": "AMD Driver Based FidelityFX", "version": "2.4.0.4370"})
        self.assertEqual(self.pe.info(fake_dll(None, None)), {"product": None, "version": None})

    def test_refuses(self):
        for data in (b"", b"MZ", b"hello" * 100, fake_dll()[:0x42], fake_dll(machine=0x14C), fake_dll(dll=False)):
            with self.assertRaises(ValueError, msg=data[:8]):
                self.pe.info(data)


class CustomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = submodule(load_backend(), "custom")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.kept = self.tmp / "custom"
        self.umu = self.tmp / "umu"
        (self.umu / "OptiScaler").mkdir(parents=True)
        (self.umu / "OptiScaler.dll").write_bytes(b"opti")

    def test_fsr4_takes_its_sibling(self):
        (self.src / "AMDXCFFX64.DLL").write_bytes(fake_dll("AMD", (2, 4, 0, 4370)))
        (self.src / "amdvkffx64.dll").write_bytes(fake_dll("AMD VK", (2, 4, 0, 4370)))
        self.c.store(self.kept, "fsr4", str(self.src / "amdvkffx64.dll"))
        s = self.c.status(self.kept)
        self.assertEqual([f["name"] for f in s["fsr4"]], ["amdxcffx64.dll", "amdvkffx64.dll"])
        self.assertEqual(s["fsr4"][0]["version"], "2.4.0.4370")
        self.assertIsNone(s["helixsr"])

    def test_fsr4_alone_and_wrong_name(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"))
        self.assertEqual([f["name"] for f in self.c.status(self.kept)["fsr4"]], ["amdxcffx64.dll"])
        (self.src / "other.dll").write_bytes(fake_dll())
        with self.assertRaises(self.c.Error):
            self.c.store(self.kept, "fsr4", str(self.src / "other.dll"))

    def test_refuses_what_isnt_a_dll(self):
        (self.src / "fake.dll").write_bytes(b"MZ not really")
        (self.src / "x.txt").write_text("x")
        for name in ("fake.dll", "x.txt"):
            with self.assertRaises(self.c.Error):
                self.c.store(self.kept, "helixsr", str(self.src / name))
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "helixsr", "relative.dll")
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "nope", str(self.src / "fake.dll"))
        self.assertIsNone(self.c.status(self.kept)["helixsr"])

    def test_helixsr_in_a_game(self):
        (self.src / "HelixSR-2.dll").write_bytes(fake_dll())
        (self.src / "helixsr.ini").write_text("[Upscaling]\n")
        self.c.store(self.kept, "helixsr", str(self.src / "HelixSR-2.dll"))
        self.assertEqual(self.c.status(self.kept)["helixsr"][0], {"name": "helixsr.dll", "product": "HelixSR", "version": "2.0.0.0"})
        self.assertEqual(self.c.game_state(self.umu), {"fsr4": False, "helixsr": False})
        game = self.umu / "OptiScaler"
        game.joinpath("helixsr.ini").write_text("[mine]\n")
        self.c.enable(self.kept, self.umu, "helixsr")
        self.assertEqual(game.joinpath("helixsr.ini").read_text(), "[mine]\n")  # the game's settings stay
        self.assertTrue(game.joinpath("helixsr.dll").is_file())
        self.assertEqual(self.c.game_state(self.umu)["helixsr"], True)
        game.joinpath("helixsr.log").write_text("log")
        game.joinpath("libxess.dll").write_text("other")
        self.c.disable(self.umu, "helixsr")
        self.assertEqual([p.name for p in game.iterdir()], ["libxess.dll"])

    def test_enable_needs_both_ends(self):
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "fsr4")  # nothing kept
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"))
        (self.umu / "OptiScaler.dll").unlink()
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "fsr4")  # OptiScaler not installed

    def test_remove(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"))
        self.c.remove(self.kept, "fsr4")
        self.assertIsNone(self.c.status(self.kept)["fsr4"])
        self.assertEqual([p.name for p in self.kept.iterdir()], [])  # no temp left


class BrowseTest(unittest.TestCase):
    def test_only_folders_and_dlls(self):
        b = submodule(load_backend(), "browse")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("b.DLL", "a.dll", "c.txt", ".hidden.dll"):
            (tmp / name).write_text("")
        (tmp / "Sub").mkdir()
        (tmp / ".git").mkdir()
        d = b.listdir(str(tmp))
        self.assertEqual(([x["name"] for x in d["dirs"]], [x["name"] for x in d["files"]]), (["Sub"], ["a.dll", "b.DLL"]))
        self.assertIsNotNone(b.listdir(str(tmp / "missing"))["error"])


class GameExtraTest(unittest.TestCase):
    """The backend: HelixSR also points FfxDx12SRPath at it, and puts it back."""

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
        build = self.b.GLOBAL_DIR / "optiscaler"
        (build / "OptiScaler").mkdir(parents=True)
        (build / "OptiScaler.dll").write_bytes(b"opti")
        (build / ".invasor-opti.version").write_text(json.dumps({"tag": "t"}))
        src = tmp / "helixsr.dll"
        src.write_bytes(fake_dll())
        self.b.custom_set("helixsr", str(src))
        self.b.game_install("42", "dxgi")

    SR_PATH = r"C:\windows\system32\umu\OptiScaler\helixsr.dll"

    def sr_path(self):
        return self.read()["value"]

    def read(self):
        """[Libraries] FfxDx12SRPath of the game's file, and the file's mtime."""
        text, _, mtime = self.b._read(self.umu / "OptiScaler.ini")
        return {**self.b._find_key(text, "Libraries", "FfxDx12SRPath"), "mtime": mtime}

    def test_helixsr_on_and_off(self):
        r = self.b.load("42")
        self.assertEqual((r["custom"], r["install"]["extras"]), ({"fsr4": False, "helixsr": True}, {"fsr4": False, "helixsr": False}))
        r = self.b.game_extra("42", "helixsr", True)
        self.assertTrue(r["install"]["extras"]["helixsr"])
        self.assertEqual(self.sr_path(), self.SR_PATH)
        self.b.game_extra("42", "helixsr", False)
        self.assertEqual(self.sr_path(), "auto")
        self.assertFalse((self.umu / "OptiScaler/helixsr.dll").exists())

    def test_a_value_of_the_users_own_stays(self):
        mtime = self.read()["mtime"]
        self.b._set_in_file(self.umu / "OptiScaler.ini", "Libraries", "FfxDx12SRPath", r"D:\mine.dll", mtime)
        self.b.game_extra("42", "helixsr", False)
        self.assertEqual(self.sr_path(), r"D:\mine.dll")

    def test_refuses(self):
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "nope", True)
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "helixsr", "yes")
        with self.assertRaises(Unavailable):
            self.b.game_extra("42", "fsr4", True)  # not kept in Global
        (self.umu / "OptiScaler.ini").write_text("[Libraries]\n")  # no FfxDx12SRPath
        with self.assertRaises(Unavailable):
            self.b.game_extra("42", "helixsr", True)
        self.assertFalse((self.umu / "OptiScaler/helixsr.dll").exists())  # nothing copied


if __name__ == "__main__":
    unittest.main()


class HelixBuildTest(unittest.TestCase):
    """backend: "Download and compile" runs bin/helixsr-fetch.sh and keeps what it builds in the helixsr slot."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.b = load_backend()
        self.ctx = FakeCtx()
        self.b.setup(self.ctx)
        self.b.GLOBAL_DIR = self.tmp / "invasor-opti"
        (self.tmp / "helixsr.dll").write_bytes(fake_dll())
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

    def test_builds_into_the_slot(self):
        # Its temporary files go under the build folder, removed with it.
        self.script(f'echo "[helixsr-fetch] building..."\ncase "$TMPDIR" in "$(dirname "$OUT")"/tmp) ;; *) exit 3 ;; esac\n'
                    f'mktemp -d >/dev/null\ncp "{self.tmp}/helixsr.dll" "$OUT/"\necho "[x]" > "$OUT/helixsr.ini"\n')
        self.assertEqual(self.b.helixsr_build(), {"started": True, "busy": False})
        self.assertEqual(self.wait(), {"busy": False, "error": None})
        kept = self.b.custom_status()["helixsr"]
        self.assertEqual([f["name"] for f in kept], ["helixsr.dll", "helixsr.ini"])
        self.assertEqual(kept[0]["product"], "HelixSR")
        self.assertEqual([p.name for p in self.b.GLOBAL_DIR.iterdir()], ["custom"])  # no build folder left

    def test_a_failed_build_keeps_the_slot(self):
        self.b.custom_set("helixsr", str(self.tmp / "helixsr.dll"))
        self.script('echo "[helixsr-fetch] error: download failed: x" >&2\nexit 1\n')
        self.b.helixsr_build()
        self.assertEqual(self.wait(), {"busy": False, "error": "Build failed: download failed: x"})
        self.assertIsNotNone(self.b.custom_status()["helixsr"])

    def test_busy_then_teardown(self):
        self.script("sleep 30\n")
        self.assertTrue(self.b.helixsr_build()["started"])
        self.assertEqual(self.b.helixsr_build(), {"started": False, "busy": True})
        self.b.teardown()
        self.assertFalse(self.b._helix_thread.is_alive())
        self.assertEqual(self.b.helixsr_status(), {"busy": False, "error": "Build stopped"})

    def test_not_while_a_game_runs(self):
        self.script("exit 0\n")
        self.ctx.game = types.SimpleNamespace(selected=None, running={"appid": "1"})
        with self.assertRaises(Unavailable):
            self.b.helixsr_build()


class HelixCheckTest(unittest.TestCase):
    """backend: helixsr_check compares the kept DLL with GitHub's latest stable tag (cached)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.b = load_backend()
        self.b.setup(FakeCtx())
        self.b.GLOBAL_DIR = self.tmp / "invasor-opti"
        self.asked = 0
        self.tag = "v2.0.0"
        n = submodule(self.b, "nightly")

        def open_url(url, timeout=30):
            self.asked += 1
            if self.tag is None:
                raise n.Error("offline")
            return io.BytesIO(json.dumps({"tag_name": self.tag}).encode())
        n.open_url = open_url

    def keep(self, version):
        (self.tmp / "helixsr.dll").write_bytes(fake_dll(version=version))
        self.b.custom_set("helixsr", str(self.tmp / "helixsr.dll"))

    def test_nothing_kept(self):
        self.assertEqual(self.b.helixsr_check(), {"installed": None, "latest": "v2.0.0", "update": False})

    def test_up_to_date_and_older(self):
        self.keep((2, 0, 0, 0))
        self.assertEqual(self.b.helixsr_check(), {"installed": "2.0.0.0", "latest": "v2.0.0", "update": False})
        self.b._latest_cache["ttl"] = 0  # the hour is over
        self.tag = "v2.0.1"
        self.assertTrue(self.b.helixsr_check()["update"])

    def test_cached_and_offline(self):
        self.keep((1, 9, 3, 0))
        self.tag = None
        self.assertEqual(self.b.helixsr_check(), {"installed": "1.9.3.0", "latest": None, "update": False})
        self.tag = "v2.0.0"
        self.b.helixsr_check()
        self.assertEqual(self.asked, 1)  # a failed check waits before asking again
        self.b._latest_cache["ttl"] = 0  # the retry wait is over
        self.assertTrue(self.b.helixsr_check()["update"])
        self.b.helixsr_check()
        self.assertEqual(self.asked, 2)
