"""Pinned import of verified Stage 33C actor adapter. No caller-selected role."""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path

ACTOR_FILE = Path('/home/kali/hermes-pg-stage33c-shadow-tunnel/actor_adapter.py')
ACTOR_SHA256 = 'a17910521c2dd6b95ced3ca3ff45ac4e3a4277631050d1f2e887195b2a8af509'


def actor_classes():
    if (ACTOR_FILE.is_symlink() or not ACTOR_FILE.is_file() or
        hashlib.sha256(ACTOR_FILE.read_bytes()).hexdigest() != ACTOR_SHA256):
        raise RuntimeError('PINNED_STAGE33C_ADAPTER_MISMATCH')
    spec = importlib.util.spec_from_file_location('stage33e_actor_adapter', str(ACTOR_FILE))
    if spec is None or spec.loader is None:
        raise RuntimeError('ADAPTER_IMPORT_UNAVAILABLE')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ChatGPTActorDB, module.AgentActorDB
