"""DLLs the user brings, which the module can't ship: AMD's FSR 4 driver files (DirectX 12 and Vulkan) and NVIDIA's
DLSS DLLs. Compiling HelixSR also keeps amdxcffx64.dll and nvngx_dlss.dll, the two its setup downloads, when their
slots are empty.

The module keeps a copy of each in its own folder (one sub-folder per slot, with INFO saying what the file is), so a
game doesn't depend on where the original was. Turned on for a game, it's copied into its OptiScaler folder
(umu/OptiScaler), the folder OptiScaler loads them from.
"""
import json
import shutil
import tempfile
from pathlib import Path

from . import pe
from .files import Error, copy_file
from .install import FOLDER
from .nightly import MAIN_DLL

INFO = "info.json"
MAX_SIZE = 300 * 1024 * 1024  # amdxcffx64.dll is ~85 MB, nvngx_dlssd.dll ~80 MB

# One slot per DLL, named as the file is in the game's OptiScaler folder.
SLOTS = {
    "amdxcffx64": {"file": "amdxcffx64.dll", "vendor": "amd"},
    "amdvkffx64": {"file": "amdvkffx64.dll", "vendor": "amd"},
    "nvngx_dlss": {"file": "nvngx_dlss.dll", "vendor": "nvidia"},
    "nvngx_dlssd": {"file": "nvngx_dlssd.dll", "vendor": "nvidia"},
    "nvngx_dlssg": {"file": "nvngx_dlssg.dll", "vendor": "nvidia"},
}
OLD_SLOT = "fsr4"  # 0.3.0's one slot for both AMD files, made into theirs by migrate()

# NVIDIA's DLLs, from NVIDIA's own GitHub (github.com/NVIDIA/DLSS, under NVIDIA's DLSS SDK license): the release
# HelixSR's setup pins, each checked by its SHA-256. {slot: (url, sha256)}
NVIDIA_RELEASE = "v310.7.0"
_NVIDIA = f"https://raw.githubusercontent.com/NVIDIA/DLSS/{NVIDIA_RELEASE}/lib/Windows_x86_64/rel/"
NVIDIA = {
    "nvngx_dlss": (_NVIDIA + "nvngx_dlss.dll", "be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e"),
    "nvngx_dlssd": (_NVIDIA + "nvngx_dlssd.dll", "f4e97624f70fbb769acb11ebd751b512ecc9463d4bd6aef04896d3956e6084a0"),
    "nvngx_dlssg": (_NVIDIA + "nvngx_dlssg.dll", "135eaf0733c1e37381a8c28abcf7a862404a54132b81787c04e35d09efc5e36f"),
}


def _slot(slot):
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {', '.join(SLOTS)}")
    return SLOTS[slot]


def _dll_info(path):
    try:
        if path.stat().st_size > MAX_SIZE:
            raise Error(f"{path.name} is too big for a DLL")
        return pe.info(path.read_bytes())
    except OSError as e:
        raise Error(f"can't read {path}: {e.strerror or e}") from None
    except ValueError as e:
        raise Error(f"{path.name}: {e}") from None


def _info(d):
    """INFO of a kept slot, {} when there's none (or it's unreadable)."""
    try:
        info = json.loads((d / INFO).read_text())
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _put(folder, slot, src, info, swap):
    """Make the slot hold a copy of src with its INFO, swapped in whole (swap: ctx.archive.swap_dir)."""
    name = SLOTS[slot]["file"]
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f".{slot}.*"):  # left by a crash
        shutil.rmtree(old, ignore_errors=True)
    work = Path(tempfile.mkdtemp(dir=folder, prefix=f".{slot}."))
    try:
        new = work / "new"
        new.mkdir()
        shutil.copyfile(src, new / name)
        (new / INFO).write_text(json.dumps({name: info}, indent=1) + "\n")
        swap(new, folder / slot)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def store(folder, slot, path, swap):
    """Keep a copy of the file the user picked (path), which must be the slot's DLL; the slot is replaced whole,
    with swap(new, target) (ctx.archive.swap_dir)."""
    name = _slot(slot)["file"]
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("path must be an existing file")
    if path.name.lower() != name:
        raise Error(f"choose {name}")
    _put(folder, slot, path, _dll_info(path), swap)


def fetch_nvidia(work, http, task=None, stop=None):
    """Download NVIDIA's three DLLs (NVIDIA) into the folder work, each checked by its SHA-256 (http: ctx.http, task:
    the ctx.task that shows it); {slot: path}, for store()."""
    out = {}
    for slot, (url, sha256) in NVIDIA.items():
        if stop is not None and stop.is_set():
            raise Error("Download stopped")
        name = SLOTS[slot]["file"]
        if task:
            task.update(None, f"Downloading {name}")
        out[slot] = Path(work) / name
        http.download(url, out[slot], checksum=("sha256", sha256), max_bytes=MAX_SIZE, task=task)
    return out


def has(folder, slot):
    """True if the slot keeps its DLL."""
    return (Path(folder) / slot / _slot(slot)["file"]).is_file()


def migrate(folder, swap):
    """0.3.0 kept both AMD files in OLD_SLOT: move each into its own slot (one already there stays), then drop
    OLD_SLOT. Nothing to do once it's gone."""
    old = Path(folder) / OLD_SLOT
    if not old.is_dir():
        return
    info = _info(old)
    for slot in ("amdxcffx64", "amdvkffx64"):
        name = SLOTS[slot]["file"]
        if (old / name).is_file() and not has(folder, slot):
            i = info.get(name) if isinstance(info.get(name), dict) else None
            _put(folder, slot, old / name, i or _dll_info(old / name), swap)
    shutil.rmtree(old, ignore_errors=True)


def remove(folder, slot):
    _slot(slot)
    shutil.rmtree(Path(folder) / slot, ignore_errors=True)


def status(folder):
    """{slot: {"name", "product", "version"} or None (not kept)}."""
    out = {}
    for slot, s in SLOTS.items():
        d = Path(folder) / slot
        name = s["file"]
        if not (d / name).is_file():
            out[slot] = None
            continue
        i = _info(d).get(name)
        i = i if isinstance(i, dict) else {}
        out[slot] = {"name": name, "product": i.get("product"), "version": i.get("version")}
    return out


def game_state(umu):
    """{slot: on} for a game: a slot is on when its DLL is in the game's OptiScaler folder."""
    game = Path(umu) / FOLDER
    return {slot: (game / s["file"]).is_file() for slot, s in SLOTS.items()}


def enable(folder, umu, slot):
    """Copy the slot's DLL into the game."""
    name = _slot(slot)["file"]
    src = Path(folder) / slot / name
    if not src.is_file():
        raise Error("Choose the DLL in Global first")
    if not (Path(umu) / MAIN_DLL).is_file():
        raise Error("OptiScaler isn't installed in this game")
    game = Path(umu) / FOLDER
    game.mkdir(exist_ok=True)
    copy_file(src, game / name)


def disable(umu, slot):
    """Remove the slot's DLL from the game."""
    p = Path(umu) / FOLDER / _slot(slot)["file"]
    if p.is_file() or p.is_symlink():
        p.unlink()
