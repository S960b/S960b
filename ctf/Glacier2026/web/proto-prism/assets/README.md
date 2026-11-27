# Original Proto Prism service material

**No source-code tarball was supplied by the organizers.** The user and agent downloaded two files unchanged from the live service:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| assets/index.html | 1606 | a71f621094dbd2c5f63f3b90baf4c3ae476b7969bf3b05ed6c0fde4bb1870fb5 |
| assets/example.zip | 336 | 79eb091c644a692336275b6fe6e13de5d08e7c601a5ca5f6b53dfa68e3e4cf68 |

The ZIP example contains protos/service.proto (371 bytes), package shop.v1 and Orders gRPC service. The webpage submits multipart bundle to POST /generate.

A **locally created** analysis artifact is also preserved: assets/codegenerator_request.bin, 764 bytes, SHA-256 37bf3b17c1349f84d190e94306a637a61c8bb1f4005fd925bfcdcb695232a7ea. It is a protoc 25.1 plugin request sample, **not** an actual server dump.

The private archive retains the original binary files. No assumption is made about unprovided server code or the exact server-side protoc version.
