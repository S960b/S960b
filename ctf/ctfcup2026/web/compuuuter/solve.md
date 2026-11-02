# compuuuter: a graph that changes between check and use

[По-русски](solve.ru.md)

## 0. The target

A Flask service holds numeric dataflow graphs in memory. A node has an `id`, a `type` and `args`; constants also carry a `value`. You upload a graph, patch single nodes, run `compute` and read the per-node results.

Hidden in the operation table is `T_FLAG = 1337`, whose output is the flag bytes read as a little-endian integer:

```python
FLAG = _load_flag()
FLAG_INT = int.from_bytes(FLAG.encode(), "little")
```

`/api/ops` does not list it, and both upload and compute reject graphs that contain it. So the task is to make the evaluator see `type == 1337` on a node that passed the check.

Why this step: the flag is not a file to read but a value the evaluator can produce, so the goal is to reach that branch.

## 1. Where the check happens

```python
def guard(nodes):
    for node in nodes:
        if node["type"] == T_FLAG:
            raise GraphError("the flag operation (type 1337) is not available")

def compute(nodes):
    guard(nodes)                      # time of check
    nmap = {}
    for node in nodes:
        nmap[node["id"]] = node       # index of the same objects
    ...
        t = node["type"]              # time of use
        if t == T_FLAG:
            val = FLAG_INT
```

Two properties make this exploitable. The node dictionaries are not copied or frozen, so `nmap` holds references to the live objects. And the type is read again much later, inside `evaluate`.

The patch route completes the picture:

```python
patch = parse_node(body)              # no guard() here
for node in graph["nodes"]:
    if node["id"] == patch["id"]:
        node["type"] = patch["type"]  # in place, same object
        node["args"] = patch["args"]
        break
```

`PATCH` performs no flag check, the app runs with `threaded=True`, and `GRAPHS_LOCK` only protects inserting a new graph, not using an existing node list. So one request can be inside `compute` while another rewrites a node.

Why this step: the whole vulnerability is the missing freeze between check and use, not a parsing bug.

## 2. Making the window usable

A few microseconds of race is not something you win over HTTP. The server helps:

```python
nmap = {}
for node in nodes:
    nmap[node["id"]] = node
```

and there is an assertion that betrays the intended trick:

```python
assert sys.hash_info.modulus == 2 ** 61 - 1
```

On 64-bit CPython, `hash(n)` for a non-negative integer is `n mod (2^61 - 1)`. So ids that are multiples of `2^61 - 1` all have the same hash:

```python
MOD = (1 << 61) - 1
ids = [i * MOD for i in range(1, count + 1)]
```

The ids are distinct, so the duplicate-id check passes, but inserting them into a dict degenerates: every key lands in the same slot and the insertion cost climbs steeply. That turns the dictionary build between `guard` and `evaluate` into a delay long enough to hit over the network.

The target node gets `id = 1` and is appended last, so evaluation reaches it after all the filler. The filler nodes themselves are ordinary constants.

Why this step: the collision amplification converts a microsecond race into a controllable pause.

## 3. The race

1. Upload a graph of `count` colliding constant nodes plus the target node with `id = 1, type = 1`.
2. Start `POST /api/graph/<gid>/compute` in a thread.
3. Sleep `delay`, then `PATCH` node `1` to `type = 1337`.
4. If the patch lands after `guard` but before `evaluate` reads the type, the result for `"1"` is `FLAG_INT`.
5. Decode it: `value.to_bytes((value.bit_length() + 7) // 8, "little")`.

Between attempts the node is patched back to a legal type, otherwise the next `compute` fails its own guard. That is the timing discipline the script implements: reset, start compute, sleep, patch, read.

Why this step: the patch alone is useless, the graph must be legal when the guard runs and forbidden when the evaluator reads it.

## 4. Sizing the graph

More colliders means a longer window, but also a slower upload: `parse_nodes` builds a `seen` set for duplicate detection, and that set suffers from the same collision problem. A 12 001-node graph is visibly slow before the race even starts, and on one instance the upload itself was enough to have the connection dropped.

The run recorded on 2 November 2026 used 6 001 nodes and a 0.3 second delay, and the first attempt succeeded:

```text
[*] Uploading 6001 nodes ...
[*] Graph id: ae1597a76f254296bb3e08e3ca9bce26
[*] Attempt 1: compute -> wait 0.3s -> PATCH(type=1337)
[+] Decoded output: ctfcup{...}
[FLAG] ctfcup{...}
```

The flag is masked here. Note that a guard error means the patch arrived too early, and a normal numeric result means it arrived too late; both are diagnosed by the same script.

Why this step: the graph size is the tuning knob for both phases, and one delay value is not universal.

## 5. Reading the result

`results["1"]` is a decimal string of the flag bytes as a little-endian integer, not a hash and not hex. Reversing the server's own conversion gives the text.

```bash
python3 scripts/exploit_compuuuter.py https://<host> --count 6000 --delay 0.3
```

The script resets the target, races, decodes and retries; the raw transcript is in `evidence/compuuuter_run.log`.

## 6. Dead ends

- Uploading a node with `type = 1337` directly: rejected at upload and again at compute.
- Patching to `1337` and then calling compute: the next guard rejects it, because by then the graph is illegal at check time.
- Using a huge graph: the upload path is quadratic as well, so the connection may die before the race begins.
- Local testing against the placeholder: it proves the evaluator branch, not the remote flag, because `_load_flag` falls back to a test string.

## 7. Lessons

- Time-of-check to time-of-use is about object identity: a dict of references is not a snapshot.
- Hash collisions are a timing primitive: they let an attacker buy a window without touching the network stack.
- When a race needs a window, look for work the server does between the check and the use on attacker-controlled data.

One paragraph: `compute` validates node types and later evaluates the same mutable dictionaries, while `PATCH` rewrites a node in place without any flag check, so a concurrent patch can turn a constant node into opcode 1337 after the guard passed; thousands of ids that collide under CPython's `n mod (2^61 - 1)` hashing make the dictionary build slow enough to hit the window, and the resulting `FLAG_INT` decodes back to the flag.
