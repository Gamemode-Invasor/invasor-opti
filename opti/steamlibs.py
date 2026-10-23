"""Where a game's Proton prefix is (the Steam libraries, as in invasor-companion's steam.py), and the
game's launch options as Steam keeps them."""
import os
import re
from pathlib import Path

from . import kv

STEAM_ROOT = Path.home() / ".local/share/Steam"
# SD cards and other disks mounted by the system, even if Steam doesn't list them.
MEDIA_GLOBS = ("/run/media/*/steamapps", "/run/media/*/*/steamapps")
VDF_PATH = re.compile(r'^\s*"path"\s+"(.*)"', re.MULTILINE)
# Where Wine's OptiScaler hack (GE-Proton, proton-cachyos) loads WINE_OPTISCALER_NAME from.
UMU = Path("pfx/drive_c/windows/system32/umu")


def libraries(steam_root=STEAM_ROOT, media_globs=MEDIA_GLOBS):
    """Every existing steamapps folder (Steam's own, libraryfolders.vdf, mounted media), each once."""
    found = [Path(steam_root) / "steamapps"]
    try:
        text = (Path(steam_root) / "steamapps/libraryfolders.vdf").read_text(errors="replace")
        found += [Path(p.replace("\\\\", "\\")) / "steamapps" for p in VDF_PATH.findall(text)]
    except OSError:
        pass
    for pattern in media_globs:
        found += sorted(Path("/").glob(pattern.lstrip("/")))
    return sorted({Path(os.path.realpath(p)) for p in found if p.is_dir()})


def umu_dir(appid, steam_root=STEAM_ROOT, media_globs=MEDIA_GLOBS):
    """compatdata/<appid>/pfx/drive_c/windows/system32/umu of the first library that has the game's
    prefix (it may not exist yet), else None: the game never ran with Proton."""
    for lib in libraries(steam_root, media_globs):
        prefix = lib / "compatdata" / appid
        if (prefix / "pfx" / "drive_c").is_dir():
            return prefix / UMU
    return None


def launch_options(appid, steam_root=STEAM_ROOT):
    """The game's launch options: a non-Steam shortcut's from shortcuts.vdf, a Steam game's from
    localconfig.vdf ("" when it has none). None if Steam's files say nothing about the game. Steam
    writes shortcuts.vdf at once, but localconfig.vdf only from time to time: a Steam game's may lag."""
    for f in sorted((Path(steam_root) / "userdata").glob("*/config/shortcuts.vdf")):
        try:
            shortcuts = kv.loads_binary(f.read_bytes()).get("shortcuts")
        except (OSError, ValueError):
            continue
        for entry in shortcuts.values() if isinstance(shortcuts, dict) else ():
            low = {k.lower(): v for k, v in entry.items()} if isinstance(entry, dict) else {}
            if isinstance(low.get("appid"), int) and str(low["appid"] & 0xFFFFFFFF) == appid:
                return str(low.get("launchoptions") or "")
    for f in sorted((Path(steam_root) / "userdata").glob("*/config/localconfig.vdf")):
        try:
            root = kv.loads_text(f.read_text(errors="replace"))
        except OSError:
            continue
        apps = kv.get(root, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", default={})
        entry = kv.get(apps, appid)
        if isinstance(entry, dict):
            return str(kv.get(entry, "LaunchOptions", default=""))
    return None
