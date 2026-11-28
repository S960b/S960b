import sys,os,json,stat,hashlib
from pathlib import Path
C=Path("/home/kali/hermes-pg-stage35c-static-guard")
A=Path("/home/kali/bridge-pg-v2/bridge_pg")
PINS={
 C/"runtime35c.py":"fbcc683b0535086959a5491bda66bdd9e0b8333fc76f964c8b1e5a8ae0ee5fd8",
 C/"signed_send56.py":"2c183f146a45647bba34500b6472394aaade056dc927711f11fcafff75e68e1b",
 C/"reserve56.py":"f893e11b4bd7ccd188c1706604cf2ca4cf7e6858ed9e4dc6d0a31f65f00c16c6",
 C/"wire56.py":"f907ec9797e40b56997648453574ed6a82c37c6d7c43204cce793131f3e097a9",
 C/"static_guard35c.py":"643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c",
 A/"compat.py":"3433dc914cd3b62b549f6aa3accffaec704cf3f637293b4fd3444cf7a4c825fd",
 A/"store.py":"3fd36bd348fb4bcbcd2325bc9ec10588dc366af6293425bee9791efd7d231a40",
}
def verified():
    PINS.update({
        Path('/home/kali/hermes-pg-stage57-owner-turn/owner_turn57.py'):'5e472145458ee51ee08287cc907df24a1cc45b5a99d3b665d0edf87e50ba8678',
        Path('/home/kali/hermes-pg-stage57-owner-turn/coordinator57.py'):'02bd3879d596bf66be5e0e35ba06d3210056eaf8fd9cd9b31f27681ae4180eea',
        Path('/home/kali/hermes-pg-stage57-owner-turn/idle57.py'):'d2ba07a343d140f50295cd266e01bcb2a3fceababd2d65947363d420ddacfb84',
        Path('/home/kali/hermes-pg-stage57-owner-turn/adapter57.py'):'1d4285acb71113244b6a8417d92785634ed59b0893880342f8c87b4257e1b494',
    })
    for p,h in PINS.items():
        s=p.lstat()
        if not(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and not s.st_mode&0o022 and hashlib.sha256(p.read_bytes()).hexdigest()==h):
            raise ValueError("source_drift")
def main():
    # The worker accepts only the narrow owner request, never a signing body.
    r=json.loads(sys.stdin.buffer.read(2049))
    if type(r) is not dict or set(r)!={"tid","version","target","request_id"}:raise ValueError("request")
    verified()
    from coordinator57 import Request,check_req
    request=Request(**r);check_req(request)
    from adapter57 import Adapter
    from bridge_pg.compat import Catalog
    import runtime35c
    # Keep owner actions inside the same one-dialogue write permit.
    if not runtime35c.permitted("send",request.tid):
        return {"status":"waiting","reason":"permit_closed"}
    return Adapter(Catalog(),runtime35c).run(request)
if __name__=="__main__":
    try:print(json.dumps(main(),separators=(",",":")))
    except Exception:
        print('{"status":"uncertain"}')
        sys.exit(2)
