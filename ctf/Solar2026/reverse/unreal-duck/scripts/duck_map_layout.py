#!/usr/bin/env python3
"""UE5.7 legacy UMAP header/import/export map diagnostic (retoc to-legacy).

This reads maps from FPackageFileSummary rather than searching aligned values.
It does NOT claim that raw float/double triples are validated actor transforms.
Usage: python3 duck_map_layout.py Lvl_ThirdPerson.umap --out duck_map_layout
"""
import argparse
import csv
import json
import math
import struct
from pathlib import Path

MAGIC=0x9e2a83c1

def u(b, o, fmt):
    n=struct.calcsize(fmt)
    if o<0 or o+n>len(b): raise ValueError(f'out-of-bounds at {o:#x} for {fmt}')
    v=struct.unpack_from(fmt,b,o)
    return v[0] if len(v)==1 else v

def fstr(b,o):
    n=u(b,o,'<i'); o+=4
    if abs(n)>50000:raise ValueError('unreasonable FString length')
    if n==0:return '',o
    raw=b[o:o+(n if n>0 else -2*n)]
    if len(raw)!=(n if n>0 else -2*n):raise ValueError('truncated FString')
    if n>0:
        if not raw.endswith(b'\0'):raise ValueError('unterminated ANSI FString')
        return raw[:-1].decode('utf8','replace'),o+n
    if not raw.endswith(b'\0\0'):raise ValueError('unterminated wide FString')
    return raw[:-2].decode('utf-16le','replace'),o-2*n

def read_header(b):
    if u(b,0,'<I')!=MAGIC:raise ValueError('not a UE legacy UMAP')
    legacy,ue3,ue4,ue5,licensee=u(b,4,'<5i')
    if legacy!=-9:raise ValueError('Expected retoc UE5.6/5.7 version -9')
    header_total=u(b,44,'<i'); ncustom=u(b,48,'<i')
    if ncustom<0 or ncustom>200:raise ValueError('unexpected custom version count')
    pkg,pos=fstr(b,52+20*ncustom)
    flags=u(b,pos,'<I');pos+=4
    def pair(label):
        nonlocal pos
        here=pos; value=u(b,pos,'<ii');pos+=8
        return {'label':label,'field_at':f'{here:#x}','count':value[0],'offset':value[1]}
    pairs={}
    pairs['names']=pair('NameMap')
    pairs['soft_object_paths']=pair('SoftObjectPaths') # Present for UE5.7
    if not(flags & 0x80000000):
        _,pos=fstr(b,pos)  # LocalizationId serialized iff !FilterEditorOnly
    pairs['gatherable_text']=pair('GatherableText')
    pairs['exports']=pair('ExportMap')
    pairs['imports']=pair('ImportMap')
    pairs['cell_exports']=pair('CellExportMap')
    pairs['cell_imports']=pair('CellImportMap')
    metadata_at=pos; metadata=u(b,pos,'<i');pos+=4
    depends_at=pos;depends=u(b,pos,'<i');pos+=4
    pairs['soft_pkg_refs']=pair('SoftPackageReferences')
    searchable_at=pos; searchable=u(b,pos,'<i');pos+=4
    thumbnails_at=pos; thumbnails=u(b,pos,'<i');pos+=4
    fields=dict(package=pkg,legacy=legacy,ue3=ue3,ue4=ue4,ue5=ue5,flags=f'{flags:#010x}',
                cooked=bool(flags&0x200),filter_editor_only=bool(flags&0x80000000),
                uses_unversioned_properties=bool(flags&0x2000),header_total=header_total,
                custom_versions=ncustom,metadata={'field_at':hex(metadata_at),'offset':metadata},
                depends={'field_at':hex(depends_at),'offset':depends},
                searchable_names={'field_at':hex(searchable_at),'offset':searchable},
                thumbnails={'field_at':hex(thumbnails_at),'offset':thumbnails})
    return fields,pairs

def read_names(b,pair):
    p=pair['offset']; names=[]
    for _ in range(pair['count']):
        n,p=fstr(b,p);p+=4 # FNameEntrySerialized two uint16 hashes
        names.append(n)
    return names,p

def fname(ns,index,number):
    if 0<=index<len(ns):return ns[index]+(f'_{number-1}' if number else '')
    return f'<bad FName[{index}],num={number}>'

def imports(b,ns,pair,next_offset,flags):
    count=pair['count']; off=pair['offset']
    if count<=0:return [],None
    size=(next_offset-off)//count if (next_offset-off)%count==0 else -1
    # UE5.7 FObjectImport: 8+8+4+8+(8 unless FilterEditorOnly)+(4 optional bool)
    valid_sizes={32,40}
    if size not in valid_sizes:
        raise ValueError(f'import stride {size} unexpected; map offsets may be wrong. Expected 32 or 40')
    rows=[]
    for i in range(count):
        pos=off+i*size
        pkgidx,pkgnum,clsidx,clsnum,outer,objidx,objnum=u(b,pos,'<7i')
        at=pos+28
        if not(flags & 0x80000000):at+=8  # package FName
        optional=u(b,at,'<I')
        rows.append({'i':i+1,'record_at':pos,'class_package':fname(ns,pkgidx,pkgnum),
             'class_name':fname(ns,clsidx,clsnum),'outer':outer,
             'name':fname(ns,objidx,objnum),'optional':optional})
    return rows,size

def exports(b,ns,pair,next_offset):
    count=pair['count'];off=pair['offset']
    if count<=0:return [],None
    size=(next_offset-off)//count if (next_offset-off)%count==0 else -1
    if size not in (96,112):
        raise ValueError(f'export stride {size} unexpected; expected 96 (unversioned) or 112 (script offsets)')
    rows=[]
    for i in range(count):
        p=off+i*size
        cls,supr,templ,outer,ni,num,objflags,serial_size,serial_off=u(b,p,'<6iIqq')
        if not(0<=ni<len(ns)):raise ValueError(f'bad export FName index {ni} at {p:#x}')
        rows.append({'i':i+1,'record_at':p,'class_index':cls,'super_index':supr,
                     'template_index':templ,'outer_index':outer,'name':fname(ns,ni,num),
                     'serial_size':serial_size,'serial_offset':serial_off,
                     'object_flags':f'{objflags:#x}'})
    return rows,size

def ref(n,ims,exs):
    if n==0:return '<null>'
    if n>0:return exs[n-1]['name'] if n<=len(exs) else f'<bad export +{n}>'
    j=-n
    return ims[j-1]['name'] if j<=len(ims) else f'<bad import {n}>'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('umap',type=Path)
    ap.add_argument('--out',type=Path,default=Path('duck_map_layout'))
    a=ap.parse_args(); b=a.umap.read_bytes()
    hdr,pairs=read_header(b)
    print('PACKAGE',hdr['package'],'HEADER',hdr['header_total'],'FLAGS',hdr['flags'])
    print('Field locations and VALUES from FPackageFileSummary:')
    for name,p in pairs.items():
        print(f'  {name:20} field={p["field_at"]:6} count={p["count"]:5} data_offset={p["offset"]:#07x}')
    names,end=read_names(b,pairs['names'])
    print(f'NameMap decoded: {len(names)} names, {pairs["names"]["offset"]:#x} .. {end:#x}')
    print(f'ImportMap starts exactly at {pairs["imports"]["offset"]:#x}; '
          f'{"MATCH" if end==pairs["imports"]["offset"] else "MISMATCH: inspect hashes/summary"} '
          'against NameMap end')
    print('Import bytes:',b[pairs['imports']['offset']:pairs['imports']['offset']+64].hex(' '))
    ims,isz=imports(b,names,pairs['imports'],pairs['exports']['offset'],int(hdr['flags'],16))
    print('ImportCount',len(ims),'ImportRecordStride',isz)
    print('Imports first 8:')
    for r in ims[:8]:print(' ',r)
    # retoc writes CellImports just after normal exports; cell_exports after those.
    exs,esz=exports(b,names,pairs['exports'],pairs['cell_imports']['offset'])
    print('ExportCount',len(exs),'ExportRecordStride',esz)
    print('Export first 10:')
    for r in exs[:10]:print(' ',r)
    for r in exs:
        r['outer_name']=ref(r['outer_index'],ims,exs)
        r['class_name']=ref(r['class_index'],ims,exs)
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/'layout.json').write_text(json.dumps({'summary':hdr,'maps':pairs,'name_end':end,
                    'import_stride':isz,'export_stride':esz,'imports':ims,'exports':exs},indent=2),encoding='utf8')
    (a.out/'names.txt').write_text('\n'.join(f'{i}\t{s}' for i,s in enumerate(names)),encoding='utf8')
    with (a.out/'exports.csv').open('w',newline='') as f:
        dw=csv.DictWriter(f,fieldnames=exs[0].keys())
        dw.writeheader();dw.writerows(exs)
    for r in exs:
        if any(x in r['name'] or x in r['outer_name'] for x in ('SM_Cube','SM_Cylinder','SM_Ramp','SM_QuarterCylinder','DuckFinish','PlayerStart')):
            print('SUSPECT',r['i'],r['name'],'class=',r['class_name'],'outer=',r['outer_name'],
                  'serial=',r['serial_offset'],'size=',r['serial_size'])
    print('Saved',a.out)
    print('NOTE: authoritative class/outer/name/serial metadata; not an unversioned-property decoder.')

if __name__=='__main__':
    try:main()
    except (ValueError,struct.error) as e:raise SystemExit('PARSE ERROR: '+str(e))
