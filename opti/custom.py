"""DLLs the user brings, which the module can't ship: AMD's driver FidelityFX (FSR 4 from the driver,
INT8 and INT8 Lite) and HelixSR.

The module keeps a copy of each in its own folder (one sub-folder per slot, with INFO saying what each
file is), so a game doesn't depend on where the original was. Turned on for a game, they're copied into
its OptiScaler folder (umu/OptiScaler), the folder OptiScaler loads them from.
"""
import json
import shutil
import tempfile
from pathlib import Path

from . import pe
from .files import Error, copy_file, replace_dir
from .install import FOLDER
from .nightly import MAIN_DLL

INFO = "info.json"
MAX_SIZE = 300 * 1024 * 1024  # amdxcffx64.dll is ~85 MB

# A slot, as its files are named in the game's OptiScaler folder (the first is the DLL the user picks; the
# others are taken from the same folder when they're there):
# - any_name: the picked DLL may have any name (else it must be one of files);
# - keep: files the game keeps when the slot is turned on again (its own settings);
# - extra: files the slot leaves in the game, removed with it;
# - ini: (section, key, value) the game's OptiScaler.ini needs while the slot is on.
SLOTS = {
    "fsr4": {"files": ("amdxcffx64.dll", "amdvkffx64.dll"), "any_name": False},
    "helixsr": {
        "files": ("helixsr.dll", "helixsr.ini"),
        "any_name": True,
        "keep": ("helixsr.ini",),
        "extra": ("helixsr.log",),
        # HelixSR as OptiScaler's FidelityFX upscaler: FSR 4 and the older ones stay, HelixSR is one more.
        "ini": ("Libraries", "FfxDx12SRPath", r"C:\windows\system32\umu\OptiScaler\helixsr.dll"),
    },
}


def _slot(slot):
    if slot not in SLOTS:
        raise ValueError(f"slot must be one of {', '.join(SLOTS)}")
    return SLOTS[slot]


def _sibling(folder, name):
    """folder/name, whatever its case (Windows names), or None."""
    try:
        return next((p for p in folder.iterdir() if p.name.lower() == name and p.is_file()), None)
    except OSError:
        return None


def _dll_info(path):
    try:
        if path.stat().st_size > MAX_SIZE:
            raise Error(f"{path.name} is too big for a DLL")
        return pe.info(path.read_bytes())
    except OSError as e:
        raise Error(f"can't read {path}: {e.strerror or e}") from None
    except ValueError as e:
        raise Error(f"{path.name}: {e}") from None


def _sources(slot, path):
    """{name in the slot: source file} for the file the user picked."""
    s = _slot(slot)
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("path must be an existing file")
    picked = path.name.lower()
    if s["any_name"]:
        if not picked.endswith(".dll"):
            raise Error("choose a .dll file")
        picked = s["files"][0]
    elif picked not in s["files"]:
        raise Error(f"choose {' or '.join(s['files'])}")
    found = {name: path if name == picked else _sibling(path.parent, name) for name in s["files"]}
    return {name: src for name, src in found.items() if src}


def store(folder, slot, path):
    """Keep a copy of the slot's files, from the one the user picked (path). Replaces the slot whole."""
    files = _sources(slot, path)
    info = {}
    for name, src in files.items():
        info[name] = _dll_info(src) if name.endswith(".dll") else {"product": None, "version": None}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f".{slot}.*"):  # left by a crash
        shutil.rmtree(old, ignore_errors=True)
    work = Path(tempfile.mkdtemp(dir=folder, prefix=f".{slot}."))
    try:
        new = work / "new"
        new.mkdir()
        for name, src in files.items():
            shutil.copyfile(src, new / name)
        (new / INFO).write_text(json.dumps(info, indent=1) + "\n")
        replace_dir(new, folder / slot, work / "old")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove(folder, slot):
    _slot(slot)
    shutil.rmtree(Path(folder) / slot, ignore_errors=True)


def status(folder):
    """{slot: [{"name", "product", "version"}] or None (not set)}."""
    out = {}
    for slot, s in SLOTS.items():
        d = Path(folder) / slot
        try:
            info = json.loads((d / INFO).read_text())
        except (OSError, ValueError):
            info = {}
        if not isinstance(info, dict):
            info = {}
        files = []
        for n in s["files"]:
            if (d / n).is_file():
                i = info.get(n) if isinstance(info.get(n), dict) else {}
                files.append({"name": n, "product": i.get("product"), "version": i.get("version")})
        out[slot] = files if any(f["name"].endswith(".dll") for f in files) else None
    return out


def game_state(umu):
    """{slot: on} for a game: a slot is on when any of its DLLs is in the game's OptiScaler folder."""
    game = Path(umu) / FOLDER
    return {slot: any((game / n).is_file() for n in s["files"] if n.endswith(".dll")) for slot, s in SLOTS.items()}


def enable(folder, umu, slot):
    """Copy the slot's files into the game (the game keeps its own "keep" files)."""
    s = _slot(slot)
    src = Path(folder) / slot
    names = [n for n in s["files"] if (src / n).is_file()]
    if not names:
        raise Error("Choose the DLL in Global first")
    if not (Path(umu) / MAIN_DLL).is_file():
        raise Error("OptiScaler isn't installed in this game")
    game = Path(umu) / FOLDER
    game.mkdir(exist_ok=True)
    for name in names:
        if name not in s.get("keep", ()) or not (game / name).exists():
            copy_file(src / name, game / name)


def disable(umu, slot):
    """Remove the slot's files (and its "extra" ones) from the game."""
    s = _slot(slot)
    game = Path(umu) / FOLDER
    for name in (*s["files"], *s.get("extra", ())):
        p = game / name
        if p.is_file() or p.is_symlink():
            p.unlink()
