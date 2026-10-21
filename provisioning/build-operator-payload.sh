#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTROL="$ROOT/control"
"$ROOT/provisioning/fetch-vendor.sh"
OUT="$ROOT/terraform/operator/payload/oke-rdma-control.tar.gz"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/payload/vendor"
cp -R "$CONTROL/app" "$CONTROL/web" "$CONTROL/validation" "$CONTROL/bootstrap" "$STAGE/payload/"
find "$STAGE/payload" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE/payload" -type f -name '*.pyc' -delete
# Large executables and wheels are downloaded on the operator, never embedded in ORM.
cp "$CONTROL/vendor/lock.json" "$CONTROL/vendor/README.md" \
  "$CONTROL/vendor/HELM-LICENSE" "$CONTROL/vendor/DRANET-LICENSE" \
  "$CONTROL/vendor/dranet-v1.5.0.tgz" "$STAGE/payload/vendor/"
cp "$CONTROL/requirements.lock" "$CONTROL/runtime-requirements.lock" "$STAGE/payload/"
python3 - "$STAGE/payload" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
entries = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}'
           for p in sorted(root.rglob('*')) if p.is_file()]
(root/'PAYLOAD-SHA256SUMS').write_text('\n'.join(entries)+'\n')
PY
mkdir -p "$(dirname "$OUT")"
tar -C "$STAGE/payload" -czf "$OUT" .
printf 'Wrote %s\n' "$OUT"
sha256sum "$OUT"
