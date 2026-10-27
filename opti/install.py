"""OptiScaler in a game's Proton prefix: the umu folder (UMU) Wine's OptiScaler hack loads it
from, as WINE_OPTISCALER_NAME=<dll>.dll says. There, <dll>.dll is a link to OptiScaler.dll.

What is OptiScaler's in umu, and nothing else is ever touched: OptiScaler.dll, OptiScaler.ini, the
OptiScaler folder, MARKER, and each <name>.dll of DLL_NAMES that is a link to OptiScaler.dll (or a
copy of it). OptiScaler is installed when OptiScaler.dll is there, also when someone put it by hand.
"""
import filecmp
import os
import re
import shutil
from pathlib import Path

from .files import Error, copy_file
from .nightly import MAIN_DLL, MARKER, read_marker

DLL_NAMES = ("dxgi", "d3d12", "dbghelp", "winmm")
INI = "OptiScaler.ini"
FOLDER = "OptiScaler"  # OptiScaler's own folder, next to OptiScaler.dll
COPY = f".{FOLDER}.copy"  # the build's folder, copied next to the game's before the swap
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


def _leftovers(umu):
    """What a crash left of an install: the copy, and swap_dir's own .OptiScaler.old-*/.new-* (nothing else)."""
    return [umu / COPY, *umu.glob(f".{FOLDER}.old-*"), *umu.glob(f".{FOLDER}.new-*")]


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
    that loads OptiScaler or None}."""
    umu = Path(umu)
    if not (umu / MAIN_DLL).is_file():
        return {"installed": False, "version": None, "dll": None}
    loaders = _loaders(umu)
    return {"installed": True, "version": read_marker(umu), "dll": loaders[0] if loaders else None}


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


def install(build, umu, global_ini, dll, swap):
    """Copy the build (a folder as nightly.update leaves it) into umu: OptiScaler.dll, the
    OptiScaler folder and its marker; global_ini as OptiScaler.ini unless the game has one; and
    <dll>.dll linked to OptiScaler.dll. swap(new, target) puts a folder in place at once
    (ctx.archive.swap_dir)."""
    _check_dll(dll)
    build, umu = Path(build), Path(umu)
    if not (build / MAIN_DLL).is_file():
        raise Error("There is no OptiScaler to install: download it in Global first")
    main = umu / MAIN_DLL
    _free(umu, dll, main if main.is_file() else build / MAIN_DLL)
    umu.mkdir(parents=True, exist_ok=True)
    for leftover in _leftovers(umu):
        shutil.rmtree(leftover, ignore_errors=True)
    if (build / FOLDER).is_dir():
        new = umu / COPY
        shutil.copytree(build / FOLDER, new)
        swap(new, umu / FOLDER)
    copy_file(build / MAIN_DLL, main)
    if not (umu / INI).exists():
        copy_file(global_ini, umu / INI)
    set_dll(umu, dll)
    if (build / MARKER).is_file():
        copy_file(build / MARKER, umu / MARKER)


def update(build, umu):
    """Put the build's files over the game's: OptiScaler.dll, every file of the OptiScaler folder, and the
    marker. Whatever the build doesn't bring stays: the game's OptiScaler.ini, its DLL name, the custom DLLs
    and anything the user added to the folder. A DLL name that was a copy of OptiScaler.dll becomes a link,
    so it loads the new one."""
    build, umu = Path(build), Path(umu)
    if not (build / MAIN_DLL).is_file():
        raise Error("There is no OptiScaler to install: download it in Global first")
    if not (umu / MAIN_DLL).is_file():
        raise Error("OptiScaler isn't installed in this game")
    loaders = _loaders(umu)  # before OptiScaler.dll changes: a copy of the old one wouldn't match the new
    for src in sorted((build / FOLDER).rglob("*")):
        if src.is_file():
            dest = umu / FOLDER / src.relative_to(build / FOLDER)
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
    for name in _loaders(umu):
        (umu / f"{name}.dll").unlink()
    for name in (MAIN_DLL, INI, MARKER):
        p = umu / name
        if p.is_file() or p.is_symlink():
            p.unlink()
    for p in [umu / FOLDER, *_leftovers(umu)]:
        if p.is_symlink():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    try:
        umu.rmdir()
    except OSError:
        pass  # not empty, or not there
