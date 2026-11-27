# Working exploit provenance

The **successful complete exploit** is retained in the user's private archive `glacierctf_glacier_catch_materials.zip`, not reconstructed from guesswork.

These two files are required together:

```text
glacierctf_glacier_catch/scripts/working/glacier_catch_clone_renderer_patched.py
glacierctf_glacier_catch/scripts/working/glacier_catch_standalone_proxyfixed.py
```

The first script imports `glacier_catch_standalone` and reuses its `Asm`, `payload_input`, `run` and address constants. The second file, despite its `proxyfixed` suffix, provides those definitions. When extracting them into a **private local directory**, make the filename match the import:

```sh
cp glacier_catch_standalone_proxyfixed.py glacier_catch_standalone.py
```

The published write-up quotes offsets and logic from `glacier_catch_clone_renderer_patched.py` as the authoritative final version. The archive also retains the original received version `scripts/received/glacier_catch_clone_renderer_original.py`, fastpatch and scanner variants, failed ROMs and screenshots.

Historical invocation (do not assume that an old challenge instance is still alive):

```sh
python3 glacier_catch_clone_renderer_patched.py --url 'https://<instance>.glacier-catch.web.glacierctf.com' --delta 0x58f0 --mode marker
python3 glacier_catch_clone_renderer_patched.py --url 'https://<new-instance>.glacier-catch.web.glacierctf.com' --delta 0x58f0 --mode read-dynamic
```

The evidence-derived `0x58f0` delta must be rediscovered for a new memory layout. The original scripts require Python, `requests` and Pillow.

We do **not** publish the private bundle's `FLAG.txt`, screenshot `flag128.png`, or the full potentially identifying instance URL. The included [decode_frame.py](decode_frame.py) is a standalone offline helper for inspecting a **private** 64×32 frame.
