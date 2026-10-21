#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
"$ROOT/provisioning/build-operator-payload.sh"
python3 "$ROOT/provisioning/package-release.py" --mode orm
