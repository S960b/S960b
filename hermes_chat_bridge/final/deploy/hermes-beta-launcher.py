"""Fixed owner-selected launcher. Default is hash/AST CHECK, not service start."""
import argparse
from pathlib import Path
import sys


SOURCE = '/home/kali/agent-dialog-beta/bridge-candidate.py'
CONTEXT = '/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py'
STATE = '/home/kali/agent-dialog-beta/state'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--validate-state', action='store_true')
    args = parser.parse_args(argv)
    previous = list(sys.path)
    try:
        # Add this reviewed package without replacing any configured PYTHONPATH
        # or the original interpreter/CWD/environment. No credential inputs.
        sys.path.insert(0, str(Path(__file__).absolute().parent.parent / 'src'))
        from git_dialog.bridge_bootstrap import main as bootstrap
        options = ['--source', SOURCE, '--context-source', CONTEXT]
        if args.run or args.validate_state:
            options += ['--run' if args.run else '--validate-state',
                        '--state-dir', STATE, '--port', '8790']
        return bootstrap(options)
    finally:
        sys.path[:] = previous


if __name__ == '__main__':
    raise SystemExit(main())
