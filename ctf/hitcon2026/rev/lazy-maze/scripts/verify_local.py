#!/usr/bin/env python3
"""Reproduce exploit locally in a Python 3.13 import-semantic emulator.

The original task uses Python 3.15's lazy imports. This verifier rewrites only
import behavior in a disposable copy of the original release modules, then
executes the original room/effect/exit code for all 1356 moves. This is a
strong regression check but not a substitute for testing on remote Python 3.15.
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT=pathlib.Path(__file__).resolve().parent

def main():
    with tempfile.TemporaryDirectory(prefix='lazy_maze_verify_') as tmp:
        tmp=pathlib.Path(tmp)
        release_zip = ROOT / 'lazy-maze-release.zip'
        if not release_zip.is_file():
            release_zip = ROOT.parent / 'original' / 'lazy-maze-release.zip'
        with zipfile.ZipFile(release_zip) as z:
            z.extractall(tmp)
        src=tmp/'release'
        meta=json.loads((ROOT/'map.json').read_text())['meta']
        effect_names={v['effect'] for v in meta.values() if 'effect' in v}
        for file in (src/'m').glob('x_*.py'):
            raw=file.read_text()
            def repl(m):
                name=m.group(1)
                if name not in effect_names:return m.group(0)
                return f'import _lazy_shim\n_lazy_shim.maybe_import("m.{name}")'
            patched=re.sub(r'^import m\.(x_[a-f0-9]+)$',repl,raw,flags=re.M)
            if patched!=raw:file.write_text(patched)
        init=src/'m'/'__init__.py'
        init.write_text('\n'.join(line for line in init.read_text().splitlines() if not line.startswith('from . import x_'))+'\n')
        game=src/'game.py'
        game.write_text(game.read_text().replace('sys.set_lazy_imports("all")','pass  # emulated by _lazy_shim').replace('room = getattr(ROOMS, module.rsplit(".", 1)[1])','room = importlib.import_module(module)'))
        (src/'_lazy_shim.py').write_text('''import sys, importlib
_filter=lambda importer,imported,fromlist:True
def install():
    sys.set_lazy_imports_filter=set_filter
    sys.get_lazy_imports_filter=get_filter
def set_filter(fn):
    global _filter
    _filter=fn
def get_filter():
    return _filter
def maybe_import(mod):
    if not _filter(None,mod,()):
        importlib.import_module(mod)
''')
        runner=src/'runner.py';runner.write_text('import _lazy_shim\n_lazy_shim.install()\n'+runner.read_text())
        route=json.loads((ROOT/'route.json').read_text())
        data=''.join(d+((' '+ch) if ch else '')+'\n' for d,pos,ch in route)
        data+='!import maze_state;print("SCRIPT_CHECK",repr(maze_state.state.script),flush=True)\nquit\n'
        # BdbQuit from the final 'quit' is expected. The exploit is successful
        # when the PDB prompt and exact constructed code are both observed.
        p=subprocess.run([sys.executable,'-u',str(runner)],cwd=src,input=data,encoding='utf8',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
        log=p.stdout
        target=(ROOT/'expected_code.txt').read_text().strip()
        matches=re.findall(r'^\(Pdb\) SCRIPT_CHECK (.+)$',log,re.M)
        import ast
        actual=ast.literal_eval(matches[0]) if matches else None
        valid=(len(route)==1356 and 'Blocked' not in log and 'Invalid choice' not in log and '(Pdb)' in log and actual==target)
        print('Steps:',len(route),'PDB:', '(Pdb)' in log,'Blocked:',log.count('Blocked'))
        print('Script:',repr(actual));print('Expected:',repr(target))
        print('VERIFIED_LOCAL_PDB' if valid else 'LOCAL_VALIDATION_FAILED')
        if not valid:print(log[-4000:])
        return 0 if valid else 1

if __name__=='__main__':sys.exit(main())
