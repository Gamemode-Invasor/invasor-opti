"""OptiScaler: OptiScaler.ini files from Invasor's panel, and OptiScaler itself in games.

The file is the selected game's, in its Proton prefix, in the folder Wine's OptiScaler hack loads
it from (install.UMU). A game gets its build's own file when OptiScaler is installed in it, with
only what this module needs set (INSTALL_INI, SCALER_INI).

In GLOBAL_DIR, NIGHTLY_NAME holds the latest OptiScaler nightly, downloaded on request
in a thread of its own (see nightly.py), HELIXSR_NAME HelixSR Scaler with HelixSR's upscalers, built on
this device by bin/helixsr-fetch.sh (also in a thread of its own, see helixsr.py), and CUSTOM_NAME the
DLLs the user brings (see custom.py). install.py puts either OptiScaler in a game, custom.py the DLLs.

A file is the only source of truth: every call reads it again, and a change rewrites just the
line of that key (comments, order and line ends stay as they are), atomically, and only if no
other program saved the file since the UI read it.
"""
import collections
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import custom, files, helixsr, ini, install, nightly

ASSETS = Path(__file__).resolve().parent / "assets"
# OptiScaler's own file (v10.0.0-pre1): a build's when it has none, and its defaults when none is downloaded.
OPTISCALER_INI = ASSETS / "OptiScaler.optiscaler.ini"
# Outside the module's folder, which an update replaces.
GLOBAL_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "invasor-opti"
NIGHTLY_NAME = "optiscaler"
HELIXSR_NAME = "helixsr"
FLAVORS = ("optiscaler", helixsr.FLAVOR)
# What a game's new OptiScaler.ini needs: its DLLs from its own folder (else HelixSR Scaler takes the game's .exe
# folder, where a game's own FSR, XeSS or no amdxcffx64.dll may be); for HelixSR Scaler, HLX FSR 4 as the
# FidelityFX upscaler too.
INSTALL_INI = (("Libraries", "OptiDllPath", r"c:\windows\system32\umu\OptiScaler"),)
SCALER_INI = (("Libraries", "FfxDx12SRPath", r"C:\windows\system32\umu\OptiScaler\amd_fidelityfx_upscaler_dx12.dll"),)
CUSTOM_NAME = "custom"
MAX_BYTES = 4 * 1024 * 1024  # OptiScaler's own is ~60 KB: anything this big isn't one

ctx = None
_write = threading.Lock()
_download = threading.Lock()  # held while a download runs
_build = threading.Lock()  # held while the downloaded build is copied, or replaced by a new one
_stop = threading.Event()
_last_error = None  # why the last download failed, for the UI
_thread = None  # the download's, while it runs
TEARDOWN_WAIT = 8  # of the core's 10 s for teardown
# HelixSR's build: the script that downloads and builds it, and how long it may take.
HELIXSR_SCRIPT = Path(__file__).resolve().parent / "bin" / "helixsr-fetch.sh"
HELIXSR_TIMEOUT = 3600  # it also downloads AMD's ~420 MB driver package
HELIXSR_RELEASE_API = "https://api.github.com/repos/lonewolf0622/HelixSR/releases/latest"  # the stable one
HELIXSR_CHECK_TTL = 3600  # seconds a found tag is reused; a failed check is retried after HELIXSR_CHECK_RETRY
HELIXSR_CHECK_RETRY = 120
_latest_cache = {"at": 0.0, "ttl": 0, "tag": None}
_latest_lock = threading.Lock()
SUPPORTS_TTL = 60  # seconds Steam's "yes, it can set launch options" is reused; a "no" is asked again after SUPPORTS_RETRY
SUPPORTS_RETRY = 10
_supports_cache = {"at": 0.0, "ttl": 0, "found": False}
_supports_lock = threading.Lock()
_helix = threading.Lock()  # held while a build runs
_helix_error = None  # why the last build failed, for the UI
_helix_thread = None  # the build's, while it runs
_helix_proc = None  # the script, while it runs


def setup(context):
    global ctx
    ctx = context


def _guarded(what, fn, user_error):
    """fn(), with its errors as the UI shows them: a bad argument, `user_error` for a files.Error (the
    message is for the user), Unavailable for what the system refused."""
    try:
        return fn()
    except ValueError as e:
        raise ctx.InvalidArgument(str(e)) from None
    except files.Error as e:
        raise user_error(str(e)) from None
    except OSError as e:
        raise ctx.Unavailable(f"can't {what}: {e.strerror or e}") from None


# ---------- Ini files ----------

def _appid(appid):
    """The digits of a real AppID (it ends up in a path)."""
    appid = str(appid or "")
    if not (appid.isascii() and appid.isdigit()):
        raise ctx.InvalidArgument("appid must be a number")
    return appid


def _umu_dir(appid):
    """The game's umu folder (it may not exist yet), or None: the game never ran with Proton."""
    try:
        prefix = ctx.paths.compatdata(appid)
    except ctx.Unavailable:  # no Steam folder
        return None
    return prefix / install.UMU if (prefix / "pfx" / "drive_c").is_dir() else None


def _ini_path(appid):
    """The game's OptiScaler.ini (it may not exist), or None if the game has no Proton prefix."""
    umu = _umu_dir(_appid(appid))
    return umu / install.INI if umu else None


def _launch_options(appid):
    """The game's launch options ("" when none), or None when nothing says. Steam's own answer, else
    Steam's files: shortcuts.vdf, and localconfig.vdf for a Steam game (which Steam writes only from
    time to time, so it may lag)."""
    try:
        return ctx.steam.launch_options(appid)
    except (ctx.Unavailable, ctx.InvalidArgument):
        pass
    try:
        shortcut = ctx.paths.shortcuts().get(appid)
        if shortcut is not None:
            return shortcut["options"]
        accounts = ctx.paths.userdata(all=True)
    except ctx.Unavailable:
        return None
    for account in accounts:
        try:
            root = ctx.vdf.loads_text((account / "config" / "localconfig.vdf").read_text(errors="replace"))
        except (OSError, ctx.InvalidArgument):
            continue
        entry = ctx.vdf.get(root, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", appid)
        if isinstance(entry, dict):
            return str(ctx.vdf.get(entry, "LaunchOptions", default=""))
    return None


def _read(path):
    """(text, encoding, mtime as a string) of the file; JSON numbers can't hold nanoseconds. The mtime is
    the one of the bytes read (taken before them: a save meanwhile shows as a change, never hides one)."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            if st.st_size > MAX_BYTES:
                raise ctx.Unavailable(f"{path.name} is too big to be an {install.INI}")
            raw = f.read()
        mtime = st.st_mtime_ns
    except OSError as e:
        raise ctx.Unavailable(f"can't read {path}: {e.strerror or e}") from None
    text, encoding = ini.decode(raw)
    return text, encoding, str(mtime)


def _find_key(text, section, key):
    """The key as ini.parse gives it ({"key", "value", "comments"}), or None."""
    for s in ini.parse(text):
        if s["name"] == section:
            return next((k for k in s["keys"] if k["key"] == key), None)
    return None


def _load_file(path):
    """{"state": "no_ini" | "ok", "path", "mtime", "sections"}: sections as ini.describe gives them."""
    if not path.is_file():
        return {"state": "no_ini", "path": str(path), "mtime": None, "sections": []}
    text, _, mtime = _read(path)
    return {"state": "ok", "path": str(path), "mtime": mtime, "sections": ini.describe(ini.parse(text))}


def _save(path, data, expected=None):
    """Replace the file with data (bytes), atomically, unless it changed since mtime `expected`
    (None: whatever is there). Returns the new mtime (string). A symlinked ini stays a symlink:
    its target is replaced."""
    path = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            shutil.copymode(path, tmp)
        else:
            os.chmod(tmp, 0o644)  # mkstemp makes it 0600
        if expected is not None and str(path.stat().st_mtime_ns) != expected:
            raise ctx.Unavailable(f"{path.name} was changed by another program: reload and try again")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # already renamed: don't hide the real error
        raise
    return str(path.stat().st_mtime_ns)


def _with_value(text, encoding, section, key, value):
    """(text with the key set, the value as written). A select's value must be one of its options;
    InvalidArgument says why it can't be written."""
    found = _find_key(text, section, key)
    if found is None:
        raise ctx.InvalidArgument(f"{install.INI} has no [{section}] {key}")
    try:
        value = ini.check_value(value)
    except ValueError as e:
        raise ctx.InvalidArgument(str(e)) from None
    control = ini.infer(found["comments"], found["value"])
    if control["kind"] == "select":
        option = ini.match_option(control["options"], value)
        if option is None:
            raise ctx.InvalidArgument(f"{value!r} isn't a value of [{section}] {key}")
        value = option
    try:
        new_text = ini.set_value(text, section, key, value)
        new_text.encode(encoding)
    except (KeyError, ValueError) as e:  # UnicodeEncodeError is a ValueError
        raise ctx.InvalidArgument(str(e)) from None
    return new_text, value


def _apply(path, values):
    """Write several values ({(section, key): value}) in one save; Unavailable when one can't be written (a key
    the file hasn't, a value it doesn't take), and then nothing is."""
    with _write:
        text, encoding, current = _read(path)
        for (section, key), value in values.items():
            try:
                text, value = _with_value(text, encoding, section, key, value)
            except ctx.InvalidArgument as e:
                raise ctx.Unavailable(f"can't set [{section}] {key} in {install.INI}: {e}") from None
        _save(path, text.encode(encoding), current)
    ctx.log.info("%s: %d values set", path, len(values))


def _set_in_file(path, section, key, value, mtime):
    """Write one value. A select's value must be one of its options (what load offered); returns
    {"value", "mtime"}."""
    with _write:
        text, encoding, current = _read(path)
        if current != str(mtime):
            raise ctx.Unavailable(f"{install.INI} was changed by another program: reload and try again")
        new_text, value = _with_value(text, encoding, section, key, value)
        new_mtime = _save(path, new_text.encode(encoding), current)
    ctx.log.info("%s: [%s] %s = %s", path, section, key, value)
    return {"value": value, "mtime": new_mtime}


# ---------- The selected game ----------

def _can_set_launch_options():
    """ctx.steam.supports("set_launch_options"), remembered for a while: it asks Steam over CEF."""
    with _supports_lock:
        if time.monotonic() - _supports_cache["at"] >= _supports_cache["ttl"]:
            found = ctx.steam.supports("set_launch_options")
            _supports_cache.update(at=time.monotonic(), ttl=SUPPORTS_TTL if found else SUPPORTS_RETRY, found=found)
        return _supports_cache["found"]


def load(appid):
    """The game's file, as _load_file (each key with "default": OptiScaler's own value for it, or None), or
    state "no_prefix" if the game has no Proton prefix. Plus:
    - "install": install.state of its umu folder, with "extras" (the custom slots it has on) and "launch_ok"
      (whether its launch options load OptiScaler as that DLL; None when there's nothing to tell) and
      "can_apply" (whether game_apply_launch can write them); None without a prefix;
    - "build": the nightly Global has downloaded (nightly.read_marker), or None;
    - "helixsr": the HelixSR Scaler Global has built, the same way, or None;
    - "custom": {slot: kept in Global}."""
    appid = _appid(appid)
    path = _ini_path(appid)
    if path is None:
        r = {"state": "no_prefix", "path": None, "mtime": None, "sections": [], "install": None}
    else:
        r = _load_file(path)
        defaults = _optiscaler_defaults(install.state(path.parent)["flavor"])
        for sec in r["sections"]:
            for k in sec["keys"]:
                k["default"] = defaults.get((sec["name"], k["key"]))  # None: OptiScaler's file hasn't the key
        r["install"] = {**install.state(path.parent), "extras": custom.game_state(path.parent)}
        dll = r["install"]["dll"]
        options = _launch_options(appid) if dll else None
        # Whether the launch options load OptiScaler as that DLL; None when there's nothing to tell.
        r["install"]["launch_ok"] = install.in_launch_options(options, dll) if options is not None else None
        # Whether "Apply to the game" can write them (Steam may lack the call: then only Copy).
        r["install"]["can_apply"] = bool(dll) and r["install"]["installed"] and _can_set_launch_options()
    r["build"] = nightly.read_marker(_nightly_dir())
    r["helixsr"] = nightly.read_marker(_helixsr_dir())
    r["custom"] = {slot: kept is not None for slot, kept in custom.status(_custom_dir()).items()}
    return r


def _optiscaler_defaults(flavor=None):
    """{(section, key): value} of OptiScaler's own file: the downloaded build's (HelixSR Scaler's for its
    flavor), else the one in assets."""
    built = (_helixsr_dir() if flavor == helixsr.FLAVOR else _nightly_dir()) / install.INI
    try:
        text, _, _ = _read(built if built.is_file() else OPTISCALER_INI)
    except ctx.Unavailable:
        return {}
    return {(s["name"], k["key"]): k["value"] for s in ini.parse(text) for k in s["keys"]}


def set_value(appid, section, key, value, mtime):
    """Write one value of the game's file (see _set_in_file)."""
    path = _ini_path(appid)
    if path is None or not path.is_file():
        raise ctx.Unavailable(f"this game has no {install.INI}")
    return _set_in_file(path, section, key, value, mtime)


def _game_umu(appid):
    path = _ini_path(appid)
    if path is None:
        raise ctx.Unavailable("this game has no Proton prefix yet: run it once with Proton")
    return path.parent


def _in_game(appid, what, fn):
    """fn(umu) under the write lock; returns load(appid)."""
    umu = _game_umu(appid)
    with _write:
        _guarded(what, lambda: fn(umu), ctx.Unavailable)
    ctx.log.info("%s: %s", umu, what)
    return load(appid)


def _flavor_build(flavor):
    """The folder of the flavor's build in Global, checked to be there."""
    if flavor == helixsr.FLAVOR:
        build, missing = _helixsr_dir(), "There is no HelixSR Scaler to install: compile HelixSR in Global first"
    else:
        build, missing = _nightly_dir(), "There is no OptiScaler to install: download it in Global first"
    if nightly.read_marker(build) is None:
        raise ctx.Unavailable(missing)
    return build


def game_install(appid, dll, flavor="optiscaler"):
    """Install a build of Global into the game, loaded as <dll>.dll: the nightly ("optiscaler") or HelixSR
    Scaler ("helixsr-scaler"). A game without an OptiScaler.ini gets the build's own with INSTALL_INI set;
    HelixSR Scaler's always has SCALER_INI and INSTALL_INI set, and it also gets the FSR 4 driver DLLs when
    they're kept in Global. Set up whole or not at all."""
    if flavor not in FLAVORS:
        raise ctx.InvalidArgument(f"flavor must be one of {', '.join(FLAVORS)}")
    scaler = flavor == helixsr.FLAVOR
    ini_path = _game_umu(appid) / install.INI
    # A game's own OptiScaler.ini stays as it is; HelixSR Scaler sets aside one without [HelixSR] (install.install).
    fresh = not ini_path.is_file() or (scaler and not install.has_helixsr_section(ini_path))
    with _build:
        build = _flavor_build(flavor)
        what = f"install {'HelixSR Scaler' if scaler else 'OptiScaler'} as {dll}.dll"
        ini_src = build / install.INI if (build / install.INI).is_file() else OPTISCALER_INI
        _in_game(appid, what, lambda umu: install.install(build, umu, ini_src, dll, ctx.archive.swap_dir))
    umu = ini_path.parent
    try:  # a HelixSR Scaler without its two keys loads the game's own DLLs
        if fresh or scaler:
            _apply(ini_path, {(s, k): v for s, k, v in (*INSTALL_INI, *(SCALER_INI if scaler else ()))})
        if scaler and custom.status(_custom_dir())["fsr4"]:
            with _write:
                custom.enable(_custom_dir(), umu, "fsr4")
    except BaseException:
        with _write:
            install.uninstall(umu)
        raise
    return load(appid)


def game_update(appid):
    """Bring the game's OptiScaler to Global's build of its flavor (see install.update)."""
    umu = _game_umu(appid)
    state = install.state(umu)
    if not state["installed"]:
        raise ctx.Unavailable("OptiScaler isn't installed in this game")
    with _build:
        build = _flavor_build(state["flavor"])
        return _in_game(appid, "update OptiScaler", lambda umu: install.update(build, umu))


def game_uninstall(appid):
    return _in_game(appid, "uninstall OptiScaler", install.uninstall)


def game_set_dll(appid, dll):
    return _in_game(appid, f"load OptiScaler as {dll}.dll", lambda umu: install.set_dll(umu, dll))


def game_apply_launch(appid):
    """Make the game's launch options load OptiScaler as its DLL name (install.with_dll keeps the rest of
    the line); returns load(appid) with "verified": False when Steam took them but couldn't read them back."""
    appid = _appid(appid)
    umu = _game_umu(appid)
    state = install.state(umu)
    if not (state["installed"] and state["dll"]):
        raise ctx.Unavailable("OptiScaler isn't installed in this game with a DLL name")
    try:  # Steam's own line only: localconfig.vdf may lag, and would undo what the user just set
        current = ctx.steam.launch_options(appid)
    except (ctx.Unavailable, ctx.InvalidArgument):
        raise ctx.Unavailable("Steam didn't tell the current launch options: try again") from None
    line = install.with_dll(current, state["dll"])
    r = ctx.steam.set_launch_options(appid, line)
    ctx.log.info("%s: launch options set to %r", appid, line)
    return {**load(appid), "verified": r["verified"]}


def game_extra(appid, slot, on):
    """Turn a custom slot on or off for the game."""
    if slot not in custom.SLOTS:
        raise ctx.InvalidArgument(f"slot must be one of {', '.join(custom.SLOTS)}")
    if not isinstance(on, bool):
        raise ctx.InvalidArgument("on must be true or false")
    if on:
        return _in_game(appid, f"turn {slot} on", lambda umu: custom.enable(_custom_dir(), umu, slot))
    return _in_game(appid, f"turn {slot} off", lambda umu: custom.disable(umu, slot))


# ---------- Global ----------

def _custom_dir():
    return GLOBAL_DIR / CUSTOM_NAME


def custom_status():
    """{slot: [{"name", "product", "version"}] or None}, see custom.status."""
    return custom.status(_custom_dir())


def custom_set(slot, path):
    """Keep a copy of a slot's DLLs, from the file the user picked; returns custom_status()."""
    with _write:
        _guarded("copy it", lambda: custom.store(_custom_dir(), slot, path, ctx.archive.swap_dir), ctx.InvalidArgument)
    ctx.log.info("custom %s: from %s", slot, path)
    return custom_status()


def custom_remove(slot):
    with _write:
        _guarded("remove it", lambda: custom.remove(_custom_dir(), slot), ctx.InvalidArgument)
    ctx.log.info("custom %s: removed", slot)
    return custom_status()


# ---------- The nightly ----------

def _nightly_dir():
    return GLOBAL_DIR / NIGHTLY_NAME


def _helixsr_dir():
    return GLOBAL_DIR / HELIXSR_NAME


def nightly_status():
    """{"installed": the downloaded build (see nightly.read_marker) or None, "busy", "error"}."""
    return {"installed": nightly.read_marker(_nightly_dir()), "busy": _download.locked(), "error": _last_error}


def nightly_download():
    """Start getting the latest nightly; {"started", "busy"}. nightly_status tells how it went."""
    global _thread
    if not _download.acquire(blocking=False):
        return {"started": False, "busy": True}
    _stop.clear()
    try:
        _thread = threading.Thread(target=_download_job, name="opti-nightly", daemon=True)
        _thread.start()
    except BaseException:
        _download.release()
        raise
    return {"started": True, "busy": False}


def _download_job():
    global _last_error
    try:
        _last_error = None
        try:
            task = ctx.task("OptiScaler nightly")
        except ctx.Unavailable as e:
            ctx.log.warning("task not shown: %s", e)
            task = None
        try:
            release, downloaded = nightly.update(_nightly_dir(), ctx.http, ctx.archive, task=task, stop=_stop, swap_lock=_build)
        except files.Error as e:
            _last_error = str(e)
        except (ctx.Unavailable, ctx.InvalidArgument) as e:  # the download or the unpacking, said by the core
            _last_error = f"Download failed ({e})"
        except Exception as e:  # never die silently: the UI shows it
            ctx.log.exception("nightly download failed")
            _last_error = f"Download failed ({e})"
        if _last_error:
            ctx.log.warning("nightly: %s", _last_error)
            if task:
                task.fail(_last_error)
            return
        text = f"{release['version']} {release['date']}".strip()
        text = f"Downloaded {text}" if downloaded else f"{text} is already the latest"
        ctx.log.info("nightly: %s", text)
        if task:
            task.done(text)
    finally:
        _download.release()


# ---------- HelixSR's build ----------

# Lines of the script that move the task on: (start of the line, fraction, text).
HELIXSR_STAGES = (
    ("looking", 0.05, "Looking for the latest stable version"),
    ("version:", 0.10, "Version found"),
    ("downloading", 0.20, "Downloading"),
    ("building", None, "Building (it can take a few minutes)"),
)


SAY = "[helixsr-fetch] "  # how the script's own lines start
DIE = SAY + "error: "  # and the one it dies with


def _helixsr_stage(line):
    """(fraction, text) for a line of the script that starts a stage, else None."""
    if not line.startswith(SAY):
        return None
    rest = line[len(SAY):]
    for key, fraction, text in HELIXSR_STAGES:
        if rest.startswith(key):
            return fraction, text
    return None


def helixsr_status():
    """{"installed": the kept build (see nightly.read_marker) or None, "busy", "error"}: whether a build runs,
    and why the last one failed (or None)."""
    return {"installed": nightly.read_marker(_helixsr_dir()), "busy": _helix.locked(), "error": _helix_error}


def _helixsr_latest():
    """Tag of the latest stable HelixSR release, or None (offline, rate limit, odd answer). Cached in memory."""
    with _latest_lock:
        cache = _latest_cache
        if time.monotonic() - cache["at"] < cache["ttl"]:
            return cache["tag"]
        tag = None
        try:
            answer = ctx.http.get_json(HELIXSR_RELEASE_API)
            tag = answer.get("tag_name") if isinstance(answer, dict) else None
            tag = tag if isinstance(tag, str) and tag else None
        except (ctx.Unavailable, ctx.InvalidArgument) as e:
            ctx.log.info("can't check the latest HelixSR: %s", e)
        cache.update(at=time.monotonic(), ttl=HELIXSR_CHECK_TTL if tag else HELIXSR_CHECK_RETRY, tag=tag)
        return tag


def _version_key(version):
    """(2, 0, 1) for "v2.0.1" or "2.0.1.0" (trailing zeros don't count); None without numbers."""
    nums = [int(n) for n in re.findall(r"\d+", version or "")]
    if not nums:
        return None
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def helixsr_check():
    """{"installed": the kept build's version (or None), "latest": the latest stable tag (None when unknown),
    "update": the kept build is older}. Asks GitHub (cached), so it's apart from helixsr_status."""
    installed = (nightly.read_marker(_helixsr_dir()) or {}).get("version")
    latest = _helixsr_latest()
    have, newest = _version_key(installed), _version_key(latest)
    return {"installed": installed, "latest": latest, "update": have is not None and newest is not None and have < newest}


def helixsr_build():
    """Start downloading and building the latest stable HelixSR into HELIXSR_NAME; {"started", "busy"}.
    helixsr_status tells how it went."""
    global _helix_thread
    if not HELIXSR_SCRIPT.is_file():
        raise ctx.Unavailable(f"build script not found: {HELIXSR_SCRIPT}")
    # Heavy (download and build): not while a game is running.
    if ctx.game.running:
        raise ctx.Unavailable("a game is running: close it to build HelixSR")
    if not _helix.acquire(blocking=False):
        return {"started": False, "busy": True}
    _stop.clear()
    try:
        _helix_thread = threading.Thread(target=_helixsr_job, name="opti-helixsr", daemon=True)
        _helix_thread.start()
    except BaseException:
        _helix.release()
        raise
    return {"started": True, "busy": False}


def _helixsr_job():
    global _helix_error
    work = None
    try:
        _helix_error = None
        try:
            task = ctx.task("HelixSR")
        except ctx.Unavailable as e:
            ctx.log.warning("task not shown: %s", e)
            task = None
        progress = task.update if task else (lambda fraction, text: None)
        try:
            GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(dir=GLOBAL_DIR, prefix=".helixsr-build."))
            _helixsr_run(work, progress)
            with _build:
                if _stop.is_set():  # the module was unloaded meanwhile: leave things as they are
                    raise files.Error("Build stopped")
                helixsr.store(work / "out", _helixsr_dir(), ctx.archive.swap_dir)
        except files.Error as e:
            _helix_error = str(e)
        except OSError as e:
            _helix_error = f"Build failed ({e.strerror or e})"
        except Exception as e:  # never die silently: the UI shows it
            ctx.log.exception("HelixSR build failed")
            _helix_error = f"Build failed ({e})"
        if _helix_error:
            ctx.log.warning("helixsr: %s", _helix_error)
            if task:
                task.fail(_helix_error)
            return
        ctx.log.info("helixsr: built into %s", _helixsr_dir())
        if task:
            task.done("Built")
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        _helix.release()


