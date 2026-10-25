#!/usr/bin/env python3
"""CTF litter: decode exact UTF-8 subtitles in json3 to hidden hints, no OCR."""
from pathlib import Path
import json, re, sys, zipfile
from io import TextIOWrapper

def load(path: Path):
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith('pudding.en.json3'):
                    return json.loads(z.read(name))
        raise ValueError('pudding.en.json3 not found in ZIP')
    return json.loads(path.read_text(encoding='utf-8'))

def frombits(bitstring):
    assert len(bitstring) % 8 == 0
    return bytes(int(bitstring[i:i+8], 2) for i in range(0, len(bitstring), 8)).decode('utf-8')

def main():
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('pudding_subtitles.zip')
    data = load(p)
    evs = data['events']
    text = lambda e: ''.join(seg.get('utf8', '') for seg in e.get('segs', []))
    bits = ''.join(re.findall(r'[01]', text(e))[0] for e in evs if re.search(r'[01]', text(e)))
    assert len(bits) == 168
    hidden = frombits(bits)
    extra = [e for e in evs if text(e).endswith(('X','O'))]
    assert len(extra) == 432
    extra.sort(key=lambda e: data['wpWinPositions'][e['wpWinPosId']]['ahHorPos'])
    positions = [data['wpWinPositions'][e['wpWinPosId']]['ahHorPos'] for e in extra]
    assert positions == list(range(1000,433000,1000)), 'coordinates do not follow expected grid'
    b2 = ''.join('1' if text(e).endswith('X') else '0' for e in extra)
    target = frombits(b2)
    print('Image digits:', len(bits), 'bits ->', hidden)
    print('Outside X/O:', len(b2), 'bits ->', target)
    if hidden != 'Think outside the box': raise ValueError('Unexpected message: check input')
    if not target.startswith('<host>'): raise ValueError('Unexpected URL: check input')
    Path('litter_hidden_messages.txt').write_text('Message: '+hidden+'\nURL: '+target+'\n', encoding='utf-8')
    print('Wrote litter_hidden_messages.txt')

if __name__ == '__main__': main()
