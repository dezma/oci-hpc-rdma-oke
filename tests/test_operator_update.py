"""Check update integrity and idle preconditions without touching system services."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=(ROOT/'provisioning/update-operator.sh').read_text()

def block(name):
    return re.search(r"<<'"+name+r"'\n(.*?)\n"+name+r"\n",SCRIPT,re.S).group(1)

def test_installer_checks_source_and_installed_files(tmp_path):
    subprocess.run(['bash','-n',str(ROOT/'provisioning/update-operator.sh')],check=True)
    source=tmp_path/'control';source.mkdir()
    (tmp_path/'provisioning').mkdir()
    manifest=ROOT/'provisioning/operator-update.json'
    shutil.copy2(manifest,tmp_path/'provisioning/operator-update.json')
    installed=tmp_path/'installed';installed.mkdir()
    for name in json.loads(manifest.read_text())['replacement']:
        for dest in [source/name,installed/name]:
            dest.parent.mkdir(exist_ok=True);shutil.copy2(ROOT/'control'/name,dest)
    def run():return subprocess.run([sys.executable,'-',str(installed),str(source)],input=block('CHECK'),text=True,capture_output=True)
    assert run().returncode==0
    (installed/'app/capacity.py').write_text('unexpected code')
    assert 'Installed file differs' in run().stderr
    shutil.copy2(ROOT/'control/app/capacity.py',installed/'app/capacity.py')
    (source/'app/capacity.py').write_text('unexpected replacement')
    assert 'checksum mismatch' in run().stderr

def test_installer_keeps_pending_and_active_job_guards(tmp_path):
    state=tmp_path/'state';state.mkdir()
    conn=sqlite3.connect(state/'control.db');conn.execute('CREATE TABLE jobs(id TEXT,state TEXT)');conn.commit()
    code=block('IDLE').replace('/var/lib/oke-rdma-control',str(state))
    def run():return subprocess.run([sys.executable,'-'],input=code,text=True,capture_output=True)
    assert run().returncode==0
    journal=state/'capacity-operation.json';journal.write_text(json.dumps({'phase':'APPLY_RUNNING'}))
    assert 'capacity operation is pending' in run().stderr
    journal.write_text(json.dumps({'phase':'CLOSED_FAILED'}));assert run().returncode==0
    conn.execute("INSERT INTO jobs VALUES('job','RUNNING')");conn.commit()
    assert 'operation is active' in run().stderr
    conn.close()
