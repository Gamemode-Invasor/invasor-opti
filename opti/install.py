"""OptiScaler in a game's Proton prefix: the umu folder (UMU) Wine's OptiScaler hack loads it
from, as WINE_OPTISCALER_NAME=<dll>.dll says. There, <dll>.dll is a link to OptiScaler.dll.

What is OptiScaler's in umu, and nothing else is ever touched: OptiScaler.dll, OptiScaler.ini, the
OptiScaler folder, MARKER, and each <name>.dll of DLL_NAMES that is a link to OptiScaler.dll (or a
copy of it). OptiScaler is installed when OptiScaler.dll is there, also when someone put it by hand.

Two flavors, as the build's MARKER says (nightly.flavor): OptiScaler's nightly, and HelixSR Scaler (see
helixsr.py), which also brings the HelixSR and Licenses folders: those are its own too, only where the game's
MARKER says it's HelixSR Scaler.
"""
import filecmp
import os
import re
import shutil
from pathlib import Path

from .files import Error, copy_file
from .nightly import MAIN_DLL, MARKER, SCALER, flavor, read_marker

DLL_NAMES = ("dxgi", "d3d12", "dbghelp", "winmm")
INI = "OptiScaler.ini"
FOLDER = "OptiScaler"  # OptiScaler's own folder, next to OptiScaler.dll
SCALER_FOLDERS = ("HelixSR", "Licenses")  # HelixSR Scaler's folders besides FOLDER
SCALER_KEEP = "HelixSR/helixsr.ini"  # HLX DLSS's settings: a game keeps its own on update
# Where Wine's OptiScaler hack (GE-Proton, proton-cachyos) loads WINE_OPTISCALER_NAME from, in compatdata/<appid>.
UMU = Path("pfx/drive_c/windows/system32/umu")


def in_launch_options(options, dll):
    """True if launch options (as Steam keeps them) load OptiScaler as <dll>.dll: WINE_OPTISCALER_NAME=<dll>.dll
    as a word of its own (quoted or not) before %command%, whatever else is around."""
    m = re.search(r"(?:^|\s)WINE_OPTISCALER_NAME=([\"']?)(?i:" + re.escape(dll) + r"\.dll)\1(?=\s|$)", options or "")
    command = (options or "").find("%command%")
    return m is not None and command > m.start()


def with_dll(options, dll):
    """The launch options with OptiScaler loaded as <dll>.dll, the rest of the user's line kept: a
    WINE_OPTISCALER_NAME=… word before %command% is replaced, else the word goes first. A line without
    %command% is the game's arguments (Steam puts them after it), so they go after %command%."""
    _check_dll(dll)
    word = f"WINE_OPTISCALER_NAME={dll}.dll"
    options = (options or "").strip()
    command = options.find("%command%")
    if command < 0:
        return f"{word} %command% {options}".strip()
    m = re.search(r"(?:^|(?<=\s))WINE_OPTISCALER_NAME=(?:\"[^\"]*\"|'[^']*'|\S*)(?=\s|$)", options[:command])
    if m:
        return options[:m.start()] + word + options[m.end():]
    return f"{word} {options}"


def _copy_name(name):
    """A build's folder, copied next to the game's before the swap."""
    return f".{name}.copy"


def _folders(kind):
    """The folders a flavor puts in umu."""
    return (FOLDER, *SCALER_FOLDERS) if kind == SCALER else (FOLDER,)


def _leftovers(umu, kind):
    """What a crash left of an install: the copies, and swap_dir's own .<folder>.old-*/.new-* (nothing else)."""
    out = []
    for name in _folders(kind):
        out += [umu / _copy_name(name), *umu.glob(f".{name}.old-*"), *umu.glob(f".{name}.new-*")]
    return out


def _check_dll(dll):
    if dll not in DLL_NAMES:
        raise ValueError(f"the DLL must be one of {', '.join(DLL_NAMES)}")


def _loads_optiscaler(path, main):
    """True if path is OptiScaler under another name: a link to main, or a copy of it."""
    if path.is_symlink():
        return os.path.realpath(path) == os.path.realpath(main)
    try:
        return path.is_file() and main.is_file() and filecmp.cmp(path, main, shallow=False)
    except OSError:
        return False


def _loaders(umu):
    """The <name>.dll of DLL_NAMES that load OptiScaler, in DLL_NAMES' order."""
    main = umu / MAIN_DLL
    return [n for n in DLL_NAMES if _loads_optiscaler(umu / f"{n}.dll", main)]


def state(umu):
    """{"installed", "version": the marker (see nightly.read_marker) or None, "dll": the first name
    that loads OptiScaler or None, "flavor": nightly.flavor, or None when not installed}."""
    umu = Path(umu)
    if not (umu / MAIN_DLL).is_file():
        return {"installed": False, "version": None, "dll": None, "flavor": None}
    loaders = _loaders(umu)
    return {"installed": True, "version": read_marker(umu), "dll": loaders[0] if loaders else None, "flavor": flavor(umu)}


def _free(umu, dll, main):
    """Refuse a <dll>.dll that is somebody else's file."""
    target = umu / f"{dll}.dll"
    if (target.exists() or target.is_symlink()) and not _loads_optiscaler(target, main):
        raise Error(f"{dll}.dll in the prefix isn't OptiScaler: choose another DLL")


def set_dll(umu, dll):
    """Make <dll>.dll the one name that loads OptiScaler (a link to OptiScaler.dll)."""
    _check_dll(dll)
    umu = Path(umu)
    main = umu / MAIN_DLL
    if not main.is_file():
        raise Error("OptiScaler isn't installed in this game")
    _free(umu, dll, main)
    for name in _loaders(umu):
        (umu / f"{name}.dll").unlink()
    (umu / f"{dll}.dll").symlink_to(MAIN_DLL)


def has_helixsr_section(path):
    """True if the ini file has a [HelixSR] section (HelixSR Scaler's)."""
    try:
        return re.search(r"^\s*\[HelixSR\]", path.read_text(errors="replace"), re.M | re.I) is not None
    except OSError:
        return False


def install(build, umu, ini, dll, swap):
    """Copy the build (a folder as nightly.update or helixsr.store leaves it) into umu: OptiScaler.dll, its
    folders and its marker; ini as OptiScaler.ini unless the game has one (for HelixSR Scaler, one with
    [HelixSR]); and <dll>.dll linked to OptiScaler.dll. swap(new, target) puts a folder in place at once
    (ctx.archive.swap_dir). Refused when OptiScaler (either flavor) is installed already: update it, or
    uninstall it first."""
    _check_dll(dll)
    build, umu = Path(build), Path(umu)
    if not (build / MAIN_DLL).is_file():
        raise Error("There is no OptiScaler to install: download it in Global first")
    kind = flavor(build)
    main = umu / MAIN_DLL
    if main.is_file():
        raise Error("OptiScaler is already installed in this game: update it, or uninstall it first")
    _free(umu, dll, build / MAIN_DLL)
    umu.mkdir(parents=True, exist_ok=True)
    for leftover in _leftovers(umu, kind):
        shutil.rmtree(leftover, ignore_errors=True)
    for name in _folders(kind):
        if (build / name).is_dir():
            new = umu / _copy_name(name)
            shutil.copytree(build / name, new)
            swap(new, umu / name)
    copy_file(build / MAIN_DLL, main)
    if (umu / INI).exists() and kind == SCALER and not has_helixsr_section(umu / INI):
        os.replace(umu / INI, umu / f"{INI}.bak")  # another OptiScaler's settings: kept aside, never lost
    if not (umu / INI).exists():
        copy_file(ini, umu / INI)
    set_dll(umu, dll)
    if (build / MARKER).is_file():
        copy_file(build / MARKER, umu / MARKER)


def update(build, umu):
    """Put the build's files over the game's: OptiScaler.dll, every file of its folders, and the marker.
    Whatever the build doesn't bring stays: the game's OptiScaler.ini (and HelixSR/helixsr.ini), its DLL name,
    the custom DLLs and anything the user added to the folders. A DLL name that was a copy of OptiScaler.dll
    becomes a link, so it loads the new one. The build must be the game's flavor."""
    build, umu = Path(build), Path(umu)
    if not (build / MAIN_DLL).is_file():
        raise Error("There is no OptiScaler to install: download it in Global first")
    if not (umu / MAIN_DLL).is_file():
        raise Error("OptiScaler isn't installed in this game")
    kind = flavor(build)
    if flavor(umu) != kind:
        raise Error("This game has another OptiScaler: uninstall it and install this one")
    loaders = _loaders(umu)  # before OptiScaler.dll changes: a copy of the old one wouldn't match the new
    for name in _folders(kind):
        for src in sorted((build / name).rglob("*")):
            rel = src.relative_to(build)
            if src.is_file() and not (str(rel) == SCALER_KEEP and (umu / rel).exists()):
                dest = umu / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                copy_file(src, dest)
    copy_file(build / MAIN_DLL, umu / MAIN_DLL)
    for name in loaders:
        link = umu / f"{name}.dll"
        if not link.is_symlink():
            link.unlink()
            link.symlink_to(MAIN_DLL)
    if (build / MARKER).is_file():
        copy_file(build / MARKER, umu / MARKER)


def uninstall(umu):
    """Remove OptiScaler's files from umu (see the top of this file); anything else stays. An umu
    left empty goes too."""
    umu = Path(umu)
    kind = flavor(umu)  # before its marker goes
    for name in _loaders(umu):
        (umu / f"{name}.dll").unlink()
    for name in (MAIN_DLL, INI, MARKER):
        p = umu / name
        if p.is_file() or p.is_symlink():
            p.unlink()
    for p in [*(umu / name for name in _folders(kind)), *_leftovers(umu, kind)]:
        if p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    try:
        umu.rmdir()
    except OSError:
        pass  # not empty, or not there
