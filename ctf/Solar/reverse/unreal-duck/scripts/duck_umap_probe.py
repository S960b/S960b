#!/usr/bin/env python3
"""UnrealDuck UE5 cooked legacy UMAP forensic probe.

Designed for output of retoc to-legacy + repak; stdlib only.
This DOES NOT decode unversioned UObject properties without mappings.
It resolves on-disk package names, probable FObjectExport records and scans
only each export's bounded bytes for LWC double / float transform candidates.
Results are CANDIDATES, not authoritative actor world coordinates.
"""
import argparse
import csv
import json
import math
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r'^(SM_(?:Cube|Cylinder|Ramp|QuarterCylinder)\d+|DuckFinishBlock|PlayerStart)$', re.I)
TARGET_RE = re.compile(r'SM_(Cube|Cylinder|Ramp|QuarterCylinder)\d+|DuckFinishBlock|PlayerStart', re.I)
MAGIC = bytes.fromhex('c1832a9e')

def number(blob, off, fmt):
    if off < 0 or off + struct.calcsize(fmt) > len(blob):
        raise ValueError('short read')
    return struct.unpack_from(fmt, blob, off)[0]


def fstring(blob, off):
    n = number(blob, off, '<i'); off += 4
    if n == 0 or abs(n) > 2048:
        raise ValueError('invalid FString size')
    needed = n * 1 if n > 0 else -n * 2
    if off + needed > len(blob):
        raise ValueError('FString outside file')
    raw = blob[off:off+needed]
    if n > 0:
        if raw[-1] != 0: raise ValueError('nonterminated ansi')
        return raw[:-1].decode('utf-8'), off + needed
    if not raw.endswith(b'\x00\x00'): raise ValueError('nonterminated wide')
    return raw[:-2].decode('utf-16le'), off + needed


