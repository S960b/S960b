#!/usr/bin/env bash
# Put the original flynative example into the ORIGINAL model-src crate; no changes
# to engine.rs, agent.rs, features.rs, model.rs, or the model.fly checkpoint.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRATE="${1:?Usage: ./build_native.sh /path/to/handout/model-src}"
mkdir -p "$CRATE/examples"
cp "$HERE/flynative.rs" "$CRATE/examples/flynative.rs"
(cd "$CRATE" && cargo build --release --example flynative)
echo "Native executable: $CRATE/target/release/examples/flynative"
