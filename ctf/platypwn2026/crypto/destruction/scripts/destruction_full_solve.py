#!/usr/bin/env python3
"""DEStruction CTF complete solution for both FLAG0 and FLAG1.

Run: python3 destruction_full_solve.py HOST PORT

Requires only Python 3 standard library. Makes one 1-MB-AD encryption request,
then recovers GHASH key H with at most 96 authentication trials (usually 72),
then retrieves FLAG1 by requesting encryption of a plaintext containing H.

Attack: 32 chosen associated-data forgeries that keep the first 14 GCM tag
bits unchanged. The two remaining tag bits each reveal two independent binary
linear equations in H. A local 64x64 GF(2) inversion recovers the complete H.

The 32 coefficient sets below are a compressed encoding of the solutions to
GF(2) linear systems for the actual GHASH reduction polynomial in the task.
"""
import argparse
import base64
import re
import socket
import zlib

AD_REQUEST_LENGTH=1<<20
PLAINTEXT=b'ABCDEFGHflag'
TARGET=b'give me flag'
VARIANTS_B85 = (
    'c-jE~5&!OEuXMvZ-LhXuuYqqLCp@Nz5QGUf|Lyq&z|QZH4Fx;iyepX>EeUcxW3NRf4Kt6RWRHtX1FyMSpZ~$GP9av+WDkU;+j7SZ'
    'l&^o-H8Cq|_sOSakTh?kuDrAKBPc3&vI0@aUI&$&MrCUMBR^5i2Q1jWZ=Yfvs*+ufoMckl^pf3ClF*S>H2CZq21%W4OzD3m(o63H'
    'eBbK1^KaC+H6+ydaNq<2CTU$vK%-ndPjeTH?zF}m&&|~=9Xb%4D2P5UM9Wm(DV-*up{%=X5i~+*^@Zz<63?A`s}#L5dTsgmh*V+7'
    'H+c^eR#sDJ^@d~>UzldBObNn*2Iu1`r9t$+P}c`ds3rGF$Gsb^hmprmaa#!fY7o{dnS+3!zP%@G$hv!FM`)Il>N&&iyi-OBtXe}W'
    'f*4Jd`$B@*Y!LRd8K2&%43qi_ni=c3v2VgJ#FB^kQVYAuak*M&@Z2Hl!#r`t@VyU|V(pxc0XMIp%^wi1=A2z^P*uoPl&WhqFI@_4'
    'x;IajIJojbDqT?Aq*&)7Z7WDb-X%uY#~_mMV}(H5V&4P0*F)8*E58+h4D6_NbfUOFmWe<2K5^dxU@+45e@{aUi1~SXLEwq80pVkv'
    's0oCw&<I-7;GAfA<HfwG_X(AIswOhBPaY1b<P>ke&Tc^a-se(wl^UAM-?X$kUPwavv?%v<v5@`mc%D*PG~tYy|C{Sw$fwr^B-2=2'
    '>3lX4iO<F!bTFVfDDh222TUn+0#$RaA5)H;<Go!No1rhK_k6}f5v3%ZZ-a&Du2)$1#hK_1O%NGaJ^CWneeMcd%Sn=pvlXdvZ1@Is'
    ';osCtx3HEN$a*-6Ywc}#4s*(owK+jz=Hi0o!DW1wM;Z*3@B+(XT;<-^{C`n&g=FZBf9&FCF}dV*E0kbXyi}B+f-v`dCiA~LMiudg'
    '0J4=kfp^SuuhNY0|LrweN*LbOr^DiyGPS>Xvs%=W?{H0rgO>9Y`6r0<(6gw66fqyHj)(1LMp|;S$?RkjsM3;^8T$rm&#vZepwd!!'
    '!>MPpXA$dK;=~pgWrU4f@bYn=EvBKKqNEreU`|s|H%@uVIWEd6jygI7D9cv<D`ohi?QK*jb~PxnVW@LMz*Y-fTY1y2yp1u5`;6Qa'
    'v*!N*ldm{Q=kR4j)N3^pcCdL#?>soZx4NG*M5=;g%rv#*HR{x<l8i42YYWj8Q~6|N2Xm)62B*kiJ#o|w4(8o~gM&RL;n%uqC+NOd'
    '_2|QeKyqf2Y&nJT@%$=J)LHBb+0x&7oiCRttDj|cwJtQvL(tJYg$O6S6QoVL=bq~1=BC+swms*xwMpzbr~l}fq12@VXr!>gJa9|X'
    '^3%zfx$wxQ;PocbVqwG40{~NqX2dKV?`%d5mqSMh=cP@Dy}|d&iH7>~LuESrbds;|(S#xAHjmg%3$*Hi11txU)@Bg!q8j{V?emi<'
    '+*C2rMiKN>nLw_)#R^>6xHsqk=eQJaVivwx^KQKzWSS{6V1oh|NwUR{b1^ea--hs>BB-Xz)nZ+OW`w2^9nqM%2|^OrcWx4uCk;%%'
    '1xk}H)4X~FLIQfPAE{g1Fl?uZkX!bU9@)cmTI-~IU-@j_lOx1`>`za%rS5A_bOJ?14yq|8)*|h@b293391Hd6pc+yj1kY|>X-Yu%'
    'BJyha>3mNRTI+zcneO8~Thwl^s9`7LP~8MB?qP~9`JlHxt+ntvRlWCFwr?Ef(%=vVXQIkhApQ|^SB9H^hFFz&-$Ug6_RSPcR|-b6'
    '!*A(u)WEt!?Bg<gyrathzTp)!Q495nTH6LQf_-`W@GC<?6<<pA*;L&&gXb^*ke`p2ywk@%6?2xz|1VE{c?c7OBsjE^R;?qzglnbx'
    'IA`YNX6V5TJ!?QMEib%)Asyy}H34EP9Ld<%hOf*`A**na_h@(%7JBH}-0L@$Gr`8fOis*v-|`7<&6cg_`7uY2><g>$LKcf+6a%MT'
    'XIHlaWj+Ywu{mq}a8aKfG!e39=G1nW5pJ{~k=EZu-L<70t<a2TxGA;yixb4G=lb*OVg)tOx7>KX)h(z{C$fq5m*~B2#WsDGI1oz4'
    'vneaq0JFOaW<PswaY?RrC^LZ?ai=5jX$EQM>Y++*jG98p3#UI-{1)B|rX{Fezv&-Mdq2x>RarURc#85=%Crm}8S+k0K|XAoS~d=@'
    '%VIg7qdf}5-Q7H#%vh1i)*Ql25a~j{$MERy`lPtAD)l0ibV!RU@fRRF0l8bqW69mr`7v7C3B9q~tXa<%|6s*5-3ah=h9*caVa6k}'
    'k1Ot^cOhC_t^Lm`f0tlDcVQ;rXYV(5idX!JhwA$UD##EzI}tx<es#+m{AumCWvns@q0q=kTgZE%30Q)KWwhhzwmFQ9#KduZWM#xh'
    'xuspzy&h%^W{C=j9BpmnKn5Oo(=C-AT>#I=!u<M7uo1(9@02VFGqs2ZeDZ2}Vtm0Q)J7O%&S)K@LIo4JRfqhexnNL~*D^PGy%llU'
    'KkOpAUadb6npk|a6KFVE2vqmpNrfKX4-cZRM464Z#vf5;_E(@>rk;Y7DB9N3l+3rBHJgO4Eg`W3L@Aa1aR<C&qGmt9;C^c9wa42c'
    '0IXp4bD5O>DPJ*AUA4vb9CtGSsqx)BZcUz{rKQXq?rCuwYMKcUy7z3sVe{afe(41WDvmBJx1Cnw23*^lsgCcV2b;2vp<_yOaUU<G'
    '4F&D<SXKaZguzdD@Wa&ent8LMZ&ww`jQ^U72^25C#4Vu{9MUJ~?rkXvQ!?vdtF<iTZLRN40p=TDWjmDPvDP$onp{@b!Qd7C(>}fT'
    'E+Ig}1ik!M*Cct@q&|dt)z{q8r*=&5N->p|w7=SCuztpk-K&N0U-D8;wjzigW)6*sHw_x4-o$nTp=n^mX1IhiTtV$Mz8)y&$(n!b'
    '?qyF=26(?SY$~_wkSxhj$p|91V84`vS^VxHE?l^&YLuu?btRfjIXz=usA6roFvI4s5(PYJA?4^27gmS}Y}^ll`m{wPYn~B5o6W^c'
    '3FyZA-Uly^#P22FopP99$6Wh>j?4vJBkxv!O2l0f(C9yz1U31^8Tg#VaHahkt`-+~^xmHtdcxYP2bl_?s>SjXwyE$@zeO~y3EA>w'
    '`T*nd8ucot7WX1}PtRWxi9{FR_D3lU@b?-os0y+*)R+4qD~7b9-=8yQG165PaVX?L*?N3f>ji*SgMxC2;3aM_nDYFFgX2$ZMRCA3'
    'spqM_j(@6mDA<(QIm3Q-qYU`JGV*wIXln2mGH-k@{^LE8J?71q3NPs-*YbiBvq3XNM=PcV0$UXmEZuG}=WU+-U#%W9wtu!O4tQ|_'
    '!%m>=u`0@*Wopj`K^B@@*%|hBW=>q3pseGC2yIqyYLEW@Qe*a0K>qbPrBI9htJE<qq)n_%+MK|$KegSouH$>s-WwZPY<y^f)F&Ex'
    'sL{H|P=wW}?Q+!ql(JSw9Fa@ko6{IpNesROjMXTNv-f-OVbjNlA;d9B?(61eFEEYi2y<+De!&U{?l|xZS@h;9L!TuXT+VrIFf(4|'
    'kXMp3CC)nvyGLgoq=7+mC->)Yjf1YbckAkY-TOSAlemb_x9rRc+uf`l(5waPY%qa68!c_ehA%Zvc*}(Z`D5Yd$19@eTRoh)YAW4%'
    '+?e8&rW$@cjn)c__o5FIHv>ciA)@pdJ25&B&6*7>!O2^>y}dfw)MZcXjYt_mqZ22_=9fO^6UhslS3Wm;-RS?Dt4?w!8wSL)u(YzI'
    ')JG&ez+l1wz_-cgluX+n`{Oe48?nble1~>yoGub9pWa{%mSrrZI(ag#jd5aq1y!6W=hUT}QkaIeV*EUS?&EiXpX1PxdGzyuoUjhW'
    'Gn}Crj8uPT0SlKB*1JyoK-Rjl$lwwe0G*u@d0&QmdFzyU!niwLEp!J#U$gP^2pGqZyn2=vMW#8agAvTy4R%Y*U%)1Eu(*!8^5#f#'
    '*ApTA^l;SPJsA~Le4Ssw_R-dRq#F)Ct~two<=C(nh)n!Y%;)&Y1P4A=f~}q*RB%4WdOvY9zE!qY6)u%$j#79dQW}jML>lH4r?}Y!'
    'aN6#@jfJ>L$(I{2RE$I$U|x<!Y=4G*82S<hjZQYDzqUQgy+?mu{sd3yesi3`xFg5D?@7W!)RE>}Cnv>6Lx~TLZ886yd@DDc-j`g_'
    'rY0S&fRa>9Vc{0+j5$+OWr9i5Ou5v7Nk*5KW^rQ07zeN2VX{y7&n*&{C$M`ek{Pca&WBXQSXd<yt_@Q0C+$ha3rj@8*?+W_HmmGB'
    '<CR@VzqyTx#~xg4<pe9VUdu+mJmGXO%r*$b@cvUuMYN%?PCTWw?a?W;zR*A@jc7<CW1i0x@#E#Lkyi{Dc-$-$kxTkCWA(bq5NsKx'
    '@*Jxv$mY?(ZiypWqG^gERv^%4N8(ynSl)OZ^G}cbG446bO)ji3QJ}`dUi}fOL9cC}nsZ<IP~dwSm8g{Su*0zq&0K|y`O$PebPuOE'
    '^2V6-!FM2_r7)R;JGTn`5P6m>kujoCM~u=O$+~!;RWS?%n6-A}K3z$EDpUb{sG}tX4Jjg5H{%ot!S?tAxvwQ+q6?NxK3x&?i%wqj'
    'jmH^>5t3Tn2NHwpr`%_pus}?ey5QO&)HT=#JL>R=Oeht}#c`N%@P7XO>$_6qZp1)zYHg6eP`n?gm5kMHQd7)^sVLB-+%^xd@9h;}'
    '>FpiwoIFhhOX&*o_KC>nSYB3h;dpMS*DfB%DJMGtwC?de5f_Io=Kdn2Y=+eCZxFPOPo__z;w5iYMDkGFA}U^xR_q%Z)2%v_+emJF'
    '+_t?#FFxD#(;R2j#w7mZJ%h+MRY>Ov(_fw)c*~Oj1VmKElDagF1m|1QtD&6d47%vAS*eRaiog68w>gF@P*W-?`3;TG?2W-Ryem|H'
    '0fjx5@QUNt3VQPcys8`>Y!x<z4Pi=jwTPI{{;VCj7Gs>h$?~IQITiHn?g~$B_4jx_98YLAv@VyOL#BhX;Yf(-g)~>;?jZ6;6u8hY'
    'Ebdp3i!8yE>v)tP<V(W@;edU1$fpfH-r_U1)KTMDu-xj2PWqgoD%-6xp-`}yySQW>FA(ufzjff!4Os0gI>h*0hpIsbw`#PNZ=~Zb'
    '2FlhvQ734;7rW_5r&u)N5w=C8X!+MgSJ+4{q~_XS2A*G9ts*rKj79hamp|oMn<swZ=BFvhfJx%pQo9hce23RKa~{v}x`Vpj5fVCf'
    '@yR-y(`jO)Ur``oSj^ii(=HE7Pg-rEj1Iil%jMRiR`5Y&p_S#1LB5n6J_#DTNv_O8_zbSKVN0Cfo8RQYgLC%;F>;N_eIXp1N|q!('
    'hxpFni>swLbSuf+wLO4=O@|_$Ie>yOB(BC*@?2&4J7&M+8jCg=!Fe;Ci$@#whunYh)HMRDlWtAl`_xL{%D`7BcZP<ptV)xl_vGbQ'
    '^j=UI(a1016bNmr7f9gIW#Em^xv$4GW*V)?_9?xx<MHe#GS-6zP#t;`T2yh`^P>ybFCr$?nK-Ya2Ly;A8|hMFr;lkS%Zo)?*H8#!'
    '__CWhBG@!F|2WabRfPR_bk@v|ud+ELBERwk5VB|X=8M2ey7Z%tQkKn0i%;}3Jmr^p*wgPuOEAnV&NTAdA=sT*^=fF&g>4x!#ARm1'
    '?gwF*efi|O^m!FuXd|1tO%~4PgCOg5m;9+=u5=5)NQkK&QktCT1d<8'
 )