def _helixsr_run(work, progress):
    """Run the script in work (its result in work/out); a files.Error says why it failed."""
    global _helix_proc
    # Its temporary folder goes in work too: on disk, not in a /tmp that may be RAM, and removed with work
    # even when the script is killed (its own cleanup doesn't run then).
    (work / "tmp").mkdir()
    env = dict(os.environ, TMPDIR=str(work / "tmp"))
    # No shell; --yes accepts the DLSS license prompt (the UI's confirmation is the user's).
    _helix_proc = proc = subprocess.Popen(
        ["bash", str(HELIXSR_SCRIPT), "--yes", "--out", str(work / "out")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        env=env, start_new_session=True,
    )
    if _stop.is_set():  # teardown came before there was a script to stop
        _kill_build()
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        _kill_build()

    timer = threading.Timer(HELIXSR_TIMEOUT, expire)
    timer.start()
    tail = collections.deque(maxlen=40)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            tail.append(line)
            stage = _helixsr_stage(line)
            if stage:
                progress(*stage)
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            _kill_build()
            proc.wait()
        proc.stdout.close()
        _helix_proc = None
    if _stop.is_set():
        raise files.Error("Build stopped")
    if timed_out.is_set():
        raise files.Error("The HelixSR build timed out")
    if proc.returncode != 0:
        ctx.log.error("%s exited %s: %s", HELIXSR_SCRIPT, proc.returncode, "\n".join(tail))
        error = next((l for l in reversed(tail) if l.startswith(DIE)), tail[-1] if tail else "")
        raise files.Error(f"Build failed: {error.removeprefix(DIE)}")


def _kill_build():
    """Stop the build's script and everything it started (its own session)."""
    proc = _helix_proc
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def teardown():
    """Stop a download (its next block, or its unpacking) or a HelixSR build, and wait for them: once the
    module is unloaded (or uninstalled with its data) nothing may write to GLOBAL_DIR anymore."""
    _stop.set()
    _kill_build()
    deadline = time.monotonic() + TEARDOWN_WAIT
    for thread in (_thread, _helix_thread):
        if thread is not None:
            thread.join(max(0, deadline - time.monotonic()))


def uninstall(context, purge):
    """With purge, the downloaded nightly, HelixSR Scaler and the custom DLLs go too (the module's own data,
    and anything else left in GLOBAL_DIR)."""
    if purge:
        shutil.rmtree(GLOBAL_DIR, ignore_errors=True)


METHODS = {
    "load": load,
    "set": set_value,
    "game_install": game_install,
    "game_update": game_update,
    "game_uninstall": game_uninstall,
    "game_set_dll": game_set_dll,
    "game_apply_launch": game_apply_launch,
    "game_extra": game_extra,
    "custom_status": custom_status,
    "custom_set": custom_set,
    "custom_remove": custom_remove,
    "nightly_status": nightly_status,
    "nightly_download": nightly_download,
    "helixsr_status": helixsr_status,
    "helixsr_build": helixsr_build,
    "helixsr_check": helixsr_check,
}
