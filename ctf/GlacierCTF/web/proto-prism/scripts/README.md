# Solver provenance and reproduction

The original private evidence archive is **glacierctf_proto-prism_materials.zip**.

- scripts/solve.py — the agent's reconstruction of the successful **inline** Python exploit; supports a challenge instance URL or --launch.
- scripts/local_repro.sh — preserved local one-protoc proof of command execution.
- scripts/probe_response_file.py — the @argument-file oracle, with positive/negative controls.
- scripts/pp_next.py and scripts/pp2.py — early probing and instance management.
- scripts/pp3.py and scripts/pp4.py — unsuccessful webhook and path approaches; webhook IDs are redacted.
- scripts/pp_overwrite.py and scripts/pp_plugin.py — unsuccessful overwrite and custom-plugin tests.
- assets/codegenerator_request.bin — local protobuf request captured on protoc 25.1, **not** the remote service.

The [public solver](solve.py) shows the working ZIP construction and can generate a ZIP offline. It does **not** pretend to be the byte-for-byte original final inline solver.

To make an archive offline:

    python3 solve.py --make-zip sample.zip --command 'echo RUNOK-MARKER > protos/service.proto'

The private local_repro.sh was historically tested with protoc 25.1 and 29.3; output was PWNED. No live service was accessed when publishing this write-up.
