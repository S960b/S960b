#!/usr/bin/env python3
"""Read retoc-to-legacy UE5.7 UMAP + UEXP with a real FPackageFileSummary.

Accurately decodes the local name map and FObjectExport table where its UE5.7
layout can be validated. Coordinates are ONLY forensic candidates until
UStaticMeshComponent unversioned properties / attachment transforms are decoded.
No third-party dependencies. The original 25,661-byte UnrealDuck UMAP is not
included: this script was tested on a synthetic -9 header and table sample.
"""
import argparse, json, csv, struct, math, re
from pathlib import Path
from collections import defaultdict

MAGIC = bytes.fromhex('c1832a9e')
RE = re.compile(r'^(?:SM_(?:Cube|Cylinder|Ramp|QuarterCylinder)\d+|DuckFinishBlock|PlayerStart)$', re.I)
class BadFormat(ValueError): pass

def unpack(b,pos,fmt):
    size=struct.calcsize(fmt)
    if pos<0 or pos+size>len(b): raise BadFormat(f'Out of bounds at 0x{pos:x} reading {fmt}')
    return struct.unpack_from(fmt,b,pos)

def strread(b,p):
    (count,) = unpack(b,p,'<i');p+=4
    if count==0:return '',p
    if not -65536<count<65536:raise BadFormat(f'Invalid FString length {count} @0x{p-4:x}')
    size=count if count>0 else -count*2
    if p+size>len(b):raise BadFormat('String extends past file')
    raw=b[p:p+size]
    if count>0:
        if raw[-1:]!=b'\0':raise BadFormat('Nonterminated ANSI FString')
        val=raw[:-1].decode('utf-8','replace')
    else:
        if raw[-2:]!=b'\0\0':raise BadFormat('Nonterminated UTF16 FString')
        val=raw[:-2].decode('utf-16le','replace')
    return val,p+size

def header(b):
    if b[:4]!=MAGIC:raise BadFormat('Not a legacy Unreal package (magic mismatch)')
    legacy,ue3,ue4,ue5,licensee=unpack(b,4,'<5i')
    if legacy!=-9:raise BadFormat(f'Expected cooked UE5.6/5.7 legacy version -9, got {legacy}')
    savedhash=b[24:44]
    total_size=unpack(b,44,'<i')[0]
    custom_count=unpack(b,48,'<i')[0]
    if not 0<=custom_count<=1000:raise BadFormat(f'Unreasonable custom version count: {custom_count}')
    # Custom versions are (FGuid 16B, version int32) for this cooked format.
    path_at=52+custom_count*20
    pkg,p=strread(b,path_at)
    flags=unpack(b,p,'<I')[0]; p+=4
    name_count,name_offset=unpack(b,p,'<ii'); name_pair_at=p; p+=8
    if not pkg.startswith('/'):raise BadFormat(f'Unexpected package name: {pkg!r}')
    if not (0<name_count<50000 and p<=name_offset<len(b)):
        raise BadFormat(f'Implausible NameMap count={name_count}, offset={name_offset}')
    return dict(legacy=legacy,ue3=ue3,ue4=ue4,ue5=ue5,licensee=licensee,
                total_header_size=total_size,package=pkg,flags=flags,
                custom_versions=custom_count,header_name_len_offset=path_at,
                name_count=name_count,name_offset=name_offset,
                name_count_field_at=name_pair_at,name_offset_field_at=name_pair_at+4,
                summary_after_names_at=p)

def parse_names(b,h):
    pos=h['name_offset'];names=[]
    for i in range(h['name_count']):
        n,pos=strread(b,pos)
        if not n or any(ord(c)<32 for c in n):raise BadFormat(f'NameMap[{i}] invalid: {n!r}')
        unpack(b,pos,'<HH') # 2x u16 hashes
        pos+=4
        names.append(n)
    return names,pos

