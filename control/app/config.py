from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    environment_name: str = 'oke-rdma-demo'
    region: str = 'eu-frankfurt-1'
    tenancy_id: str = ''
    compartment_id: str = ''
    cluster_id: str = ''
    cluster_name: str = 'oke-rdma-demo'
    node_pool_id: str = ''
    compute_cluster_id: str = ''
    capacity_stack_ocid: str = ''
    kubeconfig: str = '/etc/oke-rdma-control/kube/config'
    operator_kubernetes_access_mode: str = 'iam_admin'
    worker_ssh_enabled: bool = False
    kubernetes_version: str = 'v1.36.4'
    kubernetes_minor: str = '1.36'
    ubuntu_release: str = 'ubuntu-noble'
    oke_node_package: str = ''
    oke_repo_url: str = ''
    rdma_pf_name: str = 'ens800f0np0'
    rdma_mtu: int = 4220
    rdma_child_cidr: str = '10.208.0.0/12'
    rdma_tools_image: str = ''
    rdma_image_pull_secret: str = ''
    rdma_tos: int = 41
    mode: str = 'live'
    workload_namespace: str = 'rdma-workloads'
    vcn_cidr: str = '10.70.0.0/16'
    services_cidr: str = '10.96.0.0/16'
    worker_subnet_cidr: str = '10.70.1.0/24'
    bastion_ssh_ttl_seconds: int = 7200
    bastion_port_forwarding_ttl_seconds: int = 7200

    @property
    def rdma_configured(self):
        return bool(self.rdma_pf_name and self.rdma_mtu == 4220)

    def rdma_config(self):
        return {k: getattr(self, k) for k in RUNTIME_FIELDS}


RUNTIME_FIELDS = {'rdma_pf_name', 'rdma_mtu', 'rdma_child_cidr', 'rdma_tools_image', 'rdma_image_pull_secret', 'rdma_tos'}


def _static_path():
    return Path(os.environ.get('HPC_CONTROL_CONFIG', '/etc/oke-rdma-control/config.json'))


def _runtime_path(static_exists=None):
    default = '/var/lib/oke-rdma-control/rdma-config.json' if _static_path().exists() else '/tmp/oke-rdma-control-demo-rdma-config.json'
    return Path(os.environ.get('HPC_CONTROL_RDMA_CONFIG', default))


def _read_json(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return data


def load_settings():
    base = _read_json(_static_path())
    if not base:
        base = {'mode': 'demo', 'rdma_tools_image': 'example.invalid/rdma-tools@sha256:' + '0' * 64}
    base.update(_read_json(_runtime_path()))
    unknown = set(base) - set(Settings.__dataclass_fields__)
    if unknown:
        raise ValueError(f'Unsupported settings: {sorted(unknown)}')
    return Settings(**base)


def save_rdma_config(values):
    if set(values) - RUNTIME_FIELDS:
        raise ValueError('Unsupported RDMA settings')
    current = load_settings().rdma_config()
    current.update(values)
    path = _runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(current, indent=2) + '\n')
    tmp.chmod(0o600)
    os.replace(tmp, path)
    return load_settings()


def public_settings(settings):
    data = asdict(settings)
    data.pop('kubeconfig')
    data['rdma'] = settings.rdma_config()
    return data
