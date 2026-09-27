# CookieCorp - writeup (web, cookie jar overflow)

## What the task was

CookieCorp is a small web application where a normal baker can create a recipe and submit it for review.

The interesting endpoint is:

    POST /api/seal

A normal baker cannot call it directly:

    {"error":"inspector authorization required"}

The site says that ordinary inspectors can only apply the standard seal, while the CookieCorp Chief can award the Golden Seal.

The important part is the automated Inspector. When it opens a submitted recipe, the page contains `mixer.js`, which does this for every ingredient:

```js
document.cookie = ing.name + '=' + (ing.value || '') + '; path=/';
```

So the attacker controls both the **cookie name** and the **cookie value** that are written by the Inspector's browser.

The authentication cookies are initially protected with `HttpOnly`, so a simple `role=chief` ingredient is not enough from a normal baker session.

## What I tried first (and why each failed)

1. **Direct `/api/seal` with `role=chief`** - dead. The request came from my baker session and the server answered `403 inspector authorization required`.

2. **Mass assignment** - I added fields such as `role`, `isChief`, `admin`, `approved`, `goldenSeal`, `status`, etc. to the recipe request. The account still remained a baker and the batch received the standard seal.

3. **Stored XSS** - I tried to break out of the `window.__recipe` script with `</script>...`. The application escapes `<` in the JSON, so the payload is rendered as text and does not become executable HTML.

4. **Guessing individual cookie names and values** - common names such as `chief`, `inspector`, `isChief`, `goldenSeal`, `authorization`, and similar values all produced the standard seal.

So the problem was not simply finding the magic cookie name.

## The break: flooding the Inspector's cookie jar

The recipe builder allows up to 300 ingredients, and every ingredient becomes a separate cookie.

That gives us a much more interesting primitive: instead of setting one cookie, we can make the Inspector's browser create **hundreds of distinct cookies** in one visit.

The winning payload used **281 ingredients**:

- 275 generated cookie names using combinations such as `inspector_*`, `quality_*`, `chief_*`, `seal_*`, `auth_*`, `role_*`, etc.
- 6 privileged-looking cookies added at the end:

```text
role=chief
inspector=1
chief=1
quality_inspector=1
isChief=true
goldenSeal=true
```

The exact browser cookie-store behaviour is the key. The flooding changes the Inspector's cookie state, and the final privileged cookie names are processed after the large batch of low-priority cookies.

This has an important consequence: the trick works in the **automated Inspector's browser**, which has the Inspector's authentication context. Repeating `role=chief` from my own baker session did not work because that was a completely different session.

After submitting the 281-cookie recipe, the Inspector returned a **Golden Seal** instead of the standard seal.

## Exploitation

The exploit is completely automatic:

1. Register a normal baker account.
2. Build a recipe containing 281 cookie-producing ingredients.
3. Put the privileged-looking cookies at the end.
4. Submit the recipe for inspection.
5. Poll `/recipe/<id>` until the Inspector finishes.
6. Check for `GOLDEN SEAL`.

The relevant part of the script is simply:

```python
for i, name in enumerate(names[:275]):
    # generate many distinct cookies
    ingredients.append({"name": name[:48], "value": value[:64]})

for name, value in [
    ("role", "chief"),
    ("inspector", "1"),
    ("chief", "1"),
    ("quality_inspector", "1"),
    ("isChief", "true"),
    ("goldenSeal", "true"),
]:
    ingredients.append({"name": name, "value": value})
```

The resulting server-side state is enough for the Inspector's `/api/seal` request to produce:

```text
GOLDEN SEAL
```

## Result

The batch was accepted with the Chief's Golden Seal and the challenge was solved.

Flag: `sun{...}` (masked)

## One-paragraph version (for interviews)

The application lets users submit recipes, and an automated browser turns every recipe ingredient into a cookie with `document.cookie`. Direct attempts to forge the Chief role failed because the normal baker session was protected and the authentication cookies were `HttpOnly`. XSS and mass-assignment attempts also failed. The useful primitive was the 300-ingredient limit: by creating 281 distinct cookies and placing privileged-looking cookie names at the end, I could change the automated Inspector's cookie state. The Inspector then called `/api/seal` from its own authenticated browser context and the server awarded the **Golden Seal**.

## Lessons learned

1. When an application turns attacker-controlled fields into cookies, think about the browser's cookie store, not just individual cookie values.
2. A client-side primitive can be much more powerful when it runs in a privileged automated browser.
3. `HttpOnly` blocks normal JavaScript access, but it does not make the surrounding cookie-handling logic automatically safe.
4. Testing the exploit from the wrong session can be misleading: the important request here is the Inspector's request, not the attacker's baker request.
5. When a feature has a large bounded list, check whether the boundary interacts with browser or parser limits.
