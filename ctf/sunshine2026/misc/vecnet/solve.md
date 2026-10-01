# VecNet - writeup (exposed Git, ChromaDB, and embedding inversion)

## What the task was

The challenge presented a landing page for "VecNet", a fake product that uses AI embeddings for mail security. The page advertised a restricted webmail service and talked a lot about 768-dimensional embeddings. That wording mattered: the real secret was not stored as normal text, but as a vector embedding.

At a high level, the chain was:

1. exposed `.git` on the public web server;
2. recovered old commits and a reverted `config.php`;
3. used the recovered webmail credentials to read internal emails;
4. used the emails and config to reach the ChromaDB API;
5. dumped three ChromaDB records;
6. inverted the embedding-only record with `vec2text`;
7. brute-forced the exact archive password from the recovered password rule and SHA256 hash;
8. opened `specs.7z` and read the flag.

The flag is masked here: `sun{k33p...secur3!}`.

## First look

The landing page was static HTML. The only interesting visible link was a webmail endpoint on a separate port. Direct access asked for HTTP Basic authentication, so there was no obvious way in from the page itself.

I checked common web paths before brute-forcing anything. Most paths returned 404, but one stood out:

    /.git/config -> 200

That meant the deployed Git repository was exposed.

## Recovering the repository

The Git endpoint was not a smart HTTP Git server, so a normal `git clone` was not the right approach. The useful files were still available as raw `.git` objects:

    /.git/HEAD
    /.git/refs/heads/master
    /.git/index
    /.git/objects/<two hex chars>/<rest of sha>
    /.git/logs/HEAD

The index showed the current tracked files:

    .gitignore
    .htaccess
    fetch.php
    index.html

The current `fetch.php` was already useful. It contained a very specific internal URL and archive path:

    const ALLOWED_URL = 'http://localhost/files/specs.7z';
    const ARCHIVE_PATH = '/var/www/html/files/specs.7z';

The `.htaccess` denied direct access to `specs.7z`, but `fetch.php` would serve it only if the `url` parameter exactly matched the internal URL. So the archive could be downloaded through the public endpoint:

    /fetch.php?url=http://localhost/files/specs.7z

That produced a tiny 7z archive. Listing the archive revealed a single file, `flag.txt`, but extraction required a password.

## Git history mattered

The repository logs were more important than the current tree:

    initial site deploy
    add embed preview endpoint
    add internal service config
    REVERT: do not commit secrets
    add htaccess

The reverted commit contained `config.php`. That file leaked:

- the local mail admin URL;
- a webmail username/password;
- an internal API key.

I do not include the full secrets here; they were only challenge credentials. The important point is that the secret was committed, reverted, and still recoverable from Git history.

## Reading webmail

With the recovered Basic-auth credentials, the webmail API exposed three messages. They explained the challenge direction:

- VecNet was using a ChromaDB vector database;
- the team believed embeddings were hard to de-obfuscate;
- an email linked the encrypted `specs.7z` archive;
- another email gave vec2text settings:

    num_steps=4
    sequence_beam_width=5

This confirmed that the archive password was probably hidden in an embedding and that the intended technique was embedding inversion.

## Dead ends and wrong turns

1. **Opening the archive with obvious passwords** - I tried the product name, usernames, webmail password, API key fragments, `vec2text`, `chromadb`, and other visible strings. None opened the archive. The archive was not a simple "use leaked password as 7z password" task.

2. **Direct `/files/specs.7z` access** - the file path existed, but `.htaccess` restricted direct access to local requests. The public way in was `fetch.php` with the exact `url=http://localhost/files/specs.7z` parameter recovered from Git.

3. **ChromaDB v1 paths** - requests to `/api/v1/heartbeat`, `/api/v1/collections`, and similar routes returned `route not allowed`. The service was not a raw open ChromaDB API at those paths; it was behind a small PHP/router allowlist.

4. **Collection name instead of UUID** - `GET /collections/VecNetDB` returned metadata, but `POST /collections/VecNetDB/get` failed. The working Chroma endpoint used the collection UUID from the metadata response.

