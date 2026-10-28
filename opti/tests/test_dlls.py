import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.loader import InvalidArgument, Unavailable, fake_dll, fake_game, load_backend, submodule, swap_dir


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
        cls.c = submodule(load_backend(), "dlls")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.kept = self.tmp / "dlls"
        self.umu = self.tmp / "umu"
        (self.umu / "OptiScaler").mkdir(parents=True)
        (self.umu / "OptiScaler.dll").write_bytes(b"opti")

    def test_one_dll_per_slot(self):
        """Each slot keeps its own DLL, whatever case its name has; choosing one leaves the others."""
        (self.src / "AMDXCFFX64.DLL").write_bytes(fake_dll("AMD DX", (2, 4, 0, 4370)))
        (self.src / "nvngx_dlss.dll").write_bytes(fake_dll("NVIDIA DLSS", (3, 10, 7, 0)))
        self.c.store(self.kept, "amdxcffx64", str(self.src / "AMDXCFFX64.DLL"), swap_dir)
        self.c.store(self.kept, "nvngx_dlss", str(self.src / "nvngx_dlss.dll"), swap_dir)
        s = self.c.status(self.kept)
        self.assertEqual(s["amdxcffx64"], {"name": "amdxcffx64.dll", "product": "AMD DX", "version": "2.4.0.4370"})
        self.assertEqual(s["nvngx_dlss"]["product"], "NVIDIA DLSS")
        self.assertEqual((s["amdvkffx64"], s["nvngx_dlssd"], s["nvngx_dlssg"]), (None, None, None))
        self.assertTrue(self.c.has(self.kept, "nvngx_dlss"))
        self.assertFalse(self.c.has(self.kept, "nvngx_dlssg"))
        (self.src / "amdxcffx64.dll").unlink(missing_ok=True)
        (self.src / "x").mkdir()
        (self.src / "x/amdxcffx64.dll").write_bytes(fake_dll("AMD new", (2, 5, 0, 0)))
        self.c.store(self.kept, "amdxcffx64", str(self.src / "x/amdxcffx64.dll"), swap_dir)
        s = self.c.status(self.kept)
        self.assertEqual((s["amdxcffx64"]["product"], s["nvngx_dlss"]["product"]), ("AMD new", "NVIDIA DLSS"))

    def test_refuses(self):
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        with self.assertRaises(self.c.Error):
            self.c.store(self.kept, "nvngx_dlss", str(self.src / "amdxcffx64.dll"), swap_dir)  # another slot's name
        (self.src / "nvngx_dlss.dll").write_bytes(b"MZ not really")
        with self.assertRaises(self.c.Error):
            self.c.store(self.kept, "nvngx_dlss", str(self.src / "nvngx_dlss.dll"), swap_dir)
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "amdxcffx64", "relative.dll", swap_dir)
        with self.assertRaises(ValueError):
            self.c.store(self.kept, "nope", str(self.src / "amdxcffx64.dll"), swap_dir)
        self.assertEqual(set(self.c.status(self.kept).values()), {None})

    def test_in_a_game(self):
        (self.src / "nvngx_dlss.dll").write_bytes(fake_dll("NVIDIA DLSS", (3, 10, 7, 0)))
        self.c.store(self.kept, "nvngx_dlss", str(self.src / "nvngx_dlss.dll"), swap_dir)
        game = self.umu / "OptiScaler"
        game.joinpath("libxess.dll").write_text("other")
        self.c.enable(self.kept, self.umu, "nvngx_dlss")
        self.assertEqual([s for s, on in self.c.game_state(self.umu).items() if on], ["nvngx_dlss"])
        self.c.disable(self.umu, "nvngx_dlss")
        self.assertEqual([p.name for p in game.iterdir()], ["libxess.dll"])

    def test_enable_needs_both_ends(self):
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "amdxcffx64")  # nothing kept
        (self.src / "amdxcffx64.dll").write_bytes(fake_dll())
        self.c.store(self.kept, "amdxcffx64", str(self.src / "amdxcffx64.dll"), swap_dir)
        (self.umu / "OptiScaler.dll").unlink()
        with self.assertRaises(self.c.Error):
            self.c.enable(self.kept, self.umu, "amdxcffx64")  # OptiScaler not installed

    def test_remove(self):
        for name in ("amdxcffx64.dll", "amdvkffx64.dll"):
            (self.src / name).write_bytes(fake_dll())
            self.c.store(self.kept, name[:-4], str(self.src / name), swap_dir)
        self.c.remove(self.kept, "amdxcffx64")
        s = self.c.status(self.kept)
        self.assertEqual((s["amdxcffx64"], s["amdvkffx64"]["name"]), (None, "amdvkffx64.dll"))
        self.assertEqual(sorted(p.name for p in self.kept.iterdir()), ["amdvkffx64"])  # no temp left

    def test_migrate_the_old_fsr4_slot(self):
        """0.3.0 kept both AMD files in custom/fsr4: each moves to its slot, with its info; then fsr4 goes."""
        old = self.kept / "fsr4"
        old.mkdir(parents=True)
        (old / "amdxcffx64.dll").write_bytes(fake_dll("AMD DX", (2, 4, 0, 4370)))
        (old / "amdvkffx64.dll").write_bytes(fake_dll("AMD VK", (2, 4, 0, 4370)))
        (old / "info.json").write_text(json.dumps({"amdxcffx64.dll": {"product": "from info", "version": "2.4.0.4370"}}))
        self.c.migrate(self.kept, swap_dir)
        s = self.c.status(self.kept)
        self.assertEqual((s["amdxcffx64"]["product"], s["amdvkffx64"]["product"]), ("from info", "AMD VK"))
        self.assertFalse(old.exists())
        self.c.migrate(self.kept, swap_dir)  # nothing left to do
        self.assertEqual(self.c.status(self.kept), s)


