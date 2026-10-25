import json
v=json.load(open('/mnt/data/voteme_work/votes.json'))['votes']
with open('/mnt/data/voteme_work/proof_A.tsv','w') as f:
    for rec in v:
        a=rec['proof']['A']
        f.write(f"{rec['uuid']} {a[0]} {a[1]}\n")
