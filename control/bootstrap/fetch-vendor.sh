#!/usr/bin/env bash
# Fetch and verify the pinned Linux x86_64 operator build inputs.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENDOR="$ROOT/vendor"
verify_file() {
  python3 - "$VENDOR" "$1" <<'PY'
import hashlib, json, pathlib, sys
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = json.loads((root / 'lock.json').read_text())['files'][name]
p = root / name
sys.exit(0 if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == expected else 1)
PY
}
if verify_file helm && verify_file dranet-v1.5.0.tgz; then
  printf '%s\n' 'Pinned vendor inputs verified.'
  exit 0
fi
if [[ "$(uname -s)-$(uname -m)" != Linux-x86_64 ]]; then
  echo 'Build on Linux x86_64 (a Linux x86_64 container is also suitable).' >&2
  exit 1
fi
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
if ! verify_file helm; then
  curl -fSL --retry 5 --retry-delay 2 --retry-max-time 300 --connect-timeout 15 --max-time 180 \
    https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz -o "$STAGE/helm.tar.gz"
  tar -xzf "$STAGE/helm.tar.gz" -C "$STAGE" linux-amd64/helm
  python3 - "$VENDOR/lock.json" "$STAGE/linux-amd64/helm" <<'PY'
import hashlib, json, pathlib, sys
expected = json.loads(pathlib.Path(sys.argv[1]).read_text())['files']['helm']
if hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest() != expected:
    raise SystemExit('Helm checksum mismatch')
PY
  install -m 0755 "$STAGE/linux-amd64/helm" "$VENDOR/helm"
fi
if ! verify_file dranet-v1.5.0.tgz; then
  "$VENDOR/helm" pull oci://registry.k8s.io/networking/charts/dranet \
    --version v1.5.0 --destination "$STAGE"
  python3 - "$VENDOR/lock.json" "$STAGE/dranet-v1.5.0.tgz" <<'PY'
import hashlib, json, pathlib, sys
expected = json.loads(pathlib.Path(sys.argv[1]).read_text())['files']['dranet-v1.5.0.tgz']
if hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest() != expected:
    raise SystemExit('Dranet chart checksum mismatch')
PY
  install -m 0644 "$STAGE/dranet-v1.5.0.tgz" "$VENDOR/dranet-v1.5.0.tgz"
fi
verify_file helm
verify_file dranet-v1.5.0.tgz
printf '%s\n' 'Pinned vendor inputs verified.'
