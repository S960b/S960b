import sys
DIRS = [(1,0),(1,-1),(0,-1),(-1,0),(-1,1),(0,1)]
R = 4
cells = [(q,r) for q in range(-R,R+1) for r in range(-R,R+1) if abs(q+r) <= R]
print("cells", len(cells))
S = set(cells)
adj = {c: [ (c[0]+d[0], c[1]+d[1]) for d in DIRS if (c[0]+d[0], c[1]+d[1]) in S ] for c in cells}
print("degrees", sorted({len(v) for v in adj.values()}))

sys.setrecursionlimit(10000)
path = []
seen = set()
def dfs(c, start):
    path.append(c); seen.add(c)
    if len(path) == len(cells):
        ok = start in adj[c]
        path.pop() if not ok else None
        if ok:
            return True
        seen.discard(c)
        return False
    nbrs = [n for n in adj[c] if n not in seen]
    nbrs.sort(key=lambda n: sum(1 for m in adj[n] if m not in seen))
    for n in nbrs:
        if dfs(n, start):
            return True
    path.pop(); seen.discard(c)
    return False

start = (0,0)
if dfs(start, start):
    print("CYCLE", len(path))
    print(path)
else:
    print("no cycle")