def export_record(h,data,off,stride,index,names):
    if off+max(stride,96)>len(h):raise BadFormat('Truncated ExportMap')
    cls,superid,tmpl,outer,ni,num,flags,sz,ofs=unpack(h,off,'<6iIqq')
    if not 0<=ni<len(names) or not 0<=num<100000:raise BadFormat('Bad FName')
    if max(abs(v) for v in (cls,superid,tmpl,outer))>100000:raise BadFormat('Bad FPackageIndex')
    if sz<0 or sz>len(data)+len(h):raise BadFormat('Bad serialization size')
    if ofs<0 or ofs>len(data)+len(h):raise BadFormat('Bad serial offset')
    # UE FArchive serializes bool as little-endian uint32. Expect their values to be 0 or 1.
    if any(unpack(h,off+v,'<I')[0] not in (0,1) for v in (44,48,52,56,64,68,72)):
        raise BadFormat('Invalid bool fields in UE5.7 FObjectExport')
    name=names[ni]+(f'_{num-1}' if num else '')
    return dict(i=index+1,record_at=off,cls=cls,super=superid,template=tmpl,
                outer=outer,name=name,name_index=ni,number=num,size=sz,serial_offset=ofs)

def find_export_table(h,data,head,names,names_end):
    n=len(names); options=[]
    # Expected fast path for cooked UE5.7 with PKG_FilterEditorOnly and SoftObjectPaths:
    # name pair at 94; +8 soft-object-paths, +8 GatherableText -> exports pair at 118.
    near=head['summary_after_names_at']
    checkpositions=[near+16,near+20,near+24]+list(range(near,min(near+240,len(h)-8)))
    for p in dict.fromkeys(checkpositions):
        if p<0 or p+8>len(h):continue
        count,offset=unpack(h,p,'<ii')
        if not (1<=count<=1000 and offset>=names_end and offset<len(h)):continue
        for stride in (96,112,100,104,108,92):
            if offset+count*stride>len(h):continue
            good=0; target_names=0; values=[]
            test_count=min(count,30)
            for i in range(test_count):
                try:r=export_record(h,data,offset+i*stride,stride,i,names)
                except BadFormat:break
                good+=1;target_names+=bool(RE.match(r['name']))
                values.append(r)
            if good<min(count,8):continue
            # Requiring most of a sequential sample to validate drastically cuts false positives.
            if good<min(count,25):continue
            rank=good*10+target_names*7+(60 if p==near+16 else 0)+(30 if stride==96 else 0)
            options.append((rank,p,count,offset,stride))
    if not options:raise BadFormat(f'ExportMap not found after NameMap; try --diagnose; name end=0x{names_end:x}')
    options.sort(reverse=True)
    _,pair_at,count,offset,stride=options[0]
    arr=[]
    for i in range(count):
        arr.append(export_record(h,data,offset+i*stride,stride,i,names))
    return dict(export_count=count,export_offset=offset,export_pair_at=pair_at,stride=stride),arr

def attach(assets):
    lookup={x['i']:x for x in assets}
    def parent(obj):
        seen=set(); outer=obj['outer']
        while outer>0 and outer in lookup and outer not in seen:
            seen.add(outer);x=lookup[outer]
            if RE.match(x['name']):return x['name']
            outer=x['outer']
        return None
    for obj in assets:obj['actor_owner']=(obj['name'] if RE.match(obj['name']) else parent(obj))

def get_data(b,d,rec):
    pos=rec['serial_offset'];sz=rec['size']
    # SerialOffset in legacy packages references a virtual .umap+.uexp.
    if pos>=len(b) and pos+sz<=len(b)+len(d):
        return d[pos-len(b):pos-len(b)+sz], 'file-virtual'
    if 0<=pos and pos+sz<=len(d):return d[pos:pos+sz], 'uexp-relative'
    return b'', 'out-of-range'

def likely(v):
    if not all(math.isfinite(x) and abs(x)<100000 for x in v):return False
    if max(map(abs,v))<15:return False
    if any(0<abs(x)<1e-8 for x in v):return False
    return True

