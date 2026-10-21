#!/usr/bin/env bash
# Install the reviewed Control Center application without changing cluster resources.
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then echo 'Run with sudo bash provisioning/update-operator.sh' >&2; exit 1; fi
SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../control" && pwd)
APP_DIR=/opt/oke-rdma-control

python3.11 - "$APP_DIR" "$SOURCE_DIR" <<'CHECK'
import hashlib,json,pathlib,sys
root,source=map(pathlib.Path,sys.argv[1:])
manifest=json.loads((source.parent/'provisioning/operator-update.json').read_text())
expected=manifest['installed']
for relative,wanted in manifest['replacement'].items():
    if hashlib.sha256((source/relative).read_bytes()).hexdigest()!=wanted:
        raise SystemExit('Update source checksum mismatch: '+relative)
for relative,allowed in expected.items():
    p=root/relative
    if p.is_symlink():raise SystemExit('Installed path is a symbolic link: '+relative)
    if not p.exists() and None in allowed:continue
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() not in allowed:
        raise SystemExit('Installed file differs from the supported release: '+relative)
CHECK
# Exclude the optional scheduler while checking idle state and installing.
mkdir -p /var/lib/oke-rdma-control/monitoring
chown opc:opc /var/lib/oke-rdma-control/monitoring
exec 9>/var/lib/oke-rdma-control/monitoring/scheduler.lock
chown opc:opc /var/lib/oke-rdma-control/monitoring/scheduler.lock
flock -x 9
systemctl is-active --quiet oke-rdma-control-api.service
systemctl is-active --quiet oke-rdma-control-worker.service
BACKUP_DIR=''
ROLLBACK=0
restore_services() {
  result=$?
  trap - EXIT
  if [[ "$result" -ne 0 && "$ROLLBACK" -eq 1 ]]; then
    systemctl stop oke-rdma-control-api.service oke-rdma-control-worker.service || true
    cp -a "$BACKUP_DIR/files/." "$APP_DIR/"
    python3.11 - "$APP_DIR" "$BACKUP_DIR" <<'RESTORE'
import json,pathlib,sys
root,backup=map(pathlib.Path,sys.argv[1:])
for relative in json.loads((backup/'created.json').read_text()):
    (root/relative).unlink(missing_ok=True)
RESTORE
    echo "Application restored from $BACKUP_DIR" >&2
  fi
  systemctl start oke-rdma-control-worker.service oke-rdma-control-api.service
  exit "$result"
}
trap restore_services EXIT
systemctl stop oke-rdma-control-api.service
python3.11 - <<'IDLE'
import json,pathlib,sqlite3
pending=pathlib.Path('/var/lib/oke-rdma-control/capacity-operation.json')
if pending.exists() and json.loads(pending.read_text()).get('phase') not in {'COMPLETE','DISCARDED','CLOSED_FAILED'}:
    raise SystemExit('A capacity operation is pending. Acknowledge a terminal failed Apply in the UI first; otherwise finish or discard the recorded operation before updating. Do not delete its journal.')
for record in pathlib.Path('/var/lib/oke-rdma-control/recovery').glob('attempt-*.json'):
    if json.loads(record.read_text()).get('phase') != 'COMPLETE':
        raise SystemExit('Finish the recorded RDMA recovery before updating.')
replacement=pathlib.Path('/var/lib/oke-rdma-control/replacement/operation.json')
if replacement.exists() and json.loads(replacement.read_text()).get('phase') not in {'COMPLETE','CLOSED'}:
    raise SystemExit('A worker replacement is pending. Resume or safely close it before updating the application.')
connection=sqlite3.connect('file:/var/lib/oke-rdma-control/control.db?mode=ro',uri=True)
active=connection.execute("SELECT id,state FROM jobs WHERE state IN ('QUEUED','RUNNING') LIMIT 1").fetchone()
connection.close()
if active: raise SystemExit('An operation is active. Let it finish and run the update again. Services will resume automatically.')
IDLE
systemctl stop oke-rdma-control-worker.service
BACKUP_DIR=$(mktemp -d /var/lib/oke-rdma-control/application-backup.XXXXXXXX)
export BACKUP_DIR
python3.11 - "$APP_DIR" "$SOURCE_DIR" <<'INSTALL'
import json,os,pathlib,py_compile,shutil,sqlite3,sys
root,source=map(pathlib.Path,sys.argv[1:]);backup=pathlib.Path(os.environ['BACKUP_DIR'])
files=json.loads((source.parent/'provisioning/operator-update.json').read_text())['installed']
# Back up the durable queue using SQLite's consistent snapshot API.
src=sqlite3.connect('file:/var/lib/oke-rdma-control/control.db?mode=ro',uri=True)
dst=sqlite3.connect(backup/'control.db');src.backup(dst);dst.close();src.close()
created=[]
for relative in files:
    original=root/relative
    if not original.exists():created.append(relative);continue
    p=backup/'files'/relative;p.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(original,p)
    metadata=original.stat();os.chown(p,metadata.st_uid,metadata.st_gid)
(backup/'created.json').write_text(json.dumps(created))
# Validate Python before replacing any installed files.
for relative in files:
    if relative.endswith('.py'):py_compile.compile(str(source/relative),doraise=True)
INSTALL
ROLLBACK=1
python3.11 - "$APP_DIR" "$SOURCE_DIR" <<'COPY'
import json,pathlib,shutil,sys
root,source=map(pathlib.Path,sys.argv[1:])
for relative in json.loads((source.parent/'provisioning/operator-update.json').read_text())['installed']:
    target=root/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source/relative,target)
    shutil.chown(target,user='opc',group='opc')
COPY
systemctl start oke-rdma-control-worker.service oke-rdma-control-api.service
python3.11 - <<'HEALTH'
import time,urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2) as response:
            if response.status==200:break
    except OSError:time.sleep(1)
else:raise SystemExit('API did not become healthy; restoring application files.')
HEALTH
systemctl is-active --quiet oke-rdma-control-worker.service
ROLLBACK=0
trap - EXIT
printf 'Control Center v2.10.0 installed. Backup: %s\n' "$BACKUP_DIR"
printf '%s\n' 'Hard-refresh the browser, inspect capacity counts, and check prerequisites for a fresh reconciliation plan.'
