# Proto Prism evidence and boundaries

The participant supplied glacierctf_proto-prism_materials.zip. Its REPORT.md records verified-live [V], local [L] and assumed [A] findings.

## Positive controls

The preserved transcript records:

    POST /generate: command echo RUNOK-MARKER > protos/service.proto
    HTTP 200, 2503 bytes, protoText='RUNOK-MARKER\n'

    POST /generate: command cat /flag > protos/service.proto
    HTTP 200, protoText='gctf{...}\n'

A local protoc experiment executed the same /bin/sh-plugin polyglot and saved PWNED in evidence/local_repro_pwned.txt.

## Private artifacts and checksums

- FLAG.txt (39 bytes), SHA-256 **46cb7ad88ad856e969621a2a7b09d50f1d8c1d6260979bb7cf91aa4155b937ae** — recovered live flag.
- evidence/flag_run_transcript.txt, SHA-256 **6dbff4a89bf595e477f3eaffbd974bb3c1213eadca55d780f7a7a345db803c3b** — transcription of the final HTTP result, *not the original saved HTTP body*.
- evidence/local_repro_pwned.txt, SHA-256 **311a638d7e9b18528e9d49da77eaac7663229a724e8b503d3675ecb55b1f26df**.
- evidence/local_repro_stderr.txt, SHA-256 **bb2fc331a582a00cfe0973b6b7d295da02a7d9233f6df294fdb8b2682d2da687**.
- evidence/probe_http_log.txt, SHA-256 **b930ace5892d651ac40d56f8893f21019303924bfc9d4460f9a9d92b30fbe0b9**.

The initial chat "Proto Prism Analysis" stopped after negative/inconclusive probes. The agent's later archive demonstrates the successful response-file + /bin/sh + 35-byte-filename chain. The final successful inline script was subsequently reconstructed as scripts/solve.py. No screenshot of a platform submission exists; mark **live flag extraction as solved**, but **platform acceptance as unverified**.

Full flags, webhook tokens, ephemeral instance IDs and the raw private transcript are omitted from the public repo.
