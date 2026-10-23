"""OptiScaler: OptiScaler.ini files from Invasor's panel, and OptiScaler itself in games.

Two files: the selected game's, in its Proton prefix, in the folder Wine's OptiScaler hack loads
it from (steamlibs.UMU); and the module's own Global one (GLOBAL_DIR/OptiScaler.ini), the one a game
gets when OptiScaler is installed in it. The Global one starts as a copy of
assets/OptiScaler.invasor.ini and can be reset to that or to OptiScaler's own
(assets/OptiScaler.optiscaler.ini).

Next to the Global file, NIGHTLY_NAME holds the latest OptiScaler nightly, downloaded on request
in a thread of its own (see nightly.py), and CUSTOM_NAME the DLLs the user brings (see custom.py).
install.py puts them in a game.

A file is the only source of truth: every call reads it again, and a change rewrites just the
line of that key (comments, order and line ends stay as they are), atomically, and only if no
other program saved the file since the UI read it.
"""
import os
import shutil
import tempfile
import threading
from pathlib import Path

from . import custom, files, ini, install, nightly, steamlibs
from .browse import listdir

ASSETS = Path(__file__).resolve().parent / "assets"
# What the Global file can be reset to: OptiScaler's own file and the one this module ships.
DEFAULTS = {"optiscaler": ASSETS / "OptiScaler.optiscaler.ini", "invasor": ASSETS / "OptiScaler.invasor.ini"}
# Outside the module's folder, which an update replaces.
GLOBAL_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "invasor-opti"
NIGHTLY_NAME = "optiscaler"
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


def _ini_path(appid):
    """The game's OptiScaler.ini (it may not exist), or None if the game has no Proton prefix."""
    umu = steamlibs.umu_dir(_appid(appid))
    return umu / install.INI if umu else None


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


def _set_in_file(path, section, key, value, mtime):
    """Write one value. A select's value must be one of its options (what load offered); returns
    {"value", "mtime"}."""
    with _write:
        text, encoding, current = _read(path)
        if current != str(mtime):
            raise ctx.Unavailable(f"{install.INI} was changed by another program: reload and try again")
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
        new_mtime = _save(path, new_text.encode(encoding), current)
    ctx.log.info("%s: [%s] %s = %s", path, section, key, value)
    return {"value": value, "mtime": new_mtime}


# ---------- The selected game ----------

def load(appid):
    """The game's file, as _load_file, or state "no_prefix" if the game has no Proton prefix; plus
    "install" (install.state of its umu folder with the custom slots it has on as "extras"; None without
    a prefix), "build" (the nightly Global has downloaded, as nightly.read_marker, or None) and "custom"
    ({slot: kept in Global})."""
    path = _ini_path(appid)
    if path is None:
        r = {"state": "no_prefix", "path": None, "mtime": None, "sections": [], "install": None}
    else:
        r = _load_file(path)
        r["install"] = {**install.state(path.parent), "extras": custom.game_state(path.parent)}
    r["build"] = nightly.read_marker(_nightly_dir())
    r["custom"] = {slot: kept is not None for slot, kept in custom.status(_custom_dir()).items()}
    return r


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


def game_install(appid, dll):
    """Install the nightly Global has downloaded into the game, loaded as <dll>.dll."""
    build = _nightly_dir()
    global_ini = _global_path()  # before the write lock: making it takes the lock too
    with _build:
        if nightly.read_marker(build) is None:
            raise ctx.Unavailable("There is no OptiScaler to install: download it in Global first")
        return _in_game(appid, f"install OptiScaler as {dll}.dll", lambda umu: install.install(build, umu, global_ini, dll))


def game_update(appid):
    """Bring the game's OptiScaler to the nightly Global has downloaded (see install.update)."""
    build = _nightly_dir()
    with _build:
        if nightly.read_marker(build) is None:
            raise ctx.Unavailable("There is no OptiScaler to install: download it in Global first")
        return _in_game(appid, "update OptiScaler", lambda umu: install.update(build, umu))


def game_uninstall(appid):
    return _in_game(appid, "uninstall OptiScaler", install.uninstall)


def game_set_dll(appid, dll):
    return _in_game(appid, f"load OptiScaler as {dll}.dll", lambda umu: install.set_dll(umu, dll))


