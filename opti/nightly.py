"""OptiScaler's nightly builds (github.com/optiscaler/OptiScaler-nightly): which one is the latest,
which one is downloaded, and getting it.

A downloaded build is a folder with the .7z's content as it is plus MARKER, a JSON file saying
which build it is. A new one is unpacked next to it and swapped in with two renames: a failed
download never leaves half a build. Only files come from GitHub: a build with a symbolic link in it
is refused, so nothing copied from it can point anywhere else.
"""
import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .files import Error, has_symlink, replace_dir

# Every nightly is a prerelease: /releases/latest skips them, the first of the list is the newest.
API = "https://api.github.com/repos/optiscaler/OptiScaler-nightly/releases?per_page=1"
# Where a nightly's files are: an answer pointing anywhere else isn't followed.
DOWNLOADS = "https://github.com/optiscaler/OptiScaler-nightly/releases/download/"
USER_AGENT = "invasor-opti"
MARKER = ".invasor-opti.version"
MARKER_KEYS = ("tag", "version", "date", "asset", "sha256")
MAIN_DLL = "OptiScaler.dll"
ASSET = re.compile(r"^OptiScaler_(v[^_]+)_(\d{8})\.7z$")  # OptiScaler_v10.0.0-pre1_20261023.7z
CHUNK = 1024 * 1024
MAX_SIZE = 512 * 1024 * 1024  # a build is ~57 MB
UNPACK_TIMEOUT = 300


def open_url(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            raise Error("GitHub API rate limit reached, try again later") from None
        raise Error(f"GitHub answered {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Error(f"No connection to GitHub ({getattr(e, 'reason', e)})") from None


def pick(release):
    """{tag, version, date, asset, url, size, sha256} of a release's .7z."""
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
            "size": int(a.get("size") or 0),
            "sha256": digest[len("sha256:"):] if digest.startswith("sha256:") else None,
        }
    raise Error("The latest nightly has no .7z to download")


def latest(open_=open_url):
    with open_(API, 15) as r:
        try:
            releases = json.loads(r.read())
        except (OSError, ValueError):
            raise Error("GitHub sent an unreadable answer") from None
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


def download(release, dest, open_=open_url, progress=lambda done, total: None, stop=None):
    """The release's .7z into the file dest, checked against its size and sha256."""
    total = release["size"]
    if not release.get("sha256"):
        raise Error(f"GitHub gave no checksum for {release['asset']}: it can't be checked")
    if total > MAX_SIZE:
        raise Error(f"{release['asset']} is too big ({total // CHUNK} MB)")
    sha = hashlib.sha256()
    done = 0
    with open_(release["url"], 30) as r, open(dest, "wb") as f:
        while True:
            if stop is not None and stop.is_set():
                raise Error("Download stopped")
            try:
                chunk = r.read(CHUNK)
            except OSError as e:
                raise Error(f"Connection lost ({e})") from None
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_SIZE:
                raise Error(f"{release['asset']} is too big")
            sha.update(chunk)
            f.write(chunk)
            progress(done, total)
    if done != total:
        raise Error(f"Download incomplete or wrong ({done} bytes, {total} expected)")
    if sha.hexdigest() != release["sha256"].lower():
        raise Error(f"{release['asset']} is damaged (its checksum doesn't match)")


def _unpacker():
    """The command lines (list, extract) of a program that reads .7z: bsdtar (libarchive, part
    of SteamOS) or 7-Zip."""
    if shutil.which("bsdtar"):
        return (lambda a: ["bsdtar", "-tf", a]), (lambda a, d: ["bsdtar", "-xf", a, "-C", d])
    for name in ("7z", "7za", "7zz"):
        if shutil.which(name):
            return (lambda a, n=name: [n, "l", "-ba", "-slt", a]), (lambda a, d, n=name: [n, "x", "-y", f"-o{d}", a])
    raise Error("Can't unpack a .7z: install bsdtar (libarchive) or 7-Zip")


def _run(cmd, stop=None):
    """cmd's output; it's killed when stop is set (the module unloading) or after UNPACK_TIMEOUT."""
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors="replace")
    except OSError as e:
        raise Error(f"{cmd[0]} failed ({e})") from None
    deadline = time.monotonic() + UNPACK_TIMEOUT
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if (stop is not None and stop.is_set()) or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise Error("Unpacking stopped" if stop is not None and stop.is_set() else f"{cmd[0]} took too long") from None
    if proc.returncode:
        raise Error(f"{cmd[0]} failed: {(err or out).strip()[-200:]}")
    return out


def names(listing, seven_zip):
    """The paths of an archive listing (bsdtar -t: one per line; 7z -slt: "Path = …" lines). A 7z entry
    that is a symbolic link comes as None: 7-Zip would follow it while unpacking (bsdtar never does)."""
    if not seven_zip:
        return [line for line in listing.splitlines() if line]
    out = []
    for line in listing.splitlines():
        if line.startswith("Path = "):
            out.append(line[len("Path = "):])
        elif line.startswith("Symbolic Link = ") and line[len("Symbolic Link = "):].strip() and out:
            out[-1] = None
    return out


def safe(name):
    """True if the archive entry stays inside the folder it's unpacked into."""
    if name is None:
        return False
    p = name.replace("\\", "/")
    return not (p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"))


def extract(archive, dest, stop=None):
    """Unpack archive into the folder dest, refusing any path that would land outside it."""
    list_cmd, extract_cmd = _unpacker()
    cmd = list_cmd(str(archive))
    # 7z may list the archive itself first.
    bad = [n for n in names(_run(cmd, stop), cmd[0] != "bsdtar") if n != str(archive) and not safe(n)]
    if bad:
        raise Error(f"The archive has a path outside its folder: {bad[0] or 'a symbolic link'}")
    _run(extract_cmd(str(archive), str(dest)), stop)
    if has_symlink(dest):
        raise Error("The archive has a symbolic link: it isn't a normal OptiScaler build")
    if not (Path(dest) / MAIN_DLL).is_file():
        raise Error(f"The archive has no {MAIN_DLL}")


def update(folder, open_=open_url, progress=lambda fraction, text: None, stop=None, swap_lock=None):
    """Make folder hold the latest nightly. Returns (release, True) if it was downloaded, or
    (release, False) if folder already had it. swap_lock is held while the new build takes the old
    one's place (so nobody copies from a folder that changes under them)."""
    folder = Path(folder)
    progress(None, "Looking for the latest nightly")
    release = latest(open_)
    current = read_marker(folder)
    if current and current["tag"] == release["tag"] and (folder / MAIN_DLL).is_file():
        return release, False
    folder.parent.mkdir(parents=True, exist_ok=True)
    for old in folder.parent.glob(".download.*"):  # left by a crash
        shutil.rmtree(old, ignore_errors=True)
    work = Path(tempfile.mkdtemp(dir=folder.parent, prefix=".download."))
    try:
        archive = work / "build.7z"  # the asset's name comes from GitHub's answer: never a path
        mb = release["size"] / CHUNK
        download(release, archive, open_,
                 lambda done, total: progress(done / total if total else None, f"Downloading: {done / CHUNK:.0f} of {mb:.0f} MB"),
                 stop)
        progress(None, "Unpacking")
        new = work / "new"
        new.mkdir()
        extract(archive, new, stop)
        (new / MARKER).write_text(json.dumps({k: release[k] for k in MARKER_KEYS}, indent=1) + "\n")
        with swap_lock or contextlib.nullcontext():
            if stop is not None and stop.is_set():  # the module was unloaded meanwhile: leave things as they are
                raise Error("Download stopped")
            replace_dir(new, folder, work / "old")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return release, True
