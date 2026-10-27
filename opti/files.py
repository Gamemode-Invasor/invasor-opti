"""What the module's file handling shares: the error the user reads, and changes made whole or not at all."""
import os
import shutil
from pathlib import Path


class Error(Exception):
    """Something the user should read: the message says what went wrong."""


def copy_file(src, dest):
    """src over dest, whole or not at all: a temporary file next to dest, then a rename."""
    dest = Path(dest)
    tmp = dest.with_name(f".{dest.name}.new")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