5. **Zero-step embedding inversion** - quick inversion produced noisy text. It was enough to prove the embedding space was right, but not enough for the final password rule. The email settings (`num_steps=4`, `sequence_beam_width=5`) were needed for a useful result.

## Dumping ChromaDB

Once the API shape was understood, the useful call was a POST to the collection UUID's `/get` endpoint with documents, embeddings, and metadata included (`scripts/chroma_dump.py`; pass `--out vec_get.json` to keep the vectors - they are needed for the next step and are omitted from the console summary for readability).

The collection contained three records:

    user_password_requirements  -> document: null, embedding_only
    user_hash_sha256            -> document: d8dd241199d2617765d7613fdd1df5358297b55f258647fe463de586bbfe3ebf
    magic_string                -> document: sunshinectf8_

The `magic_string` and hash were plaintext. The actual password rule was stored as only a 768-dimensional embedding.

A quick sanity check was important: embedding the known plaintext `sunshinectf8_` locally and comparing it to the stored `magic_string` vector gave cosine similarity `1.0000`. That confirmed the local model matched the target embedding model.

The instance is gone now, but the three vectors are shipped in `assets/`
(`embeddings.npy` + `ids.txt` + `documents.json`, same order as the `/get`
response), so the rest of the chain is fully reproducible offline.

## Inverting the embedding

I used `vec2text` with a GTR-base corrector and the settings from the email:

    num_steps=4
    sequence_beam_width=5

`scripts/invert_embedding.py` does both the sanity check and the inversion:

```bash
pip install vec2text==0.0.13 torch numpy
python3 scripts/invert_embedding.py assets/ --steps 4 --beam 5
```

Observed output (the 4-step run from the solving session took ~1-2 minutes
on CPU, depending on the machine):

    cos(embed('sunshinectf8_'), magic_string vec) = 1.0000
    [user_password_requirements] steps=4 beam=5 (119s):
        The user's first and last initials, three special characters
        followed by the magic string.

(A zero-step run, `--steps 0`, already proves the embedding space is right but
returns noisier text: `"The first three characters, the user's special string,
and the last three digits, followed by the user's magic string string
identification."`)

So the password format was:

    <initials><three-special-characters>sunshinectf8_

The likely initials came from the emails and Git history. The final person was Greg Roberts, so `GR` was a natural candidate, but I still let the script search initials and special-character triples against the SHA256 hash.

The hash check (`scripts/brute_password.py`) recovered the archive password pattern. In the public writeup I keep the exact password masked, but the local helper script can recompute it from the hash.

## Extracting the flag

With the recovered password, the archive test succeeded:

    7z t -p'<recovered-password>' specs.7z

Then extraction produced `flag.txt`. The flag was:

    sun{k33p...secur3!}

(masked for the portfolio)

## One-paragraph version (for interviews)

The site exposed its `.git` directory. I recovered the Git index and object history, found a reverted `config.php` with webmail/API secrets, and used the current `fetch.php` to download an internal encrypted `specs.7z` archive. Webmail messages pointed to ChromaDB and vec2text settings. The Chroma collection contained a plaintext SHA256 hash, a plaintext magic string, and an embedding-only password rule. After confirming the embedding model with the known magic string, I inverted the password-rule vector with `vec2text` and got a human-readable rule: initials + three special characters + magic string. A local SHA256 brute-force recovered the archive password, and the archive contained the flag.

## Lessons learned

1. Reverted Git commits still count as leaked secrets if `.git` is exposed.
2. `.git/index` is enough to discover filenames even when directory listing is disabled.
3. Access controls can be path-specific: direct `/files/specs.7z` was blocked, but the intended `fetch.php` endpoint served it.
4. Vector embeddings are not anonymization. If the model is known or recoverable, sensitive text can sometimes be reconstructed.
5. Always validate the embedding space with a known plaintext vector before trusting inversion output.
6. Use hashes to turn fuzzy NLP output into an exact password: the embedding gives the rule, the SHA256 check gives certainty.