def load_variants():
    blob=zlib.decompress(base64.b85decode(VARIANTS_B85))
    assert len(blob)==32*136
    for i in range(32):
        part=blob[i*136:(i+1)*136]
        poly=int.from_bytes(part[:120],'little')
        row_hi=int.from_bytes(part[120:128],'little')
        row_lo=int.from_bytes(part[128:136],'little')
        yield poly, row_hi, row_lo


def decode_result(output, expected_ad=None):
    marker=b'Here is a result: '
    if marker not in output:
        raise RuntimeError('Encryption response not found: '+repr(output[:200]))
    line=output.split(marker,1)[1].split(b'\n',1)[0].strip()
    try:
        nonce,ct,ad,tag=[bytes.fromhex(x.decode()) for x in line.split(b',')]
    except (ValueError,UnicodeDecodeError) as exc:
        raise RuntimeError('Bad encryption response format') from exc
    if len(ct)!=12 or len(tag)!=2:
        raise RuntimeError('Unexpected ciphertext or tag size')
    if expected_ad is not None and len(ad)!=expected_ad:
        raise RuntimeError(f'Expected {expected_ad} bytes of AD, received {len(ad)}')
    return nonce,ct,ad,tag


def make_forgery(nonce, ct, ad, tag, poly, tag_difference):
    delta=bytes(x^y for x,y in zip(PLAINTEXT,TARGET))
    assert delta[8:]==b'\0'*4
    new_ct=bytes(x^y for x,y in zip(ct,delta))
    new_ad=bytearray(ad)
    m=(len(ad)+7)//8
    for i in range(15):
        e=1<<(i+3)
        off=(m+4-e)*8
        assert 0<=off and off+8<=len(ad)
        coef=(poly>>(i*64))&((1<<64)-1)
        kb=coef.to_bytes(8,'big')
        for j in range(8):
            new_ad[off+j]^=kb[j]
    tag2=tag[:-1]+bytes([tag[-1]^tag_difference])
    return b','.join((nonce.hex().encode(),new_ct.hex().encode(),
                      new_ad.hex().encode(),tag2.hex().encode()))


