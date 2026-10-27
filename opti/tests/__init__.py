import os
import sys
from pathlib import Path

# Invasor's core (ctx.paths, ctx.vdf, ctx.archive): $INVASOR_CORE (set by tools/check_module.py), else
# the core this module sits in (modules/<id>), else a checkout next to this repository
# (<repo>/<id>/ -> ../invasor).
HERE = Path(__file__).resolve().parents[1]
_core = os.environ.get("INVASOR_CORE") or next(
    (str(p) for p in (HERE.parents[1], HERE.parents[1] / "invasor") if (p / "backend/invasor").is_dir()), "")
sys.path.insert(0, str(Path(_core) / "backend"))
