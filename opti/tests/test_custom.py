import json
import shutil
import struct
import tempfile
import types
import unittest
from pathlib import Path

from tests.loader import FakeCtx, InvalidArgument, Unavailable, fake_steam, load_backend, submodule, swap_dir


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
        self.c.store(self.kept, "fsr4", str(self.src / "amdvkffx64.dll"), swap_dir)
        s = self.c.status(self.kept)
        self.assertEqual([f["name"] for f in s["fsr4"]], ["amdxcffx64.dll", "amdvkffx64.dll"])
        self.assertEqual(s["fsr4"][0]["version"], "2.4.0.4370")

    def test_fsr4_alone_and_wrong_name(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"), swap_dir)
        self.assertEqual([f["name"] for f in self.c.status(self.kept)["fsr4"]], ["amdxcffx64.dll"])
        (self.src / "other.dll").write_bytes(fake_dll())
        with self.assertRaises(self.c.Error):
            self.c.store(self.kept, "fsr4", str(self.src / "other.dll"), swap_dir)

    def test_refuses_what_isnt_a_dll(self):
        (self.src / "amdxcffx64.dll").write_bytes(b"MZ not really")
        with self.assertRaises(self.c.Error):
            self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"), swap_dir)
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "fsr4", "relative.dll", swap_dir)
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "nope", str(self.src / "amdxcffx64.dll"), swap_dir)
        self.assertIsNone(self.c.status(self.kept)["fsr4"])

    def test_fsr4_in_a_game(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll("AMD", (2, 4, 0, 4370)))
        (self.src / "amdvkffx64.dll").write_bytes(fake_dll("AMD VK", (2, 4, 0, 4370)))
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"), swap_dir)
        self.assertEqual(self.c.game_state(self.umu), {"fsr4": False})
        game = self.umu / "OptiScaler"
        game.joinpath("libxess.dll").write_text("other")
        self.c.enable(self.kept, self.umu, "fsr4")
        self.assertEqual(self.c.game_state(self.umu), {"fsr4": True})
        self.assertEqual(sorted(p.name for p in game.iterdir()), ["amdvkffx64.dll", "amdxcffx64.dll", "libxess.dll"])
        self.c.disable(self.umu, "fsr4")
        self.assertEqual([p.name for p in game.iterdir()], ["libxess.dll"])

    def test_enable_needs_both_ends(self):
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "fsr4")  # nothing kept
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"), swap_dir)
        (self.umu / "OptiScaler.dll").unlink()
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "fsr4")  # OptiScaler not installed

    def test_remove(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "fsr4", str(self.src / "amdxcffx64.dll"), swap_dir)
        self.c.remove(self.kept, "fsr4")
        self.assertIsNone(self.c.status(self.kept)["fsr4"])
        self.assertEqual([p.name for p in self.kept.iterdir()], [])  # no temp left



class GameExtraTest(unittest.TestCase):
    """The backend: the FSR 4 driver DLLs turned on and off in a game."""

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
        build = self.b.GLOBAL_DIR / "optiscaler"
        (build / "OptiScaler").mkdir(parents=True)
        (build / "OptiScaler.dll").write_bytes(b"opti")
        (build / ".invasor-opti.version").write_text(json.dumps({"tag": "t"}))
        self.b.game_install("42", "dxgi")
        self.src = tmp / "amdxcffx64.dll"
        self.src.write_bytes(fake_dll("AMD", (2, 4, 0, 4370)))

    def test_on_and_off(self):
        r = self.b.load("42")
        self.assertEqual((r["custom"], r["install"]["extras"]), ({"fsr4": False}, {"fsr4": False}))
        with self.assertRaises(Unavailable):
            self.b.game_extra("42", "fsr4", True)  # not kept in Global
        self.b.custom_set("fsr4", str(self.src))
        self.assertTrue(self.b.game_extra("42", "fsr4", True)["install"]["extras"]["fsr4"])
        self.assertTrue((self.umu / "OptiScaler/amdxcffx64.dll").is_file())
        self.assertFalse(self.b.game_extra("42", "fsr4", False)["install"]["extras"]["fsr4"])
        self.assertFalse((self.umu / "OptiScaler/amdxcffx64.dll").exists())

    def test_refuses(self):
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "nope", True)
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "fsr4", "yes")


if __name__ == "__main__":
    unittest.main()