def candidates(payload,maxcand=50):
    rows=[]
    for fmt,tag in (('<3d','FVector3d'),('<3f','FVector3f')):
        size=struct.calcsize(fmt)
        for off in range(0,len(payload)-size+1,4):
            v=unpack(payload,off,fmt)
            if not likely(v):continue
            s=0
            for x in v:
                s+=(abs(x-round(x))<.005)
                s+=(abs(x/50-round(x/50))<.005)*2
            rows.append((s,off,tag,tuple(round(x,4) for x in v)))
    return sorted(rows,key=lambda x:(-x[0],x[1]))[:maxcand]

def render(arr,axis,cell):
    pts={}
    for o,xyz in arr:
        x,y=round(xyz[axis[0]]/cell),round(xyz[axis[1]]/cell)
        if 'Finish' in o:mark='F'
        elif 'Player' in o:mark='P'
        elif 'Cylinder' in o:mark='O'
        elif 'Ramp' in o:mark='R'
        else:mark='C'
        pts.setdefault((x,y),mark)
    if not pts:return '(No coordinates validated)'
    minx=min(k[0] for k in pts);maxx=max(k[0] for k in pts)
    miny=min(k[1] for k in pts);maxy=max(k[1] for k in pts)
    if maxx-minx>120 or maxy-miny>120:return '(Grid too big: increase --cell)'
    return '\n'.join(''.join(pts.get((x,y),'.') for x in range(minx,maxx+1)) for y in range(maxy,miny-1,-1))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('umap',type=Path);ap.add_argument('--uexp',type=Path)
    ap.add_argument('--out',type=Path,default=Path('duck_level_fixed'))
    ap.add_argument('--cell',type=float,default=100)
    args=ap.parse_args();b=args.umap.read_bytes()
    ex=args.uexp if args.uexp else args.umap.with_suffix('.uexp')
    d=ex.read_bytes()
    h=header(b); names,names_end=parse_names(b,h)
    print('Package:',h['package'])
    print(f"NameCount   field at 0x{h['name_count_field_at']:X} = {h['name_count']}")
    print(f"NameOffset  field at 0x{h['name_offset_field_at']:X} = 0x{h['name_offset']:X} ({h['name_offset']})")
    print(f"NameMap    starts at 0x{h['name_offset']:X}; ends at 0x{names_end:X}")
    print('Name hits:',[x for x in names if RE.search(x)][:25])
    meta,records=find_export_table(b,d,h,names,names_end)
    attach(records)
    print(f"ExportMap count={meta['export_count']} offset=0x{meta['export_offset']:x} stride={meta['stride']} fields@0x{meta['export_pair_at']:x}")
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'names.txt').write_text('\n'.join(f'{i}\t{n}' for i,n in enumerate(names)),encoding='utf8')
    (args.out/'exports.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    (args.out/'header.json').write_text(json.dumps({**h,**meta},ensure_ascii=False,indent=2))
    rows=[]
    for obj in records:
        if not obj['actor_owner']:continue
        payload,mode=get_data(b,d,obj)
        for score,off,kind,xyz in candidates(payload):
            rows.append([obj['actor_owner'],obj['name'],obj['i'],mode,off,kind,*xyz,score,'UNVERIFIED'])
    with (args.out/'coordinate_candidates.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['actor','export','id','address_mode','relative_offset','kind','x','y','z','score','status']);w.writerows(rows)
    byactor=defaultdict(list)
    for r in rows:byactor[r[0]].append(r)
    winners=[]
    for actor,items in sorted(byactor.items()):
        best=max(items,key=lambda v:v[9])
        winners.append((actor,tuple(best[6:9])))
        print(f"CANDIDATE {actor}: ({best[6]}, {best[7]}, {best[8]}) src={best[1]} {best[5]} score={best[9]}; NOT VALIDATED")
    for name,axes in [('xy',(0,1)),('xz',(0,2)),('yz',(1,2))]:
        (args.out/(name+'.txt')).write_text(render(winners,axes,args.cell))
    print('Saved:',args.out,'NOTE: candidate vectors are not proven actor world coordinates. Need usmap or validated transform serial layout.')

if __name__=='__main__':
    try:main()
    except (BadFormat,ValueError) as exc:raise SystemExit('PARSE ERROR: '+str(exc))
