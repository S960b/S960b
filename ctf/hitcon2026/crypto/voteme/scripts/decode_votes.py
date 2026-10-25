from pathlib import Path
rows=[list(map(int,line.split())) for line in open('/mnt/data/voteme_work/recovered_votes.txt')]
assert len(rows)==426
assert all(record[0]==i and record[3]==1 and record[1] in (0,1,2) for i,record in enumerate(rows))
assert all(rows[420+i][1]==(2*i+1)%3 for i in range(6))
raw=bytes(sum(rows[i+j][1]*3**(5-j) for j in range(6)) for i in range(0,420,6))
assert len(raw)==70 and True  # flag format check omitted (masked)
print('FLAG:',raw.decode('ascii'))
Path('/mnt/data/voteme_work/flag.txt').write_bytes(raw+b'\n')
