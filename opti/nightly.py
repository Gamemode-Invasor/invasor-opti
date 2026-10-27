"""OptiScaler's nightly builds (github.com/optiscaler/OptiScaler-nightly): which one is the latest,
which one is downloaded, and getting it.

A downloaded build is a folder with the .7z's content as it is plus MARKER, a JSON file saying
which build it is. A new one is unpacked next to it and swapped in at once: a failed download never
leaves half a build. The core does the work: ctx.http downloads it, checked against GitHub's sha256,
and ctx.archive unpacks it (refusing symbolic links, so nothing copied from it can point anywhere
else) and swaps it in. update() gets them as arguments, so the tests pass fakes.
"""
import contextlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from .files import Error

# Every nightly is a prerelease: /releases/latest skips them, the first of the list is the newest.
API = "https://api.github.com/repos/optiscaler/OptiScaler-nightly/releases?per_page=1"
# Where a nightly's files are: an answer pointing anywhere else isn't followed.
DOWNLOADS = "https://github.com/optiscaler/OptiScaler-nightly/releases/download/"
MARKER = ".invasor-opti.version"
MARKER_KEYS = ("tag", "version", "date", "asset", "sha256")
MAIN_DLL = "OptiScaler.dll"
ASSET = re.compile(r"^OptiScaler_(v[^_]+)_(\d{8})\.7z$")  # OptiScaler_v10.0.0-pre1_20261023.7z
MAX_SIZE = 512 << 20  # a build is ~57 MB


def pick(release):
    """{tag, version, date, asset, url, sha256} of a release's .7z."""
    if not isinstance(release, dict):
        raise Error("GitHub sent an unexpected answer")
    for a in release.get("assets") or []:
        name = (a.get("name") or "") if isinstance(a, dict) else ""
        url = a.get("browser_download_url") if name.endswith(".7z") else None
        if not isinstance(url, str) or not url.startswith(DOWNLOADS):
            continue
        m = ASSET.match(name)
        digest = a.get("digest") or ""
        return {
            "tag": release.get("tag_name") or name,
            "version": m.group(1) if m else name[: -len(".7z")],
            "date": m.group(2) if m else "",
            "asset": name,
            "url": url,
            "sha256": digest[len("sha256:"):] if digest.startswith("sha256:") else None,
        }
    raise Error("The latest nightly has no .7z to download")


def latest(http):
    """The newest nightly, as pick() gives it (http: ctx.http)."""
    releases = http.get_json(API)
    if not isinstance(releases, list) or not releases:
        raise Error("There is no nightly build to download")
    return pick(releases[0])


def read_marker(folder):
    """MARKER in folder (a downloaded build, or a game's umu folder), or None if there is none (or it's
    unreadable)."""
    try:
        data = json.loads((Path(folder) / MARKER).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tag"), str):
        return None
    return {k: data.get(k) for k in MARKER_KEYS}


def update(folder, http, archive, task=None, stop=None, swap_lock=None):
    """Make folder hold the latest nightly (http: ctx.http, archive: ctx.archive, task: the ctx.task
    that shows it, or None). Returns (release, True) if it was downloaded, or (release, False) if
    folder already had it. swap_lock is held while the new build takes the old one's place (so nobody
    copies from a folder that changes under them)."""
    folder = Path(folder)
    say = task.update if task else (lambda fraction, text: None)
    say(None, "Looking for the latest nightly")
    release = latest(http)
    current = read_marker(folder)
    if current and current["tag"] == release["tag"] and (folder / MAIN_DLL).is_file():
        return release, False
    if not release["sha256"]:
        raise Error(f"GitHub gave no checksum for {release['asset']}: it can't be checked")
    folder.parent.mkdir(parents=True, exist_ok=True)
    for old in folder.parent.glob(".download.*"):  # left by a crash
        shutil.rmtree(old, ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=folder.parent, prefix=".download.") as work:
        build = Path(work) / "build.7z"  # the asset's name comes from GitHub's answer: never a path
        http.download(release["url"], build, checksum=("sha256", release["sha256"]), max_bytes=MAX_SIZE, task=task)
        say(None, "Unpacking")
        new = Path(work) / "new"
        archive.extract(build, new, symlinks="refuse", cancel=stop)
        if not (new / MAIN_DLL).is_file():
            raise Error(f"The archive has no {MAIN_DLL}")
        (new / MARKER).write_text(json.dumps({k: release[k] for k in MARKER_KEYS}, indent=1) + "\n")
        with swap_lock or contextlib.nullcontext():
            if stop is not None and stop.is_set():  # the module was unloaded meanwhile: leave things as they are
                raise Error("Download stopped")
            archive.swap_dir(new, folder)
    return release, True
