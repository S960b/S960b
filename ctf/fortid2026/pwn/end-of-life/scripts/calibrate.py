#!/usr/bin/env python3
# Calibrate CHALL.EXE: patch the argument push of fprintf so the program
# prints a live register (BP or SS) into OUTPUT.TXT instead of the string
# length. Run each patched copy in DOSBox with INPUT.TXT containing
# "hello"; the value printed is a signed decimal - convert with & 0xFFFF.
#
# The patch point: process_input ends with fprintf(out, "%d\n", strlen(name));
# the strlen result is pushed with `push ax` (0x50) at file offset 512+0x32F.
# Replacing 0x50 with 0x55 (`push bp`) or 0x16 (`push ss`) makes the same
# fprintf print that register. Calibration patches never run on the server.
#
# Usage:
#   python3 calibrate.py CHALL.EXE outdir
import struct
import sys


def main():
    if len(sys.argv) != 3:
        sys.exit('usage: calibrate.py CHALL.EXE outdir')
    src_path, outdir = sys.argv[1], sys.argv[2]
    src = bytearray(open(src_path, 'rb').read())
    if src[:2] != b'MZ':
        sys.exit('not an MZ executable')
    patch_off = 512 + 0x32f
    if src[patch_off] != 0x50:
        sys.exit('unexpected byte at 512+0x32f: %#x (push ax expected)' % src[patch_off])

    import os
    os.makedirs(outdir, exist_ok=True)
    for name, op in (('BP', 0x55), ('SS', 0x16)):
        clone = bytearray(src)
        clone[patch_off] = op
        path = os.path.join(outdir, 'CHALL_%s.EXE' % name)
        open(path, 'wb').write(clone)
        print('wrote', path)

    print()
    print('Run each as CHALL.EXE (INPUT.TXT = "hello"), then read OUTPUT.TXT:')
    print('  CHALL_BP.EXE prints BP as signed int (convert & 0xFFFF)')
    print('  CHALL_SS.EXE prints SS as signed int (convert & 0xFFFF)')


if __name__ == '__main__':
    main()