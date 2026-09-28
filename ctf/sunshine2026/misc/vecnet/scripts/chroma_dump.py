#!/usr/bin/env python3
# VecNet helper: dump the challenge ChromaDB collection once the UUID is known.
# Replace placeholders with values from your own instance.
import json, urllib.request

HOST = "<CHALLENGE-HOST>"
COLLECTION_UUID = "<COLLECTION-UUID>"
API_KEY = "<INTERNAL-API-KEY>"
BASE = f"http://{HOST}:8000/api/v2/tenants/default_tenant/databases/default_database/collections/{COLLECTION_UUID}/get"

payload = json.dumps({
    "include": ["documents", "embeddings", "metadatas", "uris"],
    "limit": 10,
}).encode()

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

print(json.dumps({k: v for k, v in data.items() if k != "embeddings"}, indent=2))
print("embeddings:", len(data.get("embeddings") or []), "vectors")
