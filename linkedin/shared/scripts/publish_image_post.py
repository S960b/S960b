#!/usr/bin/env python3
"""Publish a LinkedIn post with one image via the official API (one attempt).

Usage:
  python3 publish_image_post.py <image_path> <draft_text_path> [out_url_file] \\
      --title "<media title>" --alt "<alt text>" [--dry-run]

The draft file is used as the post text verbatim; if it is a QUEUE-style file
("POST TEXT:\\n...\\n---") only the block between those markers is used.

--title and --alt are REQUIRED on purpose: the image metadata is per-post, and a
left-over default once shipped the wrong title/altText to the API.

Image type is detected from the file content (png / jpeg / gif), not the
extension, and the same type is sent as Content-Type on upload.

Tokens are read from ~/linkedin/.credentials; the author URN comes from
GET /v2/userinfo, so no member id is hardcoded. No secrets are printed.
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

CRED = pathlib.Path('/home/kali/linkedin/.credentials')
VERSION = '202609'
API = 'https://api.linkedin.com'
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# LinkedIn little-format reserved characters (same set as publish_queued.py).
# '#' is intentionally NOT escaped so hashtags stay hashtags.
RESERVED = set('|{}@[]()<>\\*_~')

MAGIC = [
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
]


def escape_linkedin_little(text: str) -> str:
    out = []
    for ch in text:
        if ch in RESERVED:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def creds() -> dict:
    d = {}
    for line in CRED.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def detect_mime(data: bytes) -> str:
    for magic, mime in MAGIC:
        if data.startswith(magic):
            return mime
    return ''


def draft_text(path: pathlib.Path) -> str:
    raw = path.read_text()
    m = re.search(r'POST TEXT:\s*\n(.*?)\n---', raw, re.S)
    return (m.group(1) if m else raw).strip()


def api(url, token, method='GET', body=None, raw=None, content_type=None):
    headers = {'Authorization': 'Bearer ' + token,
               'X-Restli-Protocol-Version': '2.0.0',
               'LinkedIn-Version': VERSION}
    if raw is not None:
        data = raw
        headers['Content-Type'] = content_type or 'application/octet-stream'
    elif body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    else:
        data = None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read().decode(errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors='replace')
    except Exception as e:
        return 0, {}, f'EXC: {e!r}'


def main() -> int:
    ap = argparse.ArgumentParser(description='Publish one LinkedIn post with an image.')
    ap.add_argument('image')
    ap.add_argument('draft')
    ap.add_argument('out_url_file', nargs='?')
    ap.add_argument('--title', required=True, help='media title shown under the image')
    ap.add_argument('--alt', required=True, dest='alt_text', help='alt text for accessibility')
    ap.add_argument('--dry-run', action='store_true', help='print the payload, no network calls')
    a = ap.parse_args()

    img_path = pathlib.Path(a.image)
    draft_path = pathlib.Path(a.draft)
    for p in (img_path, draft_path):
        if not p.is_file():
            print(f'RESULT=NO_FILE {p}')
            return 2

    img = img_path.read_bytes()
    mime = detect_mime(img)
    if not mime:
        print(f'RESULT=UNSUPPORTED_IMAGE {img_path.name}: только png, jpeg или gif')
        return 2
    if len(img) > MAX_IMAGE_BYTES:
        print(f'WARN: image is {len(img)} bytes, above the {MAX_IMAGE_BYTES} soft limit')

    text_raw = draft_text(draft_path)
    text = escape_linkedin_little(text_raw)
    print(f'[i] image={img_path.name} type={mime} bytes={len(img)}', file=sys.stderr)
    print(f'[i] commentary raw={len(text_raw)} escaped={len(text)}', file=sys.stderr)
    print(f'[i] title={a.title!r}', file=sys.stderr)
    print(f'[i] alt={a.alt_text!r}', file=sys.stderr)
    if '\u2014' in text or '\u2013' in text:
        print('WARN: long dash found in post text')

    if a.dry_run:
        payload = {
            'author': 'urn:li:person:<member-sub>',
            'lifecycleState': 'PUBLISHED',
            'commentary': text,
            'visibility': 'PUBLIC',
            'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [],
                             'thirdPartyDistributionChannels': []},
            'isReshareDisabledByAuthor': False,
            'content': {'media': {'id': 'urn:li:image:<image-urn>',
                                  'title': a.title, 'altText': a.alt_text}},
        }
        print('COMMENTARY (final, repr):')
        print(repr(text))
        print()
        print('FULL JSON PAYLOAD:')
        print(json.dumps(payload, ensure_ascii=False))
        print()
        print('RESULT=DRY_RUN - no network call made')
        return 0

    token = creds().get('ACCESS_TOKEN')
    if not token:
        print('RESULT=NO_TOKEN')
        return 1

    st, _, body = api(f'{API}/v2/userinfo', token)
    print(f'USERINFO_HTTP={st}')
    if st != 200:
        print('BODY:', body[:400])
        print('RESULT=USERINFO_FAIL')
        return 2
    sub = json.loads(body).get('sub') or ''
    if not sub:
        print('RESULT=NO_SUB')
        return 2
    author = f'urn:li:person:{sub}'

    st, _, body = api(f'{API}/rest/images?action=initializeUpload', token, 'POST',
                      body={'initializeUploadRequest': {'owner': author}})
    print(f'INIT_UPLOAD_HTTP={st}')
    if st not in (200, 201):
        print('BODY:', body[:400])
        print('RESULT=INIT_UPLOAD_FAIL')
        return 3
    val = json.loads(body)['value']
    upload_url, image_urn = val['uploadUrl'], val['image']
    print(f'IMAGE_URN={image_urn}')

    st, _, body = api(upload_url, token, 'PUT', raw=img, content_type=mime)
    print(f'UPLOAD_HTTP={st}')
    if st not in (200, 201):
        print('BODY:', body[:300])
        print('RESULT=UPLOAD_FAIL')
        return 3

    payload = {
        'author': author,
        'lifecycleState': 'PUBLISHED',
        'commentary': text,
        'visibility': 'PUBLIC',
        'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [],
                         'thirdPartyDistributionChannels': []},
        'isReshareDisabledByAuthor': False,
        'content': {'media': {'id': image_urn, 'title': a.title, 'altText': a.alt_text}},
    }
    st, hd, body = api(f'{API}/rest/posts', token, 'POST', body=payload)
    print(f'PUBLISH_HTTP={st}')
    urn = hd.get('x-restli-id') or hd.get('X-Restli-Id') or ''
    print(f'URN={urn}')
    if st != 201:
        print('BODY:', body[:500])
        print('RESULT=PUBLISH_FAIL')
        return 4
    url = f'https://www.linkedin.com/feed/update/{urn}/'
    print(url)
    if a.out_url_file:
        pathlib.Path(a.out_url_file).write_text(url + '\n')
    print('RESULT=PUBLISHED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
