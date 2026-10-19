#!/bin/sh
# Also run this in --network=none --read-only with a writable /tmp to verify
# that cached local embeddings never need to download or mutate the model cache.
set -eu
smoke_root="$(mktemp -d /tmp/zg-local-smoke.XXXXXX)"
trap 'rm -rf "$smoke_root"' EXIT
export HOME="$smoke_root/home" ZVEC_GREP_HOME="$smoke_root/state"
mkdir -p "$HOME" "$smoke_root/source"
printf 'def calculate_invoice_total(items):\n    return sum(item.price for item in items)\n' > "$smoke_root/source/invoice.py"
zg --index "$smoke_root/source" --mode direct --embedding local/potion-code-16m-v2
cd "$smoke_root/source"
zg --vector 'calculate the total invoice price' --limit 1 --mode direct --preview short > "$smoke_root/result.txt"
cat "$smoke_root/result.txt"
grep -q 'invoice.py' "$smoke_root/result.txt"
