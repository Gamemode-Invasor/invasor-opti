"""bin/helixsr-fetch.sh itself, run for real: GitHub, the zip and its sums come from a `curl` that serves local
files, and HelixSR's setup is a stand-in in the zip that records its arguments and makes what the script checks."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "helixsr-fetch.sh"

CURL = r"""#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in -o) out=$2; shift ;; -H|--retry) shift ;; -*) ;; *) url=$1 ;; esac
    shift
done
echo "$url" >> "$FAKE_DIR/asked"
f="$FAKE_DIR/files/$(printf '%s' "$url" | sed 's#[^A-Za-z0-9._-]#_#g')"
[ -f "$f" ] || exit 22
if [ -n "$out" ]; then cp "$f" "$out"; else cat "$f"; fi
"""

# What HelixSR's setup leaves, as far as the script checks it (see helixsr.check): HLX DLSS with its network, HLX FSR 4
# with its shaders, HelixSR Scaler; Preset K when asked.
SETUP = r"""#!/usr/bin/env bash
echo "$@" > "$FAKE_DIR/setup-args"
python3 - "$@" <<'PY'
import sys
from pathlib import Path
def big(path, magic, at):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(1100000) + magic + bytes(at - len(magic)))
big("HelixSR/helixsr.dll", b"HXSRNET1", 32)
big("helixsr-scaler/OptiScaler/amd_fidelityfx_upscaler_dx12.dll", b"HLXFSR4D", 24)
Path("helixsr-scaler/OptiScaler.dll").write_bytes(b"scaler")
if "--preset-k" in sys.argv:
    Path("HelixSR/k").mkdir(); Path("HelixSR/k/helixsr.dll").write_bytes(b"k")
PY
"""

tag_of = lambda t: {"tag_name": t, "assets": []}


class FetchScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if platform.machine() != "x86_64" or not all(shutil.which(t) for t in ("bash", "unzip", "awk", "sha256sum", "python3")):
            raise unittest.SkipTest("the script needs x86-64 Linux with bash, unzip, awk and sha256sum")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fake = self.tmp / "fake"
        (self.fake / "files").mkdir(parents=True)
        (self.tmp / "bin").mkdir()
        curl = self.tmp / "bin/curl"
        curl.write_text(CURL)
        curl.chmod(0o755)
        self.sums_line = None  # the SHA256SUMS line of the zip ({hash} is its own): None = the right one

    def serve(self, url, data):
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in url)
        (self.fake / "files" / name).write_bytes(data if isinstance(data, bytes) else data.encode())

    def release(self, tag, sums=True):
        """A release of `tag` with its zip (stand-in setup inside) and, when sums, its SHA256SUMS.txt."""
        base = f"https://github.com/lonewolf0622/HelixSR/releases/download/{tag}/"
        zip_path = self.tmp / f"HelixSR-{tag[1:]}.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            top = f"HelixSR-{tag[1:]}/"
            z.writestr(zipfile.ZipInfo(top + "helixsr-setup.sh"), SETUP)
            z.writestr(top + "setup/helixsr_install.py", "# the games step\n")
            z.writestr(top + "setup/preset-k/helixsr.dll", "k")
            z.writestr(top + "helixsr-scaler/OptiScaler.ini", "[HelixSR]\n")
        data = zip_path.read_bytes()
        self.serve(base + zip_path.name, data)
        line = (self.sums_line or "{hash}  " + zip_path.name).format(hash=hashlib.sha256(data).hexdigest())
        assets = [{"name": zip_path.name, "browser_download_url": base + zip_path.name}]
        if sums:
            assets.append({"name": "SHA256SUMS.txt", "browser_download_url": base + "SHA256SUMS.txt"})
            self.serve(base + "SHA256SUMS.txt", line + "\n")
        return {"tag_name": tag, "assets": assets}

    def api(self, stable=None, newest=()):
        """GitHub's answers: /releases/latest (a release) and /releases (a list, as GitHub pretty-prints it)."""
        repo = "https://api.github.com/repos/lonewolf0622/HelixSR/releases"
        if stable is not None:
            self.serve(repo + "/latest", json.dumps(stable, indent=2))
        if newest:
            self.serve(repo + "?per_page=10", json.dumps(list(newest), indent=2))

    def run_script(self, *args):
        env = dict(os.environ, PATH=f"{self.tmp / 'bin'}:{os.environ['PATH']}", FAKE_DIR=str(self.fake), TMPDIR=str(self.tmp))
        return subprocess.run(["bash", str(SCRIPT), "--yes", "--out", str(self.tmp / "out"), *args],
                              env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def setup_args(self):
        return (self.fake / "setup-args").read_text().split()

    def test_stable_is_the_default_and_builds_without_preset_k(self):
        self.api(stable=self.release("v3.1.0"), newest=[self.release("v3.2.1")])
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "out/VERSION").read_text().strip(), "v3.1.0")
        self.assertNotIn("--preset-k", self.setup_args())
        self.assertFalse((self.tmp / "out/HelixSR/k").exists())

    def test_prerelease_takes_the_first_of_the_list_with_preset_k(self):
        self.api(stable=self.release("v3.1.0"), newest=[self.release("v3.2.1"), self.release("v3.1.0")])
        r = self.run_script("--channel", "prerelease", "--preset-k")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.tmp / "out/VERSION").read_text().strip(), "v3.2.1")
        self.assertIn("--preset-k", self.setup_args())
        self.assertTrue((self.tmp / "out/HelixSR/k/helixsr.dll").is_file())

    def test_channel_arguments(self):
        for args in (["--channel", "nightly"], ["--channel=nightly"], ["--channel"]):
            r = self.run_script(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("channel", r.stderr, args)
        self.assertFalse((self.fake / "asked").exists())  # it never asked GitHub

    def test_no_release_says_why(self):
        """A channel with no tag in the answer dies with its own message, not silently."""
        self.api(stable={"message": "Not Found"}, newest=[{"message": "odd"}])
        for args in ([], ["--channel", "prerelease"]):
            r = self.run_script(*args)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no release with a zip", r.stderr)

    def test_the_zip_must_match_its_sums(self):
        self.sums_line = "0" * 64 + "  HelixSR-3.1.0.zip"
        self.api(stable=self.release("v3.1.0"))
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("doesn't match", r.stderr)
        self.assertFalse((self.tmp / "out").exists())

    def test_sums_missing_or_odd(self):
        self.api(stable=self.release("v3.1.0", sums=False))
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA256SUMS", r.stderr)
        for form in ("{hash} *HelixSR-3.1.0.zip", "{hash}  ./HelixSR-3.1.0.zip"):  # sha256sum's binary mark, a ./ path
            self.sums_line = form
            self.api(stable=self.release("v3.1.0"))
            shutil.rmtree(self.tmp / "out", ignore_errors=True)
            r = self.run_script()
            self.assertEqual(r.returncode, 0, form + r.stdout + r.stderr)

    def test_preset_k_of_a_release_without_it(self):
        """--preset-k on a release that doesn't have it: a warning, the rest is built."""
        self.api(stable=self.release("v3.1.0"))
        zip_path = self.tmp / "HelixSR-3.1.0.zip"
        src = zipfile.ZipFile(zip_path)
        old = self.tmp / "old.zip"
        with zipfile.ZipFile(old, "w") as z:
            for item in src.infolist():
                if "preset-k" not in item.filename:
                    z.writestr(item, src.read(item))
        src.close()
        data = old.read_bytes()
        base = "https://github.com/lonewolf0622/HelixSR/releases/download/v3.1.0/"
        self.serve(base + "HelixSR-3.1.0.zip", data)
        self.serve(base + "SHA256SUMS.txt", f"{hashlib.sha256(data).hexdigest()}  HelixSR-3.1.0.zip\n")
        r = self.run_script("--preset-k")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no Preset K", r.stdout)
        self.assertNotIn("--preset-k", self.setup_args())


if __name__ == "__main__":
    unittest.main()
