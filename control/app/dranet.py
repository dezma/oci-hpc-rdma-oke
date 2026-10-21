"""Pinned Dranet configuration and complete, scheduler-owned DRA workloads."""
from __future__ import annotations

import json
from .config import Settings

VERSION = "v1.5.0"
DEVICE_CLASS = {
    "apiVersion": "resource.k8s.io/v1", "kind": "DeviceClass",
    "metadata": {"name": "dra.net", "labels": {"app.kubernetes.io/part-of": "oke-rdma-quickstart"}},
    "spec": {"selectors": [{"cel": {"expression": 'device.driver == "dra.net"'}}]},
}


def values(s: Settings) -> dict:
    return {
        "image": {"repository": "registry.k8s.io/networking/dranet", "tag": VERSION, "pullPolicy": "IfNotPresent"},
        "nodeSelector": {"oci-rdma": "true", "oke-rdma-mode": "dranet"},
        "args": {
            "moveIBInterfaces": False, "cloudProviderHint": "OKE", "profileProvider": "cloud",
            "filter": '"dra.net/ifName" in attributes && attributes["dra.net/ifName"].StringValue == ' + json.dumps(s.rdma_pf_name),
            "cloudProviderOptions": {"oke": {"rdma-child-ipv4-cidr": s.rdma_child_cidr}},
        },
        "kubeletRootDir": "/var/lib/kubelet",
    }


def workload(s: Settings, name: str, *, node: str = "", validation: bool = False) -> list[dict]:
    # Parent IP readiness is also checked immediately before creating validation claims.
    expression = ('device.driver == "dra.net" && has(device.attributes["dra.net"].ifName)'
                  ' && device.attributes["dra.net"].ifName == ' + json.dumps(s.rdma_pf_name) +
                  ' && device.attributes["dra.net"].rdma == true'
                  ' && has(device.attributes["dra.net"].ipv4)'
                  ' && device.attributes["dra.net"].ipv4.startsWith("10.224.")')
    # OCA's /12 includes 10.224 through 10.239; use a CEL regex for the full range.
    expression = expression.replace('.startsWith("10.224.")', '.matches("^10[.]2(2[4-9]|3[0-9])[.]")')
    labels = {"app.kubernetes.io/part-of": "oke-rdma-quickstart"}
    if validation:
        labels["app"] = "oke-dranet-validation"
    claim = {
        "apiVersion": "resource.k8s.io/v1", "kind": "ResourceClaim",
        "metadata": {"name": name, "namespace": s.workload_namespace, "labels": labels},
        "spec": {"devices": {"requests": [{"name": "rdma", "exactly": {
            "deviceClassName": "dra.net", "selectors": [{"cel": {"expression": expression}}],
        }}]}},
    }
    spec = {
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "hostNetwork": False, "hostPID": False, "hostIPC": False,
        "nodeSelector": {"oci-rdma": "true", "oke-rdma-mode": "dranet"},
        "resourceClaims": [{"name": "rdma", "resourceClaimName": name}],
        "containers": [{
            "name": "tools", "image": s.rdma_tools_image, "imagePullPolicy": "IfNotPresent",
            "command": ["sleep", "infinity"],
            "securityContext": {"privileged": False, "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"], "add": ["IPC_LOCK"]}},
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                          "limits": {"memory": "512Mi"}, "claims": [{"name": "rdma"}]},
        }],
    }
    if s.rdma_image_pull_secret:
        spec["imagePullSecrets"] = [{"name": s.rdma_image_pull_secret}]
    if node:
        spec["affinity"] = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [{"matchFields": [{"key": "metadata.name", "operator": "In", "values": [node]}]}],
        }}}
    if validation:
        spec["activeDeadlineSeconds"] = 600
        spec.setdefault("affinity", {})["podAntiAffinity"] = {"requiredDuringSchedulingIgnoredDuringExecution": [{
            "labelSelector": {"matchLabels": {"app": "oke-dranet-validation"}}, "topologyKey": "kubernetes.io/hostname",
        }]}
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": name, "namespace": s.workload_namespace, "labels": labels,
    }, "spec": spec}
    return [claim, pod]
