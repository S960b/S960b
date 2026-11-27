#!/usr/bin/env python3
"""Generate DejaHeap VM runtime-MAC forged OPEN (op=1) test, no network calls."""
import argparse
from pathlib import Path
from dh_runtime_mac_vm import E, REGS, MASK, SHIFT32, BYTE, IDX, GET


def generate(kind=8, output='dh_open_runtime_diag.vm'):
    if not 0 <= kind < 2**31:
        raise ValueError('kind must be a nonnegative 31-bit value')
    e=E()
    e.lines += ['.func main',
        ' const r10 65',' putc r10',' nl', # A
        ' const r3 1',' hopen r6 r3',
        ' const r10 66',' putc r10',' nl', # B
        ' call setup r12',
        ' const r10 67',' putc r10',' nl', # C
        ' hflush r6',
        ' const r10 68',' putc r10',' nl', # D
        ' const r1 8',' buf r30 r1',' mov r31 r30',
        ' const r32 168',' ringpeek r30 r32',
        ' const r10 70',' putc r10',' nl'  # F: print bytes status u32 fd u32
    ]
    for i in range(8):
        e.lines += [f' const r33 {i}', f' getb r34 r31 r33', ' print r34', ' nl']
    e.lines += [
        ' const r10 82',' putc r10',' nl', # R: attempting legitimate hread
        ' const r1 128',' buf r40 r1',' mov r41 r40',
        ' hread r6 r41',
        ' const r10 83',' putc r10',' nl', # S: hread succeeded
        ' print r41',' nl',
        ' exit','.end',
        '.func setup',
        ' const r1 16',' buf r0 r1',' const r0 0',
        ' const r1 16',' buf r5 r1',' hexport r5 worker',' ret r0','.end',
        '.func worker',
        ' const r10 87',' putc r10',' nl', # W
        ' gc',' gc',
        ' const r10 71',' putc r10',' nl', # G
        ' const r1 256',' buf r7 r1'
    ]
    e.const(MASK, 1); e.const(SHIFT32, 32); e.op('shl',MASK,MASK,SHIFT32)
    e.const(90,1); e.op('sub',MASK,MASK,90)
    e.const(BYTE,255)
    e.leak_key()
    for name,val in [
        ('b',0xb7b78592fee2c0c7),('v1',0xc912cca87c519df1),
        ('v2',0xa7bd9391e9f2cad9),('v3',0xd604c6a862478ae5)]:
        e.init64(name,val)
    e.init64('m',(kind << 32)|1);e.sip2block()
    e.init64('m',0);e.sip2block()
    e.copy('m','key');e.sip2block()
    e.init64('m',0);e.sip2block()
    e.finish()
    for off,v in [(0,1),(4,kind)]:
        e.const(IDX,0x70+off);e.const(GET,v)
        e.emit(f'bset r7 r{IDX} r{GET}')
    for off,src in [(8,'a'),(24,'key')]:
        for i in range(8):
            e.export_byte(off+i,REGS[src][i//4],8*(i%4))
    e.lines += [' const r10 77',' putc r10',' nl', # M
                ' ret r1','.end']
    Path(output).write_text('\n'.join(e.lines)+'\n')
    print(f'Wrote {output}, {len(e.lines)} lines; forged op=1 kind={kind} counter=0')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--kind',type=lambda x:int(x,0),default=8)
    p.add_argument('--output',default='dh_open_runtime_diag.vm')
    a=p.parse_args()
    generate(a.kind,a.output)
