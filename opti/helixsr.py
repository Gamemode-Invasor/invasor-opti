"""HelixSR Scaler built on this device by bin/helixsr-fetch.sh: OptiScaler with HelixSR's upscalers (HLX DLSS and
HLX FSR 4), kept as the nightly is (see nightly.py), as a game's umu folder lays it out:

    OptiScaler.dll, OptiScaler.ini, OptiScaler/ (HLX FSR 4 with AMD's FSR next to it), Licenses/
    HelixSR/helixsr.dll, HelixSR/helixsr.ini (HLX DLSS)
    MARKER  which build it is, with "flavor": SCALER

The script leaves that in a folder, plus VERSION (its tag) and AMD's amdxcffx64.dll and NVIDIA's nvngx_dlss.dll
when the setup got them (the backend takes them out for the DLLs first); store() checks the rest and swaps it in.
"""
import json
from pathlib import Path

from .files import Error
from .nightly import MAIN_DLL, MARKER, MARKER_KEYS, SCALER

DLSS = "HelixSR/helixsr.dll"
FSR4 = "OptiScaler/amd_fidelityfx_upscaler_dx12.dll"
VERSION = "VERSION"
MIN_SIZE = 1 << 20  # a DLL with its network or shaders is tens of MB


def _ends_with(path, magic, at):
    """True if the file is bigger than MIN_SIZE and has `magic` `at` bytes from its end."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            if size <= MIN_SIZE:
                return False
            f.seek(size - at)
            return f.read(len(magic)) == magic
    except OSError:
        return False


def check(out):
    """The tag of the build the script left in out; a files.Error says what's missing."""
    out = Path(out)
    if any(p.is_symlink() for p in out.rglob("*")):  # what's copied into games must be the build's own files
        raise Error("The build has a symbolic link in it")
    if not (out / MAIN_DLL).is_file():
        raise Error("The build has no HelixSR Scaler")
    if not _ends_with(out / DLSS, b"HXSRNET1", 32):
        raise Error("The build has no HLX DLSS with its network")
    if not _ends_with(out / FSR4, b"HLXFSR4D", 24):
        raise Error("The build has no HLX FSR 4 with its shaders")
    try:
        tag = (out / VERSION).read_text().strip()
    except OSError:
        tag = ""
    if not tag or "\n" in tag or len(tag) > 64:
        raise Error("The build doesn't say its version")
    return tag


def store(out, folder, swap):
    """Make the script's out folder the kept build (folder), with its MARKER; swap(new, target) puts it in place
    at once (ctx.archive.swap_dir). Returns the marker."""
    out = Path(out)
    tag = check(out)
    (out / VERSION).unlink()
    marker = {k: None for k in MARKER_KEYS}
    marker.update(tag=tag, version=tag.lstrip("vV"))
    (out / MARKER).write_text(json.dumps({**marker, "flavor": SCALER}, indent=1) + "\n")
    Path(folder).parent.mkdir(parents=True, exist_ok=True)
    swap(out, folder)
    return marker
