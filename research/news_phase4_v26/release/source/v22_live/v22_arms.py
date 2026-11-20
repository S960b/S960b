"""Compatibility shim — the canonical implementation lives in source/v22_arms.py.

Stage-18 P0-A: this file used to be an independent copy, and it shadowed the
canonical module for a fresh clone (the published tree carried both, and Python
picked this one). Rather than maintain two files that can drift, this module now
re-exports the canonical implementation object-for-object, so `import v22_arms`
and `import v22_live.v22_arms` provably yield the SAME functions, not look-alikes.

Editing this file instead of source/v22_arms.py has no effect, which is the
point: there is exactly one implementation.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_CANDIDATES = (_HERE.parents[2] / "source" / "v22_arms.py",  # published layout
               _HERE.parents[2] / "v22_arms.py",             # repo-root layout
               _HERE.parent / "v22_arms.py")                 # flat live tree
# Reuse the canonical module if the interpreter already loaded it, so the shim
# and a plain `import v22_arms` hand back the SAME function objects. Loading a
# second copy would pass an equality check and fail an identity check, which is
# exactly the drift this shim exists to prevent.
_mod = sys.modules.get("v22_arms")
if _mod is None or Path(getattr(_mod, "__file__", "")).resolve() == _HERE:
    for _cand in _CANDIDATES:
        if _cand.is_file() and _cand.resolve() != _HERE:
            _spec = importlib.util.spec_from_file_location("v22_arms", _cand)
            _mod = importlib.util.module_from_spec(_spec)
            sys.modules["v22_arms"] = _mod
            _spec.loader.exec_module(_mod)
            break
    else:                                             # pragma: no cover
        raise ImportError("canonical v22_arms.py not found: " + str(_CANDIDATES))

CANONICAL_PATH = str(_mod.__file__)

for _name in dir(_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_mod, _name)


