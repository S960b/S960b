#!/usr/bin/env python3
"""Offline verify saved evidence; does not rerun crypto. For full repro use run_reproduce.sh."""
from pathlib import Path
import json,hashlib
root=Path(__file__).resolve().parent
votes_file=root/'solution/evidence/recovered_votes.txt'
rows=[tuple(map(int,s.split())) for s in votes_file.read_text().splitlines()]
assert len(rows)==426,len(rows)
assert all(len(r)==4 and r[0]==i and r[1] in (0,1,2) and r[3]==1 for i,r in enumerate(rows))
assert [r[1] for r in rows[420:426]]==[1,0,2,1,0,2]
flag=bytes(sum(rows[i+j][1]*3**(5-j) for j in range(6)) for i in range(0,420,6))
assert len(flag)==70 and flag== (root/'solution/flag.txt').read_bytes().rstrip(b'\n')
assert len(json.loads((root/'solution/evidence/votes.json').read_text())['votes'])==1000
arc=root/'original/voteme-27f2656eaea4fcf2.tar.gz'
assert hashlib.sha256(arc.read_bytes()).hexdigest()=='27f2656eaea4fcf26c1f4a782827dea9b1cc4719641ad823a328247ea15e5abc'
print('PASS: original handout SHA256, 1000 votes, 426 evidence rows, known six test votes, 70-byte flag')
print('FLAG:',flag.decode())