def name_map(uasset):
    """Find name-map using header count/offset pair, verifying each FString+hash."""
    hits = []
    stop = min(len(uasset) - 8, 4096)
    for loc in range(8, stop, 4):
        count, offset = struct.unpack_from('<ii', uasset, loc)
        if not (10 <= count <= 2500 and 64 <= offset < len(uasset)):
            continue
        if offset < loc or offset > len(uasset) - min(count * 8, 64):
            continue
        names = []
        pos = offset
        try:
            for _ in range(count):
                name, pos = fstring(uasset, pos)
                if not (1 <= len(name) < 2048 and name.isprintable()):
                    raise ValueError('unprintable name')
                pos += 4  # two 16-bit FName hashes in the legacy cooked name map
                if pos > len(uasset): raise ValueError('short hashes')
                names.append(name)
        except (ValueError, UnicodeError, struct.error):
            continue
        target = sum(bool(TARGET_RE.search(n)) for n in names)
        standard = sum(n in ('None','World','Level','Object','StaticMeshComponent','Actor','PersistentLevel') for n in names)
        rank = (target*100 + standard*5 + min(count, 600)//10)
        hits.append((rank, target, count, offset, names))
    if not hits:
        raise ValueError('No validated NameMap found; need real UMAP bytes, version-aware decoder/usmap')
    hits.sort(reverse=True, key=lambda h:h[0])
    _, targets, count, offset, names = hits[0]
    if targets == 0:
        raise ValueError('NameMap exists but no expected actor names present; inspect alternate level')
    return names, {'count':count,'offset':offset,'matched_target_names':targets}

@dataclass
class Export:
    table_at: int
    cls: int
    outer: int
    name: str
    name_index: int
    number: int
    size: int
    absolute_offset: int
    data: bytes
    data_start: int
    index: int = 0


def export_map(uasset, uexp, names):
    """Probe FObjectExport core layout (UE5 cooked legacy), validate offsets and bounds.
    Does not assume fixed stride or pretend it has decoded all optional export fields.
    """
    result = {}
    total = uasset+uexp
    for pos in range(16, len(uasset) - 27):
        try:
            nameidx, instance = struct.unpack_from('<ii', uasset, pos)
            if not (0 <= nameidx < len(names) and 0 <= instance < 100000): continue
            flag, size, off = struct.unpack_from('<Iqq', uasset, pos + 8)
            if not (0 < size <= len(uexp) and 0 <= off < len(total)):
                continue
            a, b, c, outer = struct.unpack_from('<iiii', uasset, pos - 16)
            if any(abs(v)>8192 for v in (a,b,c,outer)): continue
            name = names[nameidx] + (f'_{instance-1}' if instance else '')
            # reject obvious accidental signature matches, verify size in one of two ways
            locations = []
            if off >= len(uasset) and off + size <= len(total): locations.append((off-len(uasset),total[off:off+size]))
            if off + size <= len(uexp): locations.append((off,uexp[off:off+size]))
            if not locations: continue
            # Prefer virtual position into concatenated uasset + uexp.
            data_start, data = locations[0]
            result[pos-16] = Export(pos-16, a, outer, name, nameidx, instance, size, off, data, data_start)
        except (ValueError, struct.error): continue
    exports = sorted(result.values(), key=lambda e:e.table_at)
    # Only reconstruct FPackageIndex when header count+offset is unambiguous and
    # every export record was found in order, otherwise outer numbers stay raw.
    first = exports[0].table_at if exports else -1
    match = []
    for p in range(0, min(4096,len(uasset)-8), 4):
        cnt,off = struct.unpack_from('<ii',uasset,p)
        if cnt == len(exports) and off == first:
            match.append(p)
    if len(match) == 1:
        for i,e in enumerate(exports,1):e.index=i
    return exports

@dataclass
class Coord:
    owner: str
    origin: str
    kind: str
    offset: int
    xyz: tuple
    score: float


def quality(xyz):
    if not all(math.isfinite(v) for v in xyz): return -100
    if any(abs(v)>100000 or (abs(v)>0 and abs(v)<1e-7) for v in xyz): return -100
    # Avoid ordinary struct zeroes, quaternion/scale snippets and denormals.
    if max(abs(v) for v in xyz) < 15: return -100
    if max(abs(v) for v in xyz) > 20000: return -50
    score = 0
    for v in xyz:
        if abs(v-round(v)) < .015: score += 1
        if abs(v / 10 - round(v / 10)) < .005: score += 1
        if abs(v / 50 - round(v / 50)) < .005: score += 1.5
    if abs(xyz[0])>15 and abs(xyz[1])>15: score += 2
    if sum(abs(v) > 1 for v in xyz) >= 2: score += 1
    return score


def scan_vectors(e):
    """Bounded forensic guesses, *never* unconditional correct FVector values."""
    d=e.data; result=[]
    for fmt, kind in (('<ddd','FVector3d'),('<fff','FVector3f')):
        width=struct.calcsize(fmt)
        for p in range(0,len(d)-width+1,4):
            xyz=struct.unpack_from(fmt,d,p)
            score=quality(xyz)
            if score >= 6:
                result.append(Coord(e.name, e.name, kind, p, xyz, score))
    # Search UE FTransform pattern: Quaternion (x,y,z,w), translation(x,y,z), scale(x,y,z).
    # For UE5 LWC FTransform3d: 10 doubles, UE4 FTransform3f 10 floats.
    for fmt,kind,eps in (('<10d','FTransform3d',.05),('<10f','FTransform3f',.05)):
        width=struct.calcsize(fmt)
        for p in range(0,len(d)-width+1,4):
            vals=struct.unpack_from(fmt,d,p)
            q=vals[:4]; xyz=vals[4:7]; scale=vals[7:]
            if not all(math.isfinite(v) for v in vals): continue
            if abs(sum(v*v for v in q)-1)>.08: continue
            if not all(.01<=abs(s)<=100 for s in scale): continue
            score=quality(xyz)
            if score>=4:
                result.append(Coord(e.name,e.name,kind,p+4*(8 if kind.endswith('3d') else 4),xyz,score+4))
    result.sort(key=lambda c:(-c.score,c.offset,c.kind))
    return result[:40]


def write_grid(points, plane, cell=100, maxsize=120):
    ia,ib=({'xy':(0,1),'xz':(0,2),'yz':(1,2)})[plane]
    coords={}
    for n,c in points.items():
        a,b=c.xyz[ia],c.xyz[ib]
        x,y=round(a/cell), round(b/cell)
        ch=('F' if 'Finish' in n else 'P' if 'PlayerStart' in n else
            'C' if 'Cube' in n else 'Y' if 'Cylinder' in n else 'R' if 'Ramp' in n else 'Q')
        coords.setdefault((x,y),[]).append(ch)
    if not coords: return 'No points'
    xs,ys=zip(*coords)
    width,height=max(xs)-min(xs)+1,max(ys)-min(ys)+1
    if width>maxsize or height>maxsize:
        return f'Grid {width}x{height} too big for cell={cell}; increase --cell'
    out=[f'projection={plane} cell={cell} cm  {width}x{height}']
    for y in range(max(ys),min(ys)-1,-1):
        row=[]
        for x in range(min(xs),max(xs)+1):
            ch=coords.get((x,y),[])
            row.append('.' if not ch else ch[0] if len(ch)==1 else '#')
        out.append(''.join(row))
    return '\n'.join(out)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('umap',type=Path)
    ap.add_argument('--uexp',type=Path)
    ap.add_argument('--out',type=Path,default=Path('duck_level'))
    ap.add_argument('--cell',type=float,default=100)
    ap.add_argument('--min-score',type=float,default=8)
    args=ap.parse_args()
    uasset=args.umap.read_bytes()
    if uasset[:4]!=MAGIC: raise SystemExit('Wrong .umap package magic; need retoc to-legacy cooked asset')
    exfile=args.uexp or args.umap.with_suffix('.uexp')
    uexp=exfile.read_bytes()
    names,stats=name_map(uasset)
    exps=export_map(uasset,uexp,names)
    targets=[e for e in exps if TARGET_RE.search(e.name)]
    print('ExportMap linkage:', 'validated indices' if exps and exps[0].index else 'NOT validated; component-actor outer links UNKNOWN')
    print(f'NameMap: {stats}; exports detected={len(exps)}, named target exports={len(targets)}')
    # keep metadata for source troubleshooting
    args.out.mkdir(exist_ok=True,parents=True)
    (args.out/'names.txt').write_text('\n'.join(f'{i:4} {n}' for i,n in enumerate(names)))
    (args.out/'exports.json').write_text(json.dumps([{'name':e.name,'table_at':e.table_at,'serial_abs':e.absolute_offset,'size':e.size,'outer_raw':e.outer,'class_raw':e.cls} for e in exps],indent=2))
    candidates=[]
    for e in exps:
        # keep all for geometric analysis; component exports may have generic names
        if e.size < 16: continue
        candidates.extend(scan_vectors(e))
    with (args.out/'candidates.csv').open('w',newline='') as f:
        wr=csv.writer(f)
        wr.writerow(['export','kind','byte_offset_in_export','x','y','z','heuristic_score','verification'])
        for c in candidates:
            wr.writerow([c.owner,c.kind,c.offset,*[f'{v:.6f}' for v in c.xyz],c.score,'UNVERIFIED'])
    # If export indexing was validated, bind component candidates to owner
    # actors by traversing FPackageIndex OuterIndex (positive indexes point to exports).
    index={e.index:e for e in exps if e.index}
    grouped=defaultdict(list)
    for e in exps:
        if not e.index: continue
        o=e.outer; visited=set()
        while o>0 and o in index and o not in visited:
            visited.add(o)
            parent=index[o]
            if TARGET_RE.search(parent.name):
                grouped[parent.name].extend(c for c in candidates if c.owner==e.name)
                break
            o=parent.outer
    winners={}
    for e in targets:
        cs=[c for c in candidates if c.owner==e.name or c in grouped[e.name]]
        cs=[c for c in cs if c.score>=args.min_score]
        if cs:
            winners[e.name]=max(cs,key=lambda c:c.score)
    for e in targets:
        cs=[c for c in candidates if c.owner==e.name]
        print(f'{e.name:27} export={e.table_at:5} size={e.size:5} candidates={len(cs):2} ' +
              ('candidate='+repr(winners[e.name].xyz) if e.name in winners else 'NO VERIFIED COORDINATE'))
    (args.out/'tentative.json').write_text(json.dumps({n:{'xyz':c.xyz,'kind':c.kind,'score':c.score,'verified':False} for n,c in winners.items()},indent=2))
    for plane in ('xy','xz','yz'):
        plot=write_grid(winners,plane,args.cell)
        (args.out/f'{plane}.txt').write_text(plot+'\n')
        print('\n'+plot)
    if len(winners) < len(targets):
        print('\nWARNING: This is not a final exact actor-coordinate extractor. UnrealCooked unversioned properties')
        print('need class schemas/usmap for authoritative RelativeLocation + OuterIndex and world transforms.')
    print('\nFiles:',args.out)

if __name__=='__main__':main()
