"""ANR FG (Apex Neural Rendering frame generation, github.com/apex-compute/ANR): a third-party frame generation
provider for OptiScaler, in beta. OptiScaler takes a provider from a folder of its own `custom` folder that holds
dlssg/nvngx_dlssg.dll, when [FrameGen] FGNvngxReplacement names that folder. So ANR FG in a game is its folder NAME
in the game's OptiScaler/custom, nothing else: the module never sets FGNvngxReplacement (the user picks it in
OptiScaler's own menu) and never runs the release's install scripts.

Each FG release (tag FG-<version>, several a week) has the addon zip (for an OptiScaler already there, the one taken
here; the "full" one brings its own OptiScaler) and SHA256SUMS.txt. The module keeps, in its own folder:

    NAME/            the zip's folder as it is (dlssg/nvngx_dlssg.dll, its kernels, tables and nr_fg.ini)
    PACKAGE          the zip's nr_fg_package.json, which says its version
    MARKER           which release it is (nightly.MARKER_KEYS)

SHA256SUMS.txt is downloaded checked against GitHub's digest of it, and the zip against SHA256SUMS.txt (and against
GitHub's digest of it too, when there is one). ctx.archive unpacks only NAME and PACKAGE (refusing links, paths that
leave the folder, and more than MAX_UNPACKED bytes), and the new copy is swapped in whole: a failed download never
leaves half of one. update() gets ctx.http and ctx.archive as arguments, so the tests pass fakes.
"""
import contextlib
import filecmp
import json
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

from .files import Error
from .install import FOLDER
from .nightly import MAIN_DLL, MARKER, MARKER_KEYS

NAME = "Apex Neural Rendering FG"  # the folder, as OptiScaler's FGNvngxReplacement lists it
PROVIDER = "dlssg/nvngx_dlssg.dll"  # what makes a custom folder an FG provider for OptiScaler
PACKAGE = "nr_fg_package.json"
CUSTOM = "custom"  # OptiScaler's folder of providers, inside its own (install.FOLDER)
# The newest releases first; drafts, prereleases and releases that aren't ANR FG's (no FG- tag, no addon zip) are skipped.
API = "https://api.github.com/repos/apex-compute/ANR/releases?per_page=20"
# Where a release's files are: an answer pointing anywhere else isn't followed.
DOWNLOADS = "https://github.com/apex-compute/ANR/releases/download/"
TAG = re.compile(r"^FG-(\S{1,60})$")  # FG-V.B20
ADDON = re.compile(r"^anr-fg-(\S{1,60})-addon\.zip$")  # anr-fg-V.B20-addon.zip
SUMS = "SHA256SUMS.txt"
MAX_SUMS = 64 << 10  # ~300 bytes
MAX_ZIP = 256 << 20  # ~4 MB
MAX_UNPACKED = 1 << 30  # ~6 MB
MAX_FILES = 10_000  # ~30
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _version_ok(version):
    return isinstance(version, str) and 0 < len(version) <= 64 and version.isprintable() and version.strip() == version


def _asset(release, test):
    """{"name", "url", "sha256"} of the first asset whose name passes test, under DOWNLOADS; or None."""
    for a in release.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name, url = a.get("name") or "", a.get("browser_download_url")
        if not isinstance(name, str) or not test(name) or not isinstance(url, str) or not url.startswith(DOWNLOADS):
            continue
        digest = a.get("digest") or ""
        sha = digest[len("sha256:"):] if isinstance(digest, str) and digest.startswith("sha256:") else ""
        return {"name": name, "url": url, "sha256": sha.lower() if _HEX.match(sha) else None}
    return None


