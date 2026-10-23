import unittest

from tests.loader import HERE, load_backend, submodule

FIXTURE = HERE / "assets" / "OptiScaler.optiscaler.ini"  # OptiScaler v10.0.0-pre1, nightly 2026-10-23


class IniTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ini = submodule(load_backend(), "ini")
        cls.text, _ = cls.ini.decode(FIXTURE.read_bytes())
        cls.sections = cls.ini.describe(cls.ini.parse(cls.text))
        cls.keys = {(s["name"], k["key"]): k for s in cls.sections for k in s["keys"]}

    def values(self, section, key):
        return [o["value"] for o in self.keys[(section, key)]["options"]]

    def test_every_section_and_key(self):
        self.assertEqual(len(self.sections), 37)
        self.assertEqual(sum(len(s["keys"]) for s in self.sections), 327)
        self.assertEqual(self.sections[0]["name"], "Upscalers")

    def test_bool(self):
        self.assertEqual(self.values("FrameGen", "Enabled"), ["auto", "true", "false"])

    def test_numbered(self):
        self.assertEqual(self.values("FSR", "UpscalerIndex"), ["auto", "0", "1", "2"])
        self.assertEqual(self.keys[("FSR", "UpscalerIndex")]["options"][1]["label"], "0 - FSR 4.0.2")
        self.assertEqual(self.values("FSR", "Fsr4GpuSupport"), ["auto", "0", "1", "2", "3", "4"])
        # Numbered values win over a stray "true or false" line.
        self.assertEqual(self.values("XeFG", "InterpolationCount"), ["auto", "1", "2", "3"])
        self.assertEqual(self.values("Log", "LogLevel"), ["auto", "0", "1", "2", "3", "4"])

    def test_listed(self):
        self.assertEqual(self.values("Upscalers", "Dx12Upscaler"), ["auto", "xess", "fsr21", "fsr22", "ffx", "dlss"])
        self.assertIn("xess (native DX11, Arc only)", [o["label"] for o in self.keys[("Upscalers", "Dx11Upscaler")]["options"]])
        self.assertEqual(self.values("Anisotropy", "AnisotropyOverride"), ["auto", "2", "4", "8", "16"])
        self.assertEqual(self.values("Sharpness", "Shader"), ["auto", "rcas", "da", "lcda"])

    def test_range(self):
        values = self.values("FrameGen", "HudCutoff")
        self.assertEqual(values[:3], ["auto", "0.0", "0.1"])
        self.assertEqual(values[-1], "1.0")

    def test_text(self):
        k = self.keys[("Libraries", "OptiDllPath")]
        self.assertEqual(k["kind"], "text")
        self.assertIn("Main folder", k["description"])

    def test_current_value_is_kept(self):
        control = self.ini.infer(["0 = A | 1 = B"], "7")
        self.assertEqual([o["value"] for o in control["options"]], ["auto", "0", "1", "7"])
        self.assertIsNone(self.ini.match_option(control["options"], "9"))
        self.assertEqual(self.ini.match_option(self.ini.infer(["true or false"])["options"], "True"), "true")

    def test_set_value_changes_one_line(self):
        new = self.ini.set_value(self.text, "FSR", "UpscalerIndex", "1")
        old_lines, new_lines = self.text.splitlines(keepends=True), new.splitlines(keepends=True)
        self.assertEqual(len(old_lines), len(new_lines))
        diff = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        self.assertEqual(diff, [("UpscalerIndex=auto\r\n", "UpscalerIndex=1\r\n")])

    def test_set_value_right_section(self):
        # "Enabled" is in several sections: only [CAS] changes.
        new = self.ini.set_value(self.text, "CAS", "Enabled", "true")
        parsed = {(s["name"], k["key"]): k["value"] for s in self.ini.parse(new) for k in s["keys"]}
        self.assertEqual(parsed[("CAS", "Enabled")], "true")
        self.assertEqual(parsed[("FrameGen", "Enabled")], "auto")

    def test_set_value_refuses(self):
        with self.assertRaises(KeyError):
            self.ini.set_value(self.text, "FSR", "NoSuchKey", "1")
        with self.assertRaises(ValueError):
            self.ini.set_value(self.text, "FSR", "UpscalerIndex", "1\nEvil=1")
        self.assertIn("UpscalerIndex=auto", self.ini.set_value(self.text, "FSR", "UpscalerIndex", "  "))


if __name__ == "__main__":
    unittest.main()
