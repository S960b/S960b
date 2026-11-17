"""Compatibility shim — the canonical implementation lives in source/v22_ledger.py.

See the note in the sibling v22_arms.py: one implementation, two import paths,
and this module proves it by re-exporting the canonical module's objects.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_CANDIDATES = (_HERE.parents[2] / "source" / "v22_ledger.py",
               _HERE.parents[2] / "v22_ledger.py",
               _HERE.parent / "v22_ledger.py")
# Reuse the canonical module if the interpreter already loaded it, so the shim
# and a plain `import v22_ledger` hand back the SAME function objects. Loading a
# second copy would pass an equality check and fail an identity check, which is
# exactly the drift this shim exists to prevent.
_mod = sys.modules.get("v22_ledger")
if _mod is None or Path(getattr(_mod, "__file__", "")).resolve() == _HERE:
    for _cand in _CANDIDATES:
        if _cand.is_file() and _cand.resolve() != _HERE:
            _spec = importlib.util.spec_from_file_location("v22_ledger", _cand)
            _mod = importlib.util.module_from_spec(_spec)
            sys.modules["v22_ledger"] = _mod
            _spec.loader.exec_module(_mod)
            break
    else:                                             # pragma: no cover
        raise ImportError("canonical v22_ledger.py not found: " + str(_CANDIDATES))

CANONICAL_PATH = str(_mod.__file__)

for _name in dir(_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_mod, _name)