def pick(releases):
    """{tag, version, date, asset, url, sha256, sums_url, sums_sha256} of the newest ANR FG release in GitHub's list
    (sha256: GitHub's digest of the zip, or None)."""
    if not isinstance(releases, list):
        raise Error("GitHub sent an unexpected answer")
    for r in releases:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        m = TAG.match(r.get("tag_name") or "") if isinstance(r.get("tag_name"), str) else None
        if not m:
            continue
        zip_ = _asset(r, lambda n: ADDON.match(n) is not None)
        sums = _asset(r, lambda n: n == SUMS)
        if zip_ is None or sums is None:
            continue
        date = r.get("published_at") if isinstance(r.get("published_at"), str) else ""
        return {
            "tag": m.group(0),
            "version": m.group(1),
            "date": date[:10].replace("-", ""),
            "asset": zip_["name"],
            "url": zip_["url"],
            "sha256": zip_["sha256"],
            "sums_url": sums["url"],
            "sums_sha256": sums["sha256"],
        }
    raise Error("There is no ANR FG release to download")


def latest(http):
    """The newest ANR FG release, as pick() gives it (http: ctx.http)."""
    return pick(http.get_json(API))


def sha256_in(text, name):
    """The SHA-256 SHA256SUMS.txt gives for the file `name` ("<hex>  <name>", or "<hex> *<name>"), or None."""
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _HEX.match(parts[0]) and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _package(folder):
    """PACKAGE in folder as JSON, or {} (none, unreadable, not an object)."""
    try:
        data = json.loads((Path(folder) / PACKAGE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def version(folder):
    """The version PACKAGE says (V.B20), or None."""
    v = _package(folder).get("version")
    return v if _version_ok(v) else None


def has(folder):
    """True if folder keeps ANR FG (its provider DLL)."""
    return (Path(folder) / NAME / PROVIDER).is_file()


def status(folder):
    """{"version": PACKAGE's, "tag", "date"} of the kept copy, or None when there's none."""
    if not has(folder):
        return None
    try:
        marker = json.loads((Path(folder) / MARKER).read_text())
    except (OSError, ValueError):
        marker = {}
    marker = marker if isinstance(marker, dict) else {}
    tag, date = marker.get("tag"), marker.get("date")
    return {"version": version(folder), "tag": tag if isinstance(tag, str) else None,
            "date": date if isinstance(date, str) else None}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(new):
    """The version of an unpacked copy (NAME and PACKAGE in new); a files.Error says what's wrong."""
    new = Path(new)
    if any(p.is_symlink() for p in new.rglob("*")):  # what's copied into games must be the release's own files
        raise Error("ANR FG's archive has a symbolic link in it")
    if not (new / NAME / PROVIDER).is_file():
        raise Error(f"ANR FG's archive has no {NAME}/{PROVIDER}")
    v = version(new)
    if v is None:
        raise Error(f"ANR FG's archive doesn't say its version ({PACKAGE})")
    want = _package(new).get("provider_sha256")
    if isinstance(want, str) and _HEX.match(want) and _sha256(new / NAME / PROVIDER) != want.lower():
        raise Error(f"ANR FG's {PROVIDER} isn't the one {PACKAGE} names")
    return v


def update(folder, http, archive, task=None, stop=None, swap_lock=None):
    """Make folder hold the latest ANR FG (http: ctx.http, archive: ctx.archive, task: the ctx.task that shows it,
    or None). Returns (release, True) if it was downloaded, or (release, False) if folder already had it. swap_lock
    is held while the new copy takes the old one's place (so nobody copies from a folder that changes under them)."""
    folder = Path(folder)
    say = task.update if task else (lambda fraction, text: None)
    say(None, "Looking for the latest ANR FG")
    release = latest(http)
    st = status(folder)
    if st and st["tag"] == release["tag"]:
        return release, False
    if not release["sums_sha256"]:
        raise Error(f"GitHub gave no checksum for {SUMS}: {release['asset']} can't be checked")
    folder.parent.mkdir(parents=True, exist_ok=True)
    for old in folder.parent.glob(".anr-download.*"):  # left by a crash
        shutil.rmtree(old, ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=folder.parent, prefix=".anr-download.") as work:
        work = Path(work)
        sums = work / "sums.txt"  # the names come from GitHub's answer: never a path
        http.download(release["sums_url"], sums, checksum=("sha256", release["sums_sha256"]), max_bytes=MAX_SUMS, task=task)
        sha = sha256_in(sums.read_text(errors="replace"), release["asset"])
        if sha is None:
            raise Error(f"{SUMS} has no checksum for {release['asset']}")
        if release["sha256"] and release["sha256"] != sha:
            raise Error(f"{SUMS} and GitHub give {release['asset']} different checksums")
        say(None, f"Downloading {release['asset']}")
        zip_ = work / "addon.zip"
        http.download(release["url"], zip_, checksum=("sha256", sha), max_bytes=MAX_ZIP, task=task)
        say(None, "Unpacking")
        new = work / "new"
        archive.extract(zip_, new, only_top=(NAME, PACKAGE), symlinks="refuse", max_bytes=MAX_UNPACKED,
                        max_files=MAX_FILES, cancel=stop)
        check(new)
        marker = {k: release[k] for k in MARKER_KEYS if k != "sha256"}
        (new / MARKER).write_text(json.dumps({**marker, "sha256": sha}, indent=1) + "\n")
        with swap_lock or contextlib.nullcontext():
            if stop is not None and stop.is_set():  # the module was unloaded meanwhile: leave things as they are
                raise Error("Download stopped")
            archive.swap_dir(new, folder)
    return release, True


# ---------- In a game ----------

def game_dir(umu):
    """Where ANR FG goes in a game: <umu>/OptiScaler/custom/NAME."""
    return Path(umu) / FOLDER / CUSTOM / NAME


def _same(a, b):
    """True if folder b holds every file of folder a, the same."""
    try:
        for src in a.rglob("*"):
            if src.is_file():
                dest = b / src.relative_to(a)
                if dest.is_symlink() or not dest.is_file() or not filecmp.cmp(src, dest, shallow=True):
                    return False
    except OSError:
        return False
    return True


def game_state(umu, folder):
    """{"on": the game has ANR FG, "current": it's the copy kept in folder (None when it's off or there's none)}."""
    game = game_dir(umu)
    on = (game / PROVIDER).is_file()
    current = _same(Path(folder) / NAME, game) if on and has(folder) else None
    return {"on": on, "current": current}


def _leftovers(custom):
    """What a crash left in custom: our copy, and swap_dir's own .NAME.old-*/.new-*."""
    return [*custom.glob(f".{NAME}.copy"), *custom.glob(f".{NAME}.old-*"), *custom.glob(f".{NAME}.new-*")]


def enable(folder, umu, swap):
    """Put the kept ANR FG in the game (replacing a copy already there, whole); swap: ctx.archive.swap_dir."""
    src = Path(folder) / NAME
    if not has(folder):
        raise Error("Download ANR FG in Global first")
    if not (Path(umu) / MAIN_DLL).is_file():
        raise Error("OptiScaler isn't installed in this game")
    dest = game_dir(umu)
    custom = dest.parent
    if custom.is_symlink() or (custom.exists() and not custom.is_dir()):
        raise Error(f"{FOLDER}/{CUSTOM} in the game isn't a folder")
    custom.mkdir(parents=True, exist_ok=True)
    for p in _leftovers(custom):
        shutil.rmtree(p, ignore_errors=True)
    if dest.is_symlink():  # a link would be followed by the swap: ANR FG goes in its place
        dest.unlink()
    elif dest.exists() and not dest.is_dir():
        raise Error(f"{NAME} in the game's {FOLDER}/{CUSTOM} isn't a folder")
    copy = custom / f".{NAME}.copy"
    try:
        shutil.copytree(src, copy, symlinks=True)
        swap(copy, dest)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def disable(umu):
    """Take ANR FG's folder out of the game (a link in its place goes, never what it points to); the rest of custom
    (and an emptied custom too) stays, and so does a plain file of that name, which isn't ANR FG."""
    dest = game_dir(umu)
    if dest.is_symlink():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
    if dest.parent.is_dir() and not dest.parent.is_symlink():
        for p in _leftovers(dest.parent):
            shutil.rmtree(p, ignore_errors=True)
