#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
mkdir -p build
clang -target armv6m-none-eabi -c solution/yaju_payload.S -o build/yaju_payload.o
clang -target armv6m-none-eabi -fuse-ld=lld -nostdlib \
  -Wl,-T,solution/yaju.ld build/yaju_payload.o -o build/yaju_payload.elf
llvm-objcopy -O binary build/yaju_payload.elf build/rebuilt_yaju_payload.bin
cmp solution/yaju_payload.bin build/rebuilt_yaju_payload.bin
echo 'REBUILD_EXACT_MATCH'
