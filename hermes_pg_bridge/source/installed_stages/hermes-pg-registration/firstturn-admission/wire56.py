import base64,re
DOMAIN=b"hermes-pg/task-wire/v1\x00"
MAX=32768
TID=re.compile(r"task_[0-9a-f]{32}")
MID=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
def payload(tid,mid,seq,ver,body):
    if type(tid) is not str or not TID.fullmatch(tid) or type(mid) is not str or not MID.fullmatch(mid):
        raise ValueError("identity")
    if type(seq) is not int or not 1<=seq<=2**53-1 or type(ver) is not int or not 0<=ver<=2**53-1:
        raise ValueError("counter")
    if type(body) is not str or not body or "\x00" in body:
        raise ValueError("body")
    raw=body.encode("utf-8","strict")
    if len(raw)>MAX:raise ValueError("size")
    return DOMAIN+("\n".join((tid,mid,str(seq),str(ver)))+"\n").encode("ascii")+raw
def sign_wire(sk,tid,mid,seq,ver,body):
    data=payload(tid,mid,seq,ver,body)
    sig=base64.urlsafe_b64encode(sk.sign(data)).rstrip(b"=").decode("ascii")
    wire=f"HPG1 {seq} {ver} {sig}\n{body}"
    if len(wire.encode("utf-8"))>MAX:raise ValueError("wire size")
    return wire