class GameExtraTest(unittest.TestCase):
    """The backend: a DLL turned on and off in a game, and 0.3.0's slot made into two."""

    def setUp(self):
        self.b, tmp, self.umu = fake_game(self)
        build = self.b.GLOBAL_DIR / "optiscaler"
        (build / "OptiScaler").mkdir(parents=True)
        (build / "OptiScaler.dll").write_bytes(b"opti")
        (build / ".invasor-opti.version").write_text(json.dumps({"tag": "t"}))
        self.b.game_install("42", "dxgi")
        self.src = tmp / "nvngx_dlss.dll"
        self.src.write_bytes(fake_dll("NVIDIA DLSS", (3, 10, 7, 0)))

    def test_on_and_off(self):
        r = self.b.load("42")
        self.assertEqual(set(r["dlls"].values()) | set(r["install"]["extras"].values()), {False})
        with self.assertRaises(Unavailable):
            self.b.game_extra("42", "nvngx_dlss", True)  # not kept in Global
        self.b.dlls_set("nvngx_dlss", str(self.src))
        self.assertTrue(self.b.game_extra("42", "nvngx_dlss", True)["install"]["extras"]["nvngx_dlss"])
        self.assertTrue((self.umu / "OptiScaler/nvngx_dlss.dll").is_file())
        self.assertFalse(self.b.game_extra("42", "nvngx_dlss", False)["install"]["extras"]["nvngx_dlss"])
        self.assertFalse((self.umu / "OptiScaler/nvngx_dlss.dll").exists())

    def test_0_3_0_folder_migrated(self):
        """0.3.0's custom/fsr4 becomes dlls/amdxcffx64 (and amdvkffx64)."""
        old = self.b.GLOBAL_DIR / "custom/fsr4"
        old.mkdir(parents=True)
        (old / "amdxcffx64.dll").write_bytes(fake_dll())
        s = self.b.dlls_status()
        self.assertEqual((s["amdxcffx64"]["name"], s["amdvkffx64"]), ("amdxcffx64.dll", None))
        self.assertFalse((self.b.GLOBAL_DIR / "custom").exists())
        self.assertTrue((self.b.GLOBAL_DIR / "dlls/amdxcffx64/amdxcffx64.dll").is_file())

    def test_nvidia_download(self):
        import hashlib
        http = self.b.ctx.http
        files = {}
        saved = dict(self.b.dlls.NVIDIA)
        for slot, (url, _) in self.b.dlls.NVIDIA.items():
            data = fake_dll(slot, (310, 7, 0, 0))
            files[url] = data
            self.b.dlls.NVIDIA[slot] = (url, hashlib.sha256(data).hexdigest())  # this test's fakes
        self.addCleanup(lambda: [self.b.dlls.NVIDIA.__setitem__(k, v) for k, v in saved.items()])
        http.files.update(files)
        self.assertEqual(self.b.dlls_nvidia_download(), {"started": True, "busy": False})
        self.b._nvidia_thread.join(10)
        st = self.b.dlls_nvidia_status()
        self.assertEqual((st["busy"], st["error"]), (False, None))
        s = self.b.dlls_status()
        self.assertEqual([s[k]["version"] for k in ("nvngx_dlss", "nvngx_dlssd", "nvngx_dlssg")], ["310.7.0.0"] * 3)
        self.assertEqual(sorted(p.name for p in self.b.GLOBAL_DIR.iterdir() if p.name.startswith(".")), [])

    def test_nvidia_download_bad_checksum_changes_nothing(self):
        http = self.b.ctx.http
        for url, _ in self.b.dlls.NVIDIA.values():
            http.files[url] = b"not NVIDIA's"
        self.b.dlls_nvidia_download()
        self.b._nvidia_thread.join(10)
        self.assertIn("checksum", self.b.dlls_nvidia_status()["error"])
        self.assertIsNone(self.b.dlls_status()["nvngx_dlss"])

    def test_refuses(self):
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "fsr4", True)  # 0.3.0's slot
        with self.assertRaises(InvalidArgument):
            self.b.game_extra("42", "nvngx_dlss", "yes")


if __name__ == "__main__":
    unittest.main()