def game_extra(appid, slot, on):
    """Turn a custom slot on or off for the game. A slot with an "ini" entry also sets that key (on),
    or puts it back to auto (off, only when it still has the slot's value: one of the user's own stays)."""
    if slot not in custom.SLOTS:
        raise ctx.InvalidArgument(f"slot must be one of {', '.join(custom.SLOTS)}")
    if not isinstance(on, bool):
        raise ctx.InvalidArgument("on must be true or false")
    ini_path = _game_umu(appid) / install.INI
    entry = custom.SLOTS[slot].get("ini")
    if entry and on and (not ini_path.is_file() or _find_key(_read(ini_path)[0], *entry[:2]) is None):
        # Checked before copying anything.
        raise ctx.Unavailable(f"the {install.INI} of this game has no [{entry[0]}] {entry[1]}")
    if on:
        r = _in_game(appid, f"turn {slot} on", lambda umu: custom.enable(_custom_dir(), umu, slot))
    else:
        r = _in_game(appid, f"turn {slot} off", lambda umu: custom.disable(umu, slot))
    if entry and ini_path.is_file():
        section, key, value = entry
        text, _, mtime = _read(ini_path)
        found = _find_key(text, section, key)
        if on:
            _set_in_file(ini_path, section, key, value, mtime)
        elif found and found["value"].lower() == value.lower():
            _set_in_file(ini_path, section, key, ini.AUTO, mtime)
        r = load(appid)
    return r


# ---------- Global ----------

def _global_path():
    """The Global file, made from assets/OptiScaler.invasor.ini the first time."""
    path = GLOBAL_DIR / install.INI
    if not path.exists():
        with _write:
            if not path.exists():
                _guarded("make the Global file", lambda: _reset_to(path, "invasor"), ctx.Unavailable)
    return path


def _reset_to(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(path, DEFAULTS[source].read_bytes())
    ctx.log.info("%s: from %s", path, DEFAULTS[source].name)


def global_load():
    return _load_file(_global_path())


def global_set(section, key, value, mtime):
    return _set_in_file(_global_path(), section, key, value, mtime)


def global_reset(source):
    """Replace the Global file with one of DEFAULTS ("optiscaler" or "invasor"); returns global_load()."""
    if source not in DEFAULTS:
        raise ctx.InvalidArgument("source must be optiscaler or invasor")
    path = _global_path()
    with _write:
        _guarded("reset the Global file", lambda: _reset_to(path, source), ctx.Unavailable)
    return global_load()


def _custom_dir():
    return GLOBAL_DIR / CUSTOM_NAME


def browse(path=None):
    """One folder of the file browser (its folders and .dll files); without a valid path, home."""
    if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isdir(path):
        path = str(Path.home())
    return listdir(path)


def custom_status():
    """{slot: [{"name", "product", "version"}] or None}, see custom.status."""
    return custom.status(_custom_dir())


def custom_set(slot, path):
    """Keep a copy of a slot's DLLs, from the file the user picked; returns custom_status()."""
    with _write:
        _guarded("copy it", lambda: custom.store(_custom_dir(), slot, path), ctx.InvalidArgument)
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
        progress = task.update if task else (lambda fraction, text: None)
        try:
            release, downloaded = nightly.update(_nightly_dir(), progress=progress, stop=_stop, swap_lock=_build)
        except files.Error as e:
            _last_error = str(e)
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


def teardown():
    """Stop a download (its next block, or its unpacking) and wait for it: once the module is unloaded (or
    uninstalled with its data) nothing may write to GLOBAL_DIR anymore."""
    _stop.set()
    if _thread is not None:
        _thread.join(TEARDOWN_WAIT)


def uninstall(context, purge):
    """With purge, the Global file, the downloaded nightly and the custom DLLs go too (the module's own data)."""
    if purge:
        shutil.rmtree(GLOBAL_DIR, ignore_errors=True)


METHODS = {
    "load": load,
    "set": set_value,
    "game_install": game_install,
    "game_update": game_update,
    "game_uninstall": game_uninstall,
    "game_set_dll": game_set_dll,
    "game_extra": game_extra,
    "global_load": global_load,
    "global_set": global_set,
    "global_reset": global_reset,
    "browse": browse,
    "custom_status": custom_status,
    "custom_set": custom_set,
    "custom_remove": custom_remove,
    "nightly_status": nightly_status,
    "nightly_download": nightly_download,
}
