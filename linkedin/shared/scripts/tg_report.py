#!/usr/bin/env python3
"""Send a Telegram report via the Bot API (the user's own authorized bot).

Usage:
  python3 tg_report.py "text to send"
  python3 tg_report.py --file /path/to/report.txt
"""
import json
import os
import sys
import urllib.parse
import urllib.request

CREDS = os.path.expanduser('~/linkedin/.credentials')
API = 'https://api.telegram.org/bot%s/sendMessage'


def load():
    d = {}
    for line in open(CREDS, encoding='utf-8'):
        line = line.strip()
        if line and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            d[k.strip()] = v.strip()
    return d


def send(text):
    c = load()
    tok = c.get('TELEGRAM_BOT_TOKEN')
    chat = c.get('TELEGRAM_CHAT_ID')
    if not tok or not chat:
        print('ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in', CREDS)
        return 1
    if len(text) > 4000:
        text = text[:3990] + '...'
    data = urllib.parse.urlencode({
        'chat_id': chat,
        'text': text,
        'disable_web_page_preview': 'false',
    }).encode()
    req = urllib.request.Request(API % tok, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
        print('sent ok=%s message_id=%s' % (res.get('ok'), res.get('result', {}).get('message_id')))
        return 0 if res.get('ok') else 1
    except Exception as e:
        print('ERROR sending:', repr(e))
        return 1


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    if args[0] == '--file':
        text = open(args[1], encoding='utf-8').read()
    else:
        text = ' '.join(args)
    sys.exit(send(text))