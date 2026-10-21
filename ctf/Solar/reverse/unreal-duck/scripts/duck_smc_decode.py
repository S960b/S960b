#!/usr/bin/env python3
"""Decode known UnrealDuck UE5.7 StaticMeshComponent transforms from retoc legacy exports.

Uses the already verified layout.json from duck_map_layout.py; each export has
1-based 'i', serial_offset, serial_size, and outer_name. All coordinates in UE
centimeters, relative to the parent component, NOT automatically world-space.

Usage:
  python3 duck_map_layout.py Lvl_ThirdPerson.umap --out duck_map_layout
  python3 duck_smc_decode.py Lvl_ThirdPerson.umap --layout duck_map_layout/layout.json --out duck_smc.csv

Format learned from the 55 real SMC hexdumps in job mt_duck_smc_f8afd579.job.
This is intentionally challenge-specific and fails closed on other sizes.
"""
import argparse, csv, json, math, re, struct
from pathlib import Path

# total serialization = variable property preamble + location(24)
# + optional rotator(24) + scale(24) + fixed serialized component tail(20)
FORMATS = {113: (21, 45, 69), 102: (10, 34, 58),
           91: (23, None, 47), 80: (12, None, 36)}
ACTOR = re.compile(r'^SM_(?:Cube|Ramp|Cylinder|QuarterCylinder)(?:\d+)?$')

def vec3(data, start):
    return (0.0, 0.0, 0.0) if start is None else struct.unpack_from('<3d', data, start)

def decode(data):
    spec = FORMATS.get(len(data))
    if not spec:
        raise ValueError(f'unrecognized UnrealDuck component size: {len(data)}')
    if data[:2] != b'\x04\x02':
        raise ValueError(f'not an expected unversioned component header: {data[:8].hex()}')
    loc, rot, scale = map(lambda n: vec3(data, n), spec)
    if (not all(math.isfinite(x) for x in (*loc, *rot, *scale))
            or max(map(abs, loc)) > 5000
            or any(not 0.01 <= abs(x) <= 100 for x in scale)
            or max(map(abs, rot)) > 1080):
        raise ValueError('unreasonable transform values; check version and owner')
    # This is a precise decode of 9 or 6 double values at observed offsets.
    # It is NOT a general UE5.7 unversioned property deserializer.
    return loc, rot, scale

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('umap', type=Path, help='Legacy UMAP alongside same-basename .uexp')
    p.add_argument('--layout', type=Path, required=True, help='layout.json produced by duck_map_layout.py')
    p.add_argument('--out', type=Path, default=Path('duck_smc.csv'))
    a = p.parse_args()
    header = a.umap.read_bytes()
    body = a.umap.with_suffix('.uexp').read_bytes()
    layout = json.loads(a.layout.read_text(encoding='utf-8'))
    exports = layout['exports']
    records = []
    failures = []
    for e in exports:
        owner = e.get('outer_name', '')
        if not ACTOR.fullmatch(owner):
            continue
        cls = e.get('class_name', '')
        if 'StaticMeshComponent' not in cls and 'StaticMeshComponent' not in e.get('name', ''):
            # Some exporters use a custom name. Only accepted sizes and component header
            # can make us accept a record, never actor name alone.
            pass
        size = int(e['serial_size'])
        off = int(e['serial_offset'])
        if size not in FORMATS:
            continue
        pos = off - len(header)
        if not 0 <= pos <= len(body)-size:
            failures.append((owner, 'invalid virtual serial offset', off, size))
            continue
        data = body[pos:pos+size]
        try:
            loc, rot, scale = decode(data)
        except ValueError as ex:
            failures.append((owner, str(ex), off, size))
            continue
        records.append(dict(actor=owner, export_index=e['i'], component=e['name'],
            x=loc[0], y=loc[1], z=loc[2], pitch=rot[0], yaw=rot[1], roll=rot[2],
            sx=scale[0], sy=scale[1], sz=scale[2], serial_offset=off, size=size))
    if not records:
        raise SystemExit('No StaticMeshComponent transforms: verify layout.json, actor owner, file pair')
    records.sort(key=lambda x:(re.sub(r'\d+$', '', x['actor']),int(re.search(r'\d+$',x['actor']).group()) if re.search(r'\d+$',x['actor']) else -1))
    with a.out.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=list(records[0])); w.writeheader(); w.writerows(records)
    for x in records:
        print(f"{x['actor']:24} ({x['x']:9.3f},{x['y']:9.3f},{x['z']:8.3f}) yaw={x['yaw']:7.1f} scale=({x['sx']:4.1f},{x['sy']:4.1f},{x['sz']:4.1f})")
    print(f'OK: {len(records)} block transforms, {len(failures)} failed; CSV: {a.out}')
    if len(records) != 53:
        print(f'WARNING: expected 53 puzzle block components, decoded {len(records)}; inspect owner relationships and failed records')
    for e in failures:print('SKIPPED', *e)
    print('CAUTION: RelativeLocation equals world Location only if the actor/root hierarchy has identity transform.')

if __name__ == '__main__': main()