def solve_binary_system(rows,values):
    # rows: 64-bit GF2 linear functionals of H, one per observed bit.
    # values: corresponding measured 0/1 outputs.
    assert len(rows)==len(values)==64
    sysrows=[int(r)|(int(v)<<64) for r,v in zip(rows,values)]
    rr=0
    for bit in range(64):
        piv=next((i for i in range(rr,64) if sysrows[i]>>bit&1),None)
        if piv is None:
            raise RuntimeError('Dependent linear equations for H')
        sysrows[rr],sysrows[piv]=sysrows[piv],sysrows[rr]
        for i in range(64):
            if i!=rr and (sysrows[i]>>bit&1):
                sysrows[i]^=sysrows[rr]
        rr+=1
    H=0
    for i in range(64):
        H|=((sysrows[i]>>64)&1)<<i
    assert all((r&H).bit_count()%2==v for r,v in zip(rows,values))
    return H.to_bytes(8,'big')

class Connection:
    def __init__(self, host, port, timeout=120):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buffer = bytearray()
    def until(self, needle):
        if isinstance(needle, str): needle = needle.encode()
        while True:
            idx = self.buffer.find(needle)
            if idx >= 0:
                end = idx + len(needle)
                data = bytes(self.buffer[:end])
                del self.buffer[:end]
                return data
            chunk = self.sock.recv(1<<18)
            if not chunk:
                raise ConnectionError('Server closed connection before ' + repr(needle))
            self.buffer.extend(chunk)
    def line(self, data):
        self.sock.sendall(data + b'\n')
    def close(self):
        self.sock.close()



