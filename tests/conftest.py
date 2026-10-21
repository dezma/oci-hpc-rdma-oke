import os
from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'control'))
os.environ['HPC_CONTROL_DB'] = str(ROOT/'.test-state/control.db')
os.environ['HPC_CONTROL_RDMA_CONFIG'] = str(ROOT/'.test-state/rdma.json')
os.environ['HPC_CONTROL_REPORT_DIR'] = str(ROOT/'.test-state/reports')
os.environ['HPC_CONTROL_STATE_DIR'] = str(ROOT/'.test-state')
os.environ['HPC_CONTROL_CONFIG'] = str(ROOT/'.test-state/missing-config.json')
os.environ['HPC_CONTROL_DEMO_TOKEN'] = 'demo'

@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    from app import db, provider
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'control.db')
    monkeypatch.setattr(provider, 'STATE_DIR', tmp_path)
    monkeypatch.setenv('HPC_CONTROL_RDMA_CONFIG', str(tmp_path/'rdma.json'))
    monkeypatch.setenv('HPC_CONTROL_REPORT_DIR', str(tmp_path/'reports'))
