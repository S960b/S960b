# Proto Prism (web, protobuf, RCE)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

Author: schni. Points: 50. Historical service: challs.glacierctf.com:13376. The task converts a ZIP containing a protos/ directory to an Insomnia v4 JSON export.

**Solved:** the retained remote transcript records HTTP 200 and a real gctf flag returned in the protoText field. **The participant confirms the flag was accepted by the CTF platform.** The public write-up masks the full flag in keeping with the repository convention.

The bug is argument injection through protoc response-file expansion. A ZIP member named protos/@args.proto makes protoc read the attacker-supplied root-level args.proto as command-line arguments; those arguments register /bin/sh as a protoc code-generation plugin. A 35-byte malicious proto filename turns the beginning of the binary CodeGeneratorRequest into a shell-comment polyglot. The command copies /flag into protos/service.proto, which the service later returns in its JSON export.

Read [solve.md](solve.md), [solve.ru.md](solve.ru.md), [scripts/README.md](scripts/README.md) and [evidence/README.md](evidence/README.md). Original private archive: glacierctf_proto-prism_materials.zip. No new remote exploitation was performed during publication.
