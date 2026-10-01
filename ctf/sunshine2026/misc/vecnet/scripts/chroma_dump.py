#!/usr/bin/env python3
"""VecNet helper: dump the challenge ChromaDB collection once the UUID is known.

Replace the placeholders with values from your own instance.

The full response (documents + embeddings + metadatas) is the input for
`invert_embedding.py`, so pass `--out` to save it (embeddings are 768 floats
per record and are omitted from the console summary for readability).

Examples:
    python3 chroma_dump.py                       # summary only
    python3 chroma_dump.py --out vec_get.json    # full dump, incl. vectors

Instead of editing the placeholders you can point the script at an instance
with env vars: CHROMA_BASE, CHROMA_HOST, CHROMA_UUID, CHROMA_API_KEY.
"""
import argparse
import json
import os
import sys
import urllib.request

HOST = os.environ.get("CHROMA_HOST", "<CHALLENGE-HOST>")
COLLECTION_UUID = os.environ.get("CHROMA_UUID", "<COLLECTION-UUID>")
API_KEY = os.environ.get("CHROMA_API_KEY", "<INTERNAL-API-KEY>")
BASE = os.environ.get(
    "CHROMA_BASE",
    f"http://{HOST}:8000/api/v2/tenants/default_tenant/databases/default_database/collections/{COLLECTION_UUID}/get",
)

payload = json.dumps({
    "include": ["documents", "embeddings", "metadatas", "uris"],
    "limit": 10,
}).encode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", metavar="FILE", default=None,
                    help="write the full JSON response (with embeddings) to FILE")
    args = ap.parse_args()

    req = urllib.request.Request(
        BASE,
        data=payload,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "X-Chroma-Token": API_KEY,
        },
    )

    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())

    ids = data.get("ids", [])
    docs = data.get("documents") or [None] * len(ids)
    n_emb = len(data.get("embeddings") or [])

    if args.out:
        with open(args.out, "w") as f:
            json.dump(data, f)
        print(f"[+] full dump (ids, documents, metadatas, {n_emb} embeddings) -> {args.out}")
        print(f"[+] feed it to invert_embedding.py:  python3 invert_embedding.py {args.out}")
    else:
        for i, d in zip(ids, docs):
            print(f"  {i}: document={d!r}")
        print(f"embeddings: {n_emb} vectors (use --out FILE to save them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())