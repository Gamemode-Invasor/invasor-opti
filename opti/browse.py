"""One folder for the file browser (from Companion's): its folders and its .dll files."""
import os
from pathlib import Path

LIMIT = 1000


def listdir(path, exts=(".dll",), limit=LIMIT):
    """{"path", "parent", "dirs": [{"name", "label"}], "files": [{"name"}], "error"}.

    Hidden entries left out, sorted (case-insensitive), folders before files, only files ending in one of
    exts, at most `limit` entries in all. A folder that can't be read comes back with "error" and nothing
    listed (the path is still the one asked for).
    """
    folder = Path(os.path.realpath(path))
    parent = None if folder == folder.parent else str(folder.parent)
    out = {"path": str(folder), "parent": parent, "dirs": [], "files": [], "error": None}
    try:
        entries = [e for e in os.scandir(folder) if not e.name.startswith(".")]
        dirs = sorted((e.name for e in entries if e.is_dir()), key=str.lower)
        names = sorted((e.name for e in entries if not e.is_dir() and e.is_file() and e.name.lower().endswith(exts)), key=str.lower)
    except OSError as e:
        out["error"] = e.strerror or str(e)
        return out
    out["dirs"] = [{"name": n, "label": n} for n in dirs[:limit]]
    out["files"] = [{"name": n} for n in names[: max(0, limit - len(out["dirs"]))]]
    return out