def request_encrypt(conn,plain,ad_req_len):
    conn.line(b'1')
    conn.until(b'Enter AD length: ')
    conn.line(str(ad_req_len).encode())
    conn.until(b'plaintext in hex: ')
    conn.line(plain.hex().encode())
    return conn.until(b'Enter your choice: ')


def submit(conn,payload):
    conn.line(b'2')
    conn.until(b'Submit your ciphertext in hex: ')
    conn.line(payload)
    return conn.until(b'Enter your choice: ')


def extract_flag(output):
    match=re.search(rb'Congratulations! Here is flag:\s*([a-fA-F0-9]+)',output)
    return bytes.fromhex(match.group(1).decode()) if match else None


def solve(host,port):
    variants=list(load_variants())
    conn=Connection(host,port,timeout=180)
    trials=0
    got_flag0=None
    try:
        conn.until(b'Enter your choice: ')
        print('[*] Getting one known-plaintext encryption with 1 MiB of AD.',flush=True)
        result=request_encrypt(conn,PLAINTEXT,AD_REQUEST_LENGTH)
        nonce,ct,ad,tag=decode_result(result,AD_REQUEST_LENGTH+4)
        print('[+] Transcript received. Recovering 64-bit GHASH secret.',flush=True)
        equations=[];values=[]
        for i,(poly,row_hi,row_lo) in enumerate(variants,1):
            # Desired tag difference is two bits. Test 0,1,2; if all are
            # rejected then 3 is forced (no fourth request needed).
            guess=3
            for delta_tag in range(3):
                payload=make_forgery(nonce,ct,ad,tag,poly,delta_tag)
                result=submit(conn,payload)
                trials+=1
                flag=extract_flag(result)
                if flag is not None:
                    guess=delta_tag
                    if got_flag0 is None:
                        got_flag0=flag
                        print(f'[+] FLAG0: {flag.decode("utf-8","replace")}',flush=True)
                    break
            equations.extend([row_hi,row_lo])
            values.extend([(guess>>1)&1,guess&1])
            print(f'[*] Recovered {i*2}/64 H bits-worth of independent equations '
                  f'({trials} decryption submissions).',flush=True)
        if got_flag0 is None:
            # Extremely unlikely (all 32 two-bit leaks equal 3), but this
            # confirms FLAG0 with one extra allowed verification attempt.
            last_payload=make_forgery(nonce,ct,ad,tag,variants[0][0],3)
            got_flag0=extract_flag(submit(conn,last_payload))
            trials+=1
            if got_flag0 is None:
                raise RuntimeError('FLAG0 confirmation unexpectedly failed')
            print(f'[+] FLAG0: {got_flag0.decode("utf-8","replace")}',flush=True)
        secret_H=solve_binary_system(equations,values)
        print('[+] Recovered H = '+secret_H.hex(),flush=True)
        # This makes FLAG1 trivial: the encrypt oracle permits H as plaintext.
        # A non-command 12-byte plaintext containing H is accepted.
        print('[*] Requesting an ordinary encryption of H + "zzzz".',flush=True)
        result=request_encrypt(conn,secret_H+b'zzzz',8)
        nonce1,ct1,ad1,tag1=decode_result(result,12)
        payload=b','.join(x.hex().encode() for x in (nonce1,ct1,ad1,tag1))
        result=submit(conn,payload)
        trials+=1
        flag1=extract_flag(result)
        if flag1 is None:
            raise RuntimeError('FLAG1 rejected: likely wrong observed bit or challenge changed')
        print('[+] FLAG1: '+flag1.decode('utf-8','replace'),flush=True)
        print(f'[+] Complete. {trials+2} total encryption/decryption requests.',flush=True)
        return got_flag0,flag1,secret_H
    finally:
        conn.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('port',type=int)
    args=parser.parse_args()
    solve(args.host,args.port)