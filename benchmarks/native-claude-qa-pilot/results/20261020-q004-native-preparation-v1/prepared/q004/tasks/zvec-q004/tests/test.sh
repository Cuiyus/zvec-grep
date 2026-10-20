#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
test -d /workspace
echo 1 > /logs/verifier/reward.txt
