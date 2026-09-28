# Used Goods of Tomorrow — GraphQL introspection + hidden promo code

- **Platform:** SunshineCTF 2026
- **Category:** Web
- **Date:** 2026
- **Link:** `<challenge-url>` (challenge instance, closed after the event)
- **Flag:** `sun{...}` (masked)
- **Endpoint:** `POST /graphql`

"Used Goods of Tomorrow" is a GraphQL-backed storefront ("Tomorrow-Mart"). Every new scout gets 500 starter credits. The goal is to buy the crown jewel, **Lot #4042 — Founders' Vault Deed**, which costs 1,000,000 credits.

The whole chain is a GraphQL enumeration:

1. Genuine schema introspection reveals a hidden `vendorTerminalSync` mutation.
2. Calling it leaks a master **vendor key**.
3. The master key unlocks an internal `promoCodes` query that exposes a promo code for **100% off Lot #4042**.
4. Register an account and place the order with that promo code.

Files:

- `solve.md` - the full write-up, including the dead ends and the exact GraphQL queries
- `scripts/solve.py` - a working solver