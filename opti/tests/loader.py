import hashlib
import importlib
import importlib.util
import itertools
import logging
import os
import shutil
import struct
import tempfile
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
_count = itertools.count()


def load_backend():
    """backend.py as the core loads it: a package of its own, with a name nobody else has (so
    each call is a fresh copy of every file, and its files import each other consistently)."""
    name = f"opti_under_test_{next(_count)}"
    spec = importlib.util.spec_from_file_location(name, HERE / "backend.py", submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def submodule(backend, name):
    """One of the module's files, as loaded with that backend."""
    return importlib.import_module(f"{backend.__name__}.{name}")



from invasor import archive, paths, vdf  # noqa: E402  (the core, found by tests/__init__.py)
from invasor.schema import InvalidArgument, Unavailable  # noqa: E402

paths.STEAM_ROOT = str(HERE / "no-steam-here")  # never the real Steam: fake_steam points it at a test's own
paths.MEDIA_GLOBS = ()
swap_dir = archive.swap_dir  # what the backend passes to install.install and custom.store

_log = logging.getLogger("opti-test")
_log.propagate = False  # what the backend logs isn't the tests' output
_log.addHandler(logging.NullHandler())


class FakeHttp:
    """ctx.http: get_json answers from `answers` (url: JSON, or an exception to raise), download writes
    `files[url]` and checks it against the checksum as the core does. `asked` lists the URLs."""

    def __init__(self, answers=None, files=None):
        self.answers, self.files, self.asked, self.downloads = answers or {}, files or {}, [], []

    def get_json(self, url, max_bytes=None):
        self.asked.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def download(self, url, dest, checksum=None, user_chose=False, max_bytes=None, task=None):
        self.asked.append(url)
        self.downloads.append({"url": url, "checksum": checksum, "max_bytes": max_bytes, "task": task})
        data = self.files[url]
        if checksum and hashlib.new(checksum[0], data).hexdigest() != checksum[1]:
            raise InvalidArgument("the file doesn't match its checksum")
        Path(dest).write_bytes(data)
        return {"path": str(dest), "size": len(data)}


class FakeSteam:
    """ctx.steam: no launch options call unless a test sets `options` ({appid: line}); set_launch_options
    records what it got."""

    def __init__(self):
        self.options, self.can_set, self.set = None, False, []

    def launch_options(self, appid):
        if self.options is None:
            raise Unavailable("no Steam in the tests")
        return self.options.get(appid, "")

    def supports(self, name):
        return name == "set_launch_options" and self.can_set

    def set_launch_options(self, appid, line):
        self.set.append((appid, line))
        if self.options is not None:
            self.options[appid] = line
        return {"applied": True, "verified": True}


class FakeCtx:
    """What the backend uses of the core's ctx in these tests: the core's own paths, vdf and archive."""
    InvalidArgument = InvalidArgument
    Unavailable = Unavailable
    log = _log
    game = types.SimpleNamespace(selected=None, running=None)
    paths = paths
    vdf = vdf
    archive = archive

    def __init__(self):
        self.http = FakeHttp()
        self.steam = FakeSteam()

    def task(self, label):
        raise Unavailable("no Tasks panel in the tests")  # the backend's plan B


def fake_steam(steam_root):
    """Make the core's ctx.paths find Steam (prefixes, shortcuts, accounts) only in steam_root."""
    paths.STEAM_ROOT = str(steam_root)


def fake_game(test, appid="42"):
    """A fresh backend (its GLOBAL_DIR in a tmp removed after the test) and a fake Steam with a Proton prefix for
    appid: (backend, tmp, the game's umu folder, not made yet)."""
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, tmp)
    steam = tmp / "Steam"
    prefix = steam / "steamapps/compatdata" / appid / "pfx/drive_c"
    prefix.mkdir(parents=True)
    b = load_backend()
    b.setup(FakeCtx())
    b.GLOBAL_DIR = tmp / "invasor-opti"
    fake_steam(steam)
    return b, tmp, prefix / "windows/system32/umu"


def tree(folder):
    """{relative path: link target, "dir" or file bytes}."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        rel = str(p.relative_to(folder))
        out[rel] = ("-> " + os.readlink(p)) if p.is_symlink() else "dir" if p.is_dir() else p.read_bytes()
    return out


def fake_dll(product="AMD", version=(2, 4, 0, 4370), machine=0x8664, dll=True):
    """The few bytes pe.info reads: MZ header, PE signature and machine, a version resource."""
    head = bytearray(b"MZ" + b"\0" * 0x3E)
    struct.pack_into("<I", head, 0x3C, 0x40)
    # COFF header: machine, then characteristics (IMAGE_FILE_DLL unless it's an EXE) at its byte 18.
    head += b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 16 + struct.pack("<H", 0x2000 if dll else 0x0002)
    res = b""
    if version:
        a, b, c, d = version
        res += struct.pack("<IIII", 0xFEEF04BD, 0x10000, (a << 16) | b, (c << 16) | d)
    if product:
        key = "ProductName".encode("utf-16-le") + b"\0\0"
        pad = -(len(head) + len(res) + len(key)) % 4
        res += key + b"\0" * pad + product.encode("utf-16-le") + b"\0\0"
    return bytes(head + res)
