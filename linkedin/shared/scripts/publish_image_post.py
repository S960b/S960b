#!/usr/bin/env python3
"""Publish a LinkedIn text post with one image via the official API.
Reads tokens from ~/linkedin/.credentials. No secrets are written anywhere.

Usage: python3 publish_image_post.py <image_path> <draft_text_path> [out_share_id_file]
"""
import json
import pathlib
import sys
import urllib.request

CRED = pathlib.Path('/home/kali/linkedin/.credentials')
AUTHOR_URN = 'urn:li:person:<member-sub>'  # подставить реальный sub из GET /v2/userinfo (маскировано)
VERSION = '202609'

# LinkedIn little-format reserved characters (same set as publish_queued.py).
# '#' is intentionally NOT escaped so hashtags stay hashtags.
RESERVED = set('|{}@[]()<>\\*_~')


def escape_linkedin_little(text: str) -> str:
    out = []
    for ch in text:
        if ch in RESERVED:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def creds():
    d = {}
    for line in CRED.read_text().splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            d[k.strip()] = v.strip()
    return d


def register_image(token, img_bytes):
    req = urllib.request.Request(
        'https://api.linkedin.com/rest/images?action=initializeUpload',
        data=json.dumps({'initializeUploadRequest': {'owner': AUTHOR_URN}}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'X-Restli-Protocol-Version': '2.0.0',
                 'LinkedIn-Version': VERSION, 'Content-Type': 'application/json'},
        method='POST')
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.loads(r.read())
    with urllib.request.urlopen(urllib.request.Request(d['value']['uploadUrl'], data=img_bytes,
                                                       method='PUT',
                                                       headers={'Content-Type': 'image/png'}),
                                timeout=120) as r:
        assert r.status == 201, r.status
    return d['value']['image']


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    img = pathlib.Path(sys.argv[1]).read_bytes()
    text_raw = pathlib.Path(sys.argv[2]).read_text()
    text = escape_linkedin_little(text_raw)
    print(f'[i] commentary raw={len(text_raw)} escaped={len(text)}', file=sys.stderr)
    token = creds()['ACCESS_TOKEN']

    image_urn = register_image(token, img)
    print('[i] image:', image_urn, file=sys.stderr)

    body = {
        'author': AUTHOR_URN,
        'lifecycleState': 'PUBLISHED',
        'commentary': text,
        'visibility': 'PUBLIC',
        'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [],
                         'thirdPartyDistributionChannels': []},
        'isReshareDisabledByAuthor': False,
        'content': {'media': {'id': image_urn,
                              'title': '3rd place at the CTF leaderboard',
                              'altText': 'CTF leaderboard showing our team in 3rd place with 4652 points and 38 solves'}},
    }
    req = urllib.request.Request('https://api.linkedin.com/rest/posts',
                                 data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + token,
                                          'X-Restli-Protocol-Version': '2.0.0',
                                          'LinkedIn-Version': VERSION,
                                          'Content-Type': 'application/json'},
                                 method='POST')
    with urllib.request.urlopen(req, timeout=40) as r:
        url_share = 'https://www.linkedin.com/feed/update/' + r.headers['x-restli-id'] + '/'
        print('HTTP', r.status, '->', url_share)
        if len(sys.argv) > 3:
            pathlib.Path(sys.argv[3]).write_text(url_share + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
