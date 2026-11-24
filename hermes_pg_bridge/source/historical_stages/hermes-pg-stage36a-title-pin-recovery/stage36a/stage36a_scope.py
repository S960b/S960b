#!/usr/bin/env python3
"""Stage 36A: in-memory correction of Stage 36 title pin; no code edits or DB writes.

A known ChatGPT create call used a title different from the pre-approved
literal in Stage36. This wrapper pins the original installed controller bytes,
request-id, DB task ID, and both titles. It reuses its existing stringent
checks and atomic permit-scope transition with only the expected title fixed.
"""
import hashlib
import importlib.util
import os
import pathlib
import pwd
import sys

CONTROLLER = pathlib.Path('/home/kali/hermes-pg-stage36-controlled-e2e/stage36.py')
EXPECTED_SHA = '7d488d187acdedc47dc4fc0f0faf8ab392acdbddd74a6b08c8909744787ef3a2'
OLD_TITLE = 'PGV2 Stage36 Independent Managed E2E Canary'
ACTUAL_TITLE = 'Hermes PG v2 Independent E2E Stage 36 (dispatch disabled)'
TASK_ID = 'task_b6c26aa18220412bafdbf32393f16568'
REQUEST_ID = 'pgv2-stage36-independent-e2e-20261123-v1'


def require(cond, marker):
    if not cond:
        print('STAGE36A_STOP_' + marker, file=sys.stderr, flush=True)
        raise SystemExit(2)


def main():
    require(len(sys.argv)==1, 'NO_ARGUMENTS_ALLOWED')
    require(pwd.getpwuid(os.geteuid()).pw_name=='kali' and os.environ.get('HOME')=='/home/kali', 'WRONG_USER')
    require(CONTROLLER.is_file() and not CONTROLLER.is_symlink(), 'ORIGINAL_CONTROLLER_MISSING')
    require(hashlib.sha256(CONTROLLER.read_bytes()).hexdigest()==EXPECTED_SHA, 'ORIGINAL_CONTROLLER_CHANGED')
    spec = importlib.util.spec_from_file_location('stage36_original_pinned', CONTROLLER)
    require(spec is not None and spec.loader is not None, 'CONTROLLER_IMPORT_FAILED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.TITLE==OLD_TITLE and module.REQUEST_ID==REQUEST_ID, 'ORIGINAL_CONSTANTS_CHANGED')
    require(module.MARK_SCOPE.name == 'scope.json' and module.MARK_CREATE.name == 'arm-create.json', 'ORIGINAL_STATE_CHANGED')
    print('STAGE36A_ORIGINAL_CONTROLLER_PINNED_TITLE_ONLY_CORRECTION', flush=True)
    module.TITLE = ACTUAL_TITLE
    # The original scope() verifies the actual DB title, both registry rows,
    # version/policy state=0, disabled dispatch, current permit and marker.
    # It then writes only the one-task permit, allow_create=false.
    try:
        module.scope(TASK_ID)
    except module.Stop as ex:
        print(str(ex),file=sys.stderr,flush=True)
        raise SystemExit(2)
    print('STAGE36A_SCOPED_CREATED_TASK_' + TASK_ID,flush=True)
    print('STAGE36A_ALLOW_CREATE_FALSE_DISPATCH_NOT_CHANGED',flush=True)


if __name__=='__main__':
    main()
