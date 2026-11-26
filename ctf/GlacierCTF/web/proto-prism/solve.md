# Proto Prism — protoc response-file to RCE

[Русская версия](solve.ru.md)

## 1. Reconnaissance

The temporary service (600-second instance from nc port 13376) exposes an HTML upload form: POST /generate, multipart field bundle, and GET /example.zip. It accepts a ZIP containing protos/*.proto and returns Insomnia v4 JSON with proto_file objects and their protoText. The service source itself was **not provided**. Its behavior was established through black-box probes.

The original example.zip (336 bytes) contains protos/service.proto and uses Unix regular-file ZIP attributes. Files produced via basic Python zipfile.writestr were rejected (HTTP 400): explicitly setting create_system=3 and external_attr=(0o100644 << 16) made the uploads pass. Name length 127 was accepted; 128 rejected. Recursive proto discovery worked. Non-.proto files could be extracted without being passed to protoc.

A leading dash in protos/-x.proto behaved like a protoc option, whereas protos/sub/-x.proto did not. Consequently the service passed **unprefixed relative names as argv**, without an end-of-options sentinel. It did not use shell string concatenation: shell-metacharacter tests in filenames were negative.

## 2. The response-file pivot

The decisive probe placed a member named protos/@args.proto in the ZIP and a second file args.proto at the extraction root. Protoc's ExpandArgumentFile treats argv beginning with @ as a **response file**; it opens the rest of the name relative to its cwd, NOT its proto import path. Each line becomes one argv argument, preserving slashes and whitespace.

Thus the seemingly allowed .proto filename injects arbitrary-length compiler options stored in the root-level file:

    --plugin=protoc-gen-x=/bin/sh
    --x_out=<parameter>:.

The output-language plugin mechanism now executes /bin/sh with a binary CodeGeneratorRequest on standard input. The attack is **argument injection followed by plugin execution**; it is not an HTTP-layer shell injection.

## 3. Protobuf / shell polyglot

Calling /bin/sh directly normally fails: a CodeGeneratorRequest contains binary wire-format bytes, quotes, and descriptors. The agent saved a 764-byte local dump from protoc 25.1 to inspect the field layout.

A file_to_generate field starts with tag 0x0a (newline) followed by a varint filename length. If the first filename has length exactly **35**, its length byte is 0x23, ASCII '#'. Use the unusual name:

    #AAAAAAAAAAAAAAAAAA<newline>BBBBBBBBB.proto

This is 35 bytes. The request begins 0a 23 23 41..., so shell sees an initial newline and a '#' comment, then a controlled newline embedded in the filename. The plugin parameter inserts a command surrounded by separators and an immediate exit:

    ;echo RUNOK-MARKER > protos/service.proto;exit 0;#

That causes shell to run the command before attempting to parse subsequent binary protobuf fields. The agent reproduced this locally on protoc 25.1/29.3 using [local_repro.sh](scripts/README.md); pwned.txt contained PWNED.

## 4. Remote result

The final ZIP contains exactly these intentional members:

    args.proto
    protos/@args.proto
    protos/#AAAAAAAAAAAAAAAAAA<newline>BBBBBBBBB.proto
    protos/service.proto

First the live instance returned HTTP 200 (2,503 bytes) with service.proto protoText set to "RUNOK-MARKER\n" after the shell command echo RUNOK-MARKER > protos/service.proto. This demonstrates code execution and, crucially, that the service reads protoText **after** running protoc.

The second request replaced the command with:

    cat /flag > protos/service.proto

The response was HTTP 200; service.proto's protoText contained the real competition flag, here redacted as gctf{...}. The flag did not require an outbound webhook: it was returned over the application's ordinary JSON API.

## 5. Failed alternatives and what is known

Straight shell characters in ZIP filenames, direct compiler flags, --<lang>_prefix, custom on-disk plugin execution and descriptor-set overwrites were all unsuccessful or blocked by service checks. Filename collisions yielded consistent distinct JSON resource IDs. A webhook exfiltration attempt did not produce observable callbacks; egress status was not established.

The exact deployed protoc version and its server-side validator source were not available. The response-file behavior, 35-byte polyglot and successful in-band overwrite are supported by local tests and the recorded live marker/flag result. The local CodeGeneratorRequest sample was **not** captured from the remote process.

## 6. Evidence limitations

The original "Proto Prism Analysis" conversation ended before a flag was found. The later agent archive contains the breakthrough, reconstructed final solve.py, an unchanged example.zip and HTML form, local PWNED artifact, and [the transcribed successful run](evidence/README.md). The last successful solution was executed as an inline script and was only reconstructed into solve.py later; the complete HTTP response was not saved byte-for-byte. **The participant confirms platform acceptance of the recovered flag.** The raw submission/acceptance response was not retained with the agent archive.

The lesson: validate argv boundaries and disable response-file and plugin execution paths when invoking compilers on untrusted filenames.
