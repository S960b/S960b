#!/usr/bin/env python3
"""Read-only audit of the exact already-registered Stage 33F plugin.

The selected static JS code excerpts contain no DB secrets. Do not inspect or
print process environments, log bodies, runtime reservations, credentials.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

PATH=Path('/home/kali/hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs')
CONFIG=Path('/home/kali/.config/gigatool/opencode.json')
EXPECTED_PLUGIN='3535ae7bd5bb0da3e8574afcab299118dac744bc851c4bcb90b8656acd8b3ea7'
EXPECTED_CFG='a8f11a25173b5e7f1290d97b4aedfaea24403256fa09c16144093e0f9d297cb3'

def safe_regular(p):
    s=p.lstat()
    return stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and not p.is_symlink()

def inspect():
    if os.geteuid()==0:raise RuntimeError('ROOT_NOT_ALLOWED')
    if not safe_regular(PATH) or not safe_regular(CONFIG):raise RuntimeError('SOURCE_UNTRUSTED')
    raw=PATH.read_bytes(); cfg_raw=CONFIG.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=EXPECTED_PLUGIN:raise RuntimeError('PLUGIN_HASH_CHANGED')
    if hashlib.sha256(cfg_raw).hexdigest()!=EXPECTED_CFG:raise RuntimeError('CONFIG_HASH_CHANGED')
    cfg=json.loads(cfg_raw)
    entries=cfg['plugin']
    if len(entries)!=7:raise RuntimeError('PLUGIN_COUNT_CHANGED')
    names=[e if isinstance(e,str) else e[0] for e in entries]
    if names.count(str(PATH))!=1 or any('mt-git-inbox.mjs' in n for n in names):
        raise RuntimeError('LIVE_PLUGIN_OWNERSHIP_CHANGED')
    lines=raw.decode('utf-8').splitlines()
    markers={
        'dispatch':'async function dispatch(',
        'reconcile':'async function reconcileAnsweredInflight(',
        'runTick':'export async function runTick(',
        'claim':'op:"claim"',
        'ack':'op:"ack"',
        'reservation':'state.inflight[key]',
        'confirmedAnswer':'function confirmedMtResponseAfter('
    }
    indexes={}
    for tag,needle in markers.items():
        hits=[i for i,line in enumerate(lines) if needle in line.replace(' ', '')] if tag in ('claim','ack') else [i for i,line in enumerate(lines) if needle in line]
        if not hits:raise RuntimeError('MISSING_'+tag.upper())
        indexes[tag]=hits
    blocks=[]
    # Relevant code excerpts only. They are static source, not real conversations.
    for tag in ('dispatch','reconcile','claim','ack','reservation','confirmedAnswer'):
        i=indexes[tag][0]
        n=95 if tag=='reconcile' else 32 if tag=='dispatch' else 15
        lo=max(0,i-(3 if tag not in ('dispatch','reconcile') else 0))
        hi=min(len(lines),i+n)
        blocks.append({'name':tag,'start_line':lo+1,'lines':[f'{j+1}: {lines[j][:280]}' for j in range(lo,hi)]})
    return {'stage':'33H','candidate_sha256':EXPECTED_PLUGIN,'live_config_sha256':EXPECTED_CFG,
      'sdk_or_pg_write_attempted':False,'critical_marker_lines':{k:[i+1 for i in v] for k,v in indexes.items()},
      'source_excerpts':blocks}

def main():
    try:
        out=inspect()
        print(json.dumps(out,ensure_ascii=False,indent=2))
        print('STAGE33H_PINNED_PLUGIN_SOURCE_INVENTORY_OK')
    except Exception as e:
        print('STAGE33H_STOP_SOURCE_INVENTORY_'+str(e).split(':')[0])
        raise SystemExit(2)
if __name__=='__main__':main()
