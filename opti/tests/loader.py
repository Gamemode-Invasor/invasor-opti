import importlib
import importlib.util
import itertools
import logging
import sys
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



class InvalidArgument(ValueError):
    pass


class Unavailable(RuntimeError):
    pass


_log = logging.getLogger("opti-test")
_log.propagate = False  # what the backend logs isn't the tests' output
_log.addHandler(logging.NullHandler())


class FakeCtx:
    """What the backend uses of the core's ctx in these tests."""
    InvalidArgument = InvalidArgument
    Unavailable = Unavailable
    log = _log

    def task(self, label):
        raise Unavailable("no Tasks panel in the tests")  # the backend's plan B


def fake_steam(backend, steam_root):
    """Make the backend find prefixes only under steam_root (a Steam folder made by the test)."""
    libs = submodule(backend, "steamlibs")
    find = libs.umu_dir
    libs.umu_dir = lambda appid: find(appid, steam_root, ())
