from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
import subprocess
import time
from typing import Any
import yaml

from .collection_failures import finding as collection_failure, pull_blocked
from .config import Settings
from .diagnostics import (
    profile as diagnostic_profile,
    public_profiles,
    sanitize_output,
    validate_node_name,
    write_reports,
)


class ProviderError(RuntimeError):
    pass


class ActionError(ProviderError):
    def __init__(self, message, *, field='', code='operation_failed', next_action=''):
        super().__init__(message)
        self.detail = {'message': message, 'field': field, 'code': code, 'next_action': next_action}


def error_detail(error):
    return getattr(error, 'detail', str(error))


class BaseProvider:
    def __init__(self, settings: Settings):
        self.s = settings
        self.report_progress = lambda stage, completed, total, detail="": None

    def _run(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout: int = 90,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            argv,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={
                **__import__("os").environ,
                "KUBECONFIG": self.s.kubeconfig,
                "KUBECTL_KUBERC": "false",
                "OCI_CLI_AUTH": "instance_principal",
            },
        )
        if check and result.returncode:
            msg = result.stderr.strip() or result.stdout.strip() or f"command failed: {argv[0]}"
            raise ProviderError(msg[-4000:])
        return result

    def kubectl(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self._run(["kubectl", "--kubeconfig", self.s.kubeconfig, *args], **kwargs)

    def oci(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self._run(
            ["oci", "--auth", "instance_principal", "--region", self.s.region, *args],
            **kwargs,
        )

    @staticmethod
    def _resource_count(value: Any) -> int:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return 0

    def _rdma_nodes(self) -> list[dict[str, Any]]:
        result = self.kubectl(["get", "nodes", "-l", "oci-rdma=true", "-o", "json"])
        return json.loads(result.stdout).get("items", [])

    @staticmethod
    def _node_internal_ip(node: dict[str, Any]) -> str:
        for address in node.get("status", {}).get("addresses", []) or []:
            if address.get("type") == "InternalIP":
                return str(address.get("address", ""))
        return ""

    @staticmethod
    def _node_instance_id(node: dict[str, Any]) -> str:
        candidates = [
            node.get("spec", {}).get("providerID", ""),
            node.get("metadata", {}).get("annotations", {}).get("oci.oraclecloud.com/instance-id", ""),
            node.get("metadata", {}).get("annotations", {}).get("oci.oraclecloud.com/node.info", ""),
        ]
        for candidate in candidates:
            match = re.search(r"ocid1\.instance\.[A-Za-z0-9._-]+", str(candidate))
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _addon_name(addon: dict[str, Any]) -> str:
        return str(addon.get("addon-name") or addon.get("addon_name") or addon.get("name") or "")

    @staticmethod
    def _addon_state(addon: dict[str, Any]) -> str:
        return str(
            addon.get("lifecycle-state")
            or addon.get("lifecycle_state")
            or addon.get("state")
            or "UNKNOWN"
        )

    @staticmethod
    def _addon_version(addon: dict[str, Any]) -> str:
        return str(
            addon.get("current-installed-version")
            or addon.get("current_installed_version")
            or addon.get("version")
            or ""
        )

    def _node_pool_instance_map(self) -> dict[str, str]:
        """Return node name/private-IP -> instance OCID from the managed node pool.

        OKE node provider IDs are the preferred source, but older/custom images
        can expose different providerID/annotation formats. The node-pool API is
        a safe read-only fallback and avoids requiring SSH merely to correlate a
        Kubernetes Node with OCI Instance Agent plugin state.
        """
        if not self.s.node_pool_id:
            return {}
        result = self.oci(
            ["ce", "node-pool", "get", "--node-pool-id", self.s.node_pool_id, "--output", "json"],
            timeout=30,
            check=False,
        )
        if result.returncode:
            return {}
        try:
            nodes = json.loads(result.stdout).get("data", {}).get("nodes", []) or []
        except json.JSONDecodeError:
            return {}
        mapping: dict[str, str] = {}
        for item in nodes:
            instance_id = str(item.get("id", ""))
            if not instance_id:
                continue
            for key in (item.get("name"), item.get("private-ip"), item.get("private_ip")):
                if key:
                    mapping[str(key)] = instance_id
        return mapping

    def _instance_details(self, instance_id: str) -> dict[str, Any]:
        if not instance_id:
            return {"available": False, "error": "Kubernetes node has no OCI instance OCID in providerID"}
        result = self.oci(
            ["compute", "instance", "get", "--instance-id", instance_id, "--output", "json"],
            timeout=30,
            check=False,
        )
        if result.returncode:
            return {
                "available": False,
                "error": (result.stderr or result.stdout).strip()[-1000:],
            }
        try:
            data = json.loads(result.stdout).get("data", {})
        except json.JSONDecodeError:
            return {"available": False, "error": "OCI Compute returned invalid JSON"}
        return {
            "available": True,
            "display_name": data.get("display-name", data.get("display_name", "")),
            "shape": data.get("shape", ""),
            "lifecycle_state": data.get("lifecycle-state", data.get("lifecycle_state", "")),
            "availability_domain": data.get("availability-domain", data.get("availability_domain", "")),
        }

    def _instance_agent_plugins(self, instance_id: str) -> dict[str, Any]:
        if not instance_id:
            return {"available": False, "items": [], "error": "Missing OCI instance OCID"}
        result = self.oci(
            [
                "instance-agent",
                "plugin",
                "list",
                "--compartment-id",
                self.s.compartment_id,
                "--instanceagent-id",
                instance_id,
                "--all",
                "--output",
                "json",
            ],
            timeout=30,
            check=False,
        )
        if result.returncode:
            return {
                "available": False,
                "items": [],
                "error": (result.stderr or result.stdout).strip()[-1000:],
            }
        try:
            raw = json.loads(result.stdout).get("data", [])
        except json.JSONDecodeError:
            return {"available": False, "items": [], "error": "OCI Instance Agent returned invalid JSON"}
        items = [{
            "name": item.get("name", ""),
            "status": item.get("status", "UNKNOWN"),
            "message": item.get("message") or "",
            "updated": item.get("time-last-updated-utc", item.get("time_last_updated_utc", "")),
        } for item in raw]
        return {"available": True, "items": items, "error": ""}

    def diagnostic_catalog(self) -> dict[str, Any]:
        nodes = [
            {
                "name": node.get("metadata", {}).get("name", ""),
                "internal_ip": self._node_internal_ip(node),
                "ready": any(
                    condition.get("type") == "Ready" and condition.get("status") == "True"
                    for condition in node.get("status", {}).get("conditions", [])
                ),
            }
            for node in self._rdma_nodes()
        ]
        return {
            "profiles": public_profiles(),
            "nodes": nodes,
            "image": self.s.rdma_tools_image,
            "image_configured": bool(
                self.s.rdma_tools_image
                and re.fullmatch(r"[^\s@]+@sha256:[0-9a-fA-F]{64}", self.s.rdma_tools_image)
            ),
            "privilege_boundary": {
                "ephemeral": True,
                "privileged": True,
                "host_network": True,
                "host_pid": True,
                "host_root_read_only": True,
                "service_account_token": False,
                "arbitrary_commands": False,
                "approval_required": True,
                "retention": "Reports are retained for up to 30 days or 100 runs on the private operator VM.",
            },
        }

    def validate_node_diagnostic(self, node_name: str, profile_name: str) -> dict[str, Any]:
        node_name = validate_node_name(node_name)
        selected_profile = diagnostic_profile(profile_name)
        if not self.s.rdma_tools_image or not re.fullmatch(
            r"[^\s@]+@sha256:[0-9a-fA-F]{64}", self.s.rdma_tools_image
        ):
            raise ProviderError(
                "Configure the operations/RDMA tools image as an immutable repository@sha256:<digest> "
                "before running privileged node diagnostics."
            )
        nodes = {node.get("metadata", {}).get("name", ""): node for node in self._rdma_nodes()}
        if node_name not in nodes:
            raise ProviderError(f"Node {node_name!r} is not a current worker labelled oci-rdma=true")
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in nodes[node_name].get("status", {}).get("conditions", [])
        )
        if not ready:
            raise ProviderError(f"Node {node_name!r} is not Kubernetes Ready")
        return {
            "node_name": node_name,
            "profile": selected_profile.name,
            "title": selected_profile.title,
            "timeout_seconds": selected_profile.timeout_seconds,
        }

    def registry_checks(self, namespaces):
        checks = []
        if not self.s.rdma_image_pull_secret:
            return [{'name': 'Registry credentials', 'status': 'pass', 'detail': 'No pull secret configured; image must be accessible without it. A real pull verifies access.'}]
        for namespace in dict.fromkeys(namespaces):
            result = self.kubectl(['-n', namespace, 'get', 'secret', self.s.rdma_image_pull_secret,
                                  '-o', 'jsonpath={.type}'], check=False, timeout=15)
            ok = result.returncode == 0 and result.stdout.strip() in {'kubernetes.io/dockerconfigjson', 'kubernetes.io/dockercfg'}
            detail = ('Secret exists with a registry credential type; actual pull remains unverified.' if ok else
                      f'Cannot use pull secret {self.s.rdma_image_pull_secret} in namespace {namespace}. Verify namespace, secret, type and operator read access. ' + (result.stderr or 'Unsupported secret type.')[-600:])
            checks.append({'name': f'Registry · {namespace}', 'status': 'pass' if ok else 'block', 'detail': detail})
        return checks

    def require_registry(self, namespaces):
        blocked = [x['detail'] for x in self.registry_checks(namespaces) if x['status'] == 'block']
        if blocked:
            raise ActionError(' '.join(blocked), field='rdma_image_pull_secret', code='registry_prerequisite',
                              next_action='Provision the named pull secret in each required namespace, then retry. Credentials are never entered in the UI.')

    def _diagnostic_findings(self, profile_name: str, output: str) -> list[dict[str, str]]:
        from .link_findings import findings as link_findings
        facts = next((json.loads(line.split('=', 1)[1]) for line in output.splitlines() if line.startswith('DRANET_FACTS=')), {})
        findings = link_findings(facts, self.s.rdma_pf_name, self.s.rdma_mtu)
        iface = next((i for i in facts.get('interfaces', []) if i.get('name') == self.s.rdma_pf_name), {})
        waiting = [line for line in output.splitlines() if 'Interface ' + self.s.rdma_pf_name + ' is not ready' in line or 'Interface ' + self.s.rdma_pf_name + ' ready is false' in line]
        if waiting and iface.get('carrier') != '1':
            findings.append({'code': 'authentication_waiting', 'severity': 'block',
                'message': 'Authentication logs report waiting for the selected interface; current carrier is not established.',
                'evidence': waiting[-1][:500],
                'next_action': 'Restore link readiness and rerun diagnostics. Module COMPLETED and a generic WPA process do not establish successful authentication; review evidence timestamps.'})
        # Only the configured PF determines link health; inactive unused ports are normal.
        pf = re.escape(self.s.rdma_pf_name)
        links = output.replace('\\n', '\n')
        selected = [line for line in links.splitlines() if re.search(r'netdev\s+' + pf + r'(?:\s|$)', line)]
        if any('physical_state DISABLED' in line or 'state DOWN' in line for line in selected):
            findings.append({'severity': 'block', 'message': f'{self.s.rdma_pf_name}: selected RDMA port is down or disabled.'})
        if 'WPA_STATUS_INCONCLUSIVE' in output or 'Read-only file system' in output:
            findings.append({'severity': 'warn', 'message': 'WPA control status is inconclusive in read-only diagnostics. Socket/process and log evidence must be reviewed separately.'})
        if 'oci-hpc-rdma-configure' in output and ('nil pointer dereference' in output or 'Current Status: [not responding]' in output):
            findings.append({'severity': 'block', 'message': 'HPC RDMA configuration logs contain a crash or unresponsive-module signature. Review timestamps; confirmed initialization recovery can check current eligibility.'})
        if not findings:
            findings.append({'severity': 'info', 'message': 'Collection completed; no selected-port blocking signature detected. This is not proof of successful RDMA authentication or workload validation.'})
        return findings

    def _wait_for_diagnostic_pod(self, namespace: str, pod_name: str, timeout: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            result = self.kubectl(
                ["-n", namespace, "get", "pod", pod_name, "-o", "json"],
                check=False,
                timeout=20,
            )
            if result.returncode == 0:
                try:
                    last = json.loads(result.stdout)
                except json.JSONDecodeError:
                    last = {}
                phase = str(last.get("status", {}).get("phase", ""))
                if phase in {"Succeeded", "Failed"} or pull_blocked(last):
                    return last
            time.sleep(2)
        return last

    def node_diagnostic(self, node_name: str, profile_name: str, job_id: str) -> dict[str, Any]:
        if not getattr(self, "_checking_hosts", False):
            self.report_progress("Collecting host diagnostic", 0, 1, node_name)
        validated = self.validate_node_diagnostic(node_name, profile_name)
        self.require_registry(["oke-rdma-diagnostics"])
        selected_profile = diagnostic_profile(profile_name)
        namespace = "oke-rdma-diagnostics"
        pod_name = f"rdma-diag-{job_id.split('-', 1)[0]}-{selected_profile.name[:24]}"[:63].rstrip("-")
        started_at = int(time.time())
        host_script = selected_profile.script.rstrip() + "\n"
        wrapper = (
            "set -euo pipefail\n"
            "printf '%s\\n' 'OKE RDMA fixed diagnostic runner'\n"
            f"printf 'profile=%s\\n' '{selected_profile.name}'\n"
            f"printf 'target_node=%s\\n' '{node_name}'\n"
            "printf 'started_at=%s\\n' \"$(date -u --iso-8601=seconds || date -u)\"\n"
            "if [ ! -x /host/bin/bash ]; then echo 'ERROR: /host/bin/bash is unavailable'; exit 20; fi\n"
            f"timeout {selected_profile.timeout_seconds} chroot /host /bin/bash -s <<'OKE_RDMA_HOST_DIAGNOSTIC'\n"
            + host_script
            + "OKE_RDMA_HOST_DIAGNOSTIC\n"
            + "printf 'completed_at=%s\\n' \"$(date -u --iso-8601=seconds || date -u)\"\n"
        )
        ns = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "app.kubernetes.io/part-of": "oke-rdma-quickstart",
                    "pod-security.kubernetes.io/enforce": "privileged",
                    "pod-security.kubernetes.io/audit": "privileged",
                    "pod-security.kubernetes.io/warn": "privileged",
                },
            },
        }
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/name": "oke-rdma-node-diagnostic",
                    "app.kubernetes.io/part-of": "oke-rdma-quickstart",
                    "oke-rdma/job-id": job_id,
                    "oke-rdma/profile": selected_profile.name,
                },
                "annotations": {
                    "oke-rdma/approval": "typed-environment-confirmation",
                    "oke-rdma/command-model": "fixed-profile-no-browser-shell",
                },
            },
            "spec": {
                "nodeName": node_name,
                "restartPolicy": "Never",
                "activeDeadlineSeconds": selected_profile.timeout_seconds + 30,
                "terminationGracePeriodSeconds": 1,
                "hostNetwork": True,
                "hostPID": True,
                "hostIPC": False,
                "dnsPolicy": "ClusterFirstWithHostNet",
                "automountServiceAccountToken": False,
                "securityContext": {"seccompProfile": {"type": "Unconfined"}},
                "tolerations": [{"operator": "Exists"}],
                "imagePullSecrets": ([{"name": self.s.rdma_image_pull_secret}] if self.s.rdma_image_pull_secret else []),
                "containers": [{
                    "name": "diagnostic",
                    "image": self.s.rdma_tools_image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/bash", "-lc"],
                    "args": [wrapper],
                    "securityContext": {
                        "privileged": True,
                        "allowPrivilegeEscalation": True,
                        "runAsUser": 0,
                        "readOnlyRootFilesystem": True,
                    },
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    },
                    "volumeMounts": [
                        {
                            "name": "host-root",
                            "mountPath": "/host",
                            "readOnly": True,
                            "mountPropagation": "HostToContainer",
                        },
                        {"name": "tmp", "mountPath": "/tmp"},
                    ],
                }],
                "volumes": [
                    {"name": "host-root", "hostPath": {"path": "/", "type": "Directory"}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                ],
            },
        }

        phase = "Unknown"
        pod_status: dict[str, Any] = {}
        raw_output = ""
        pod_deleted = False
        delete_error = ""
        execution_error = ""
        event_evidence = []
        self._apply_documents([ns], dry_run=True)
        self._apply_documents([ns])
        # The durable worker is serial. Remove any stale diagnostic pod left by a
        # prior VM/worker interruption before creating the next fixed-profile run.
        self.kubectl(
            [
                "-n", namespace, "delete", "pod",
                "-l", "app.kubernetes.io/name=oke-rdma-node-diagnostic",
                "--ignore-not-found=true", "--wait=false",
            ],
            check=False,
            timeout=30,
        )
        self._apply_documents([pod], dry_run=True)
        self._apply_documents([pod])
        try:
            pod_status = self._wait_for_diagnostic_pod(
                namespace,
                pod_name,
                selected_profile.timeout_seconds + 45,
            )
            phase = str(pod_status.get("status", {}).get("phase", "")) or "Timeout"
            if phase not in {"Succeeded", "Failed"}:
                phase = "ImagePullBlocked" if pull_blocked(pod_status) else "Timeout"
            logs = subprocess.CompletedProcess([], 0, "", "") if pull_blocked(pod_status) else self.kubectl(
                [
                    "-n", namespace, "logs", pod_name, "-c", "diagnostic",
                    "--limit-bytes=786432",
                ],
                check=False,
                timeout=45,
            )
            raw_output = (logs.stdout or "") + (logs.stderr or "")
            if not raw_output.strip():
                raw_output = json.dumps(pod_status.get("status", {}), indent=2)
        except Exception as exc:
            execution_error = str(exc)[-2000:]
            phase = "CollectionFailed"
        finally:
            # Best-effort evidence collection must precede deletion, even on log/time-out errors.
            try:
                current = self.kubectl(['-n', namespace, 'get', 'pod', pod_name, '-o', 'json'], check=False, timeout=15)
                if current.returncode == 0:
                    pod_status = json.loads(current.stdout)
                observed = self.kubectl(['-n', namespace, 'get', 'events', '--field-selector', 'involvedObject.name=' + pod_name, '-o', 'json'], check=False, timeout=15)
                if observed.returncode == 0:
                    event_evidence = [{k: e.get(k) for k in ['type', 'reason', 'message', 'lastTimestamp', 'count']} for e in json.loads(observed.stdout).get('items', [])][-30:]
            except Exception as exc:
                execution_error += '\nEvidence capture incomplete: ' + str(exc)[-500:]
            raw_output += '\nEXECUTION_STATUS=' + json.dumps(pod_status.get('status', {})) + '\nEVENTS=' + json.dumps(event_evidence) + '\n' + execution_error
            deleted = self.kubectl(
                [
                    "-n", namespace, "delete", "pod", pod_name,
                    "--ignore-not-found=true", "--wait=true", "--timeout=30s",
                ],
                check=False,
                timeout=30,
            )
            pod_deleted = deleted.returncode == 0
            if not pod_deleted:
                delete_error = (deleted.stderr or deleted.stdout).strip()[-1000:]

        output, truncated = sanitize_output(raw_output)
        finished_at = int(time.time())
        findings = self._diagnostic_findings(selected_profile.name, output)
        facts = next((json.loads(line.split("=", 1)[1]) for line in output.splitlines() if line.startswith("DRANET_FACTS=")), {})
        status = "passed" if phase == "Succeeded" and not execution_error and logs.returncode == 0 else "failed"
        if profile_name == 'dranet-readiness' and not facts:
            status = 'failed'
        if status == 'failed':
            findings = [collection_failure(pod_status, event_evidence, execution_error)] + [f for f in findings if f['severity'] != 'info']
        health = 'not_established' if status == 'failed' else ('needs_attention' if any(f['severity'] == 'block' for f in findings) else 'not_established')

        report = {
            "schema_version": "1.1",
            "collection_status": status,
            "health_status": health,
            "environment": self.s.environment_name,
            "cluster": self.s.cluster_name,
            "region": self.s.region,
            "job_id": job_id,
            "node": validated["node_name"],
            "profile": {
                "name": selected_profile.name,
                "title": selected_profile.title,
                "description": selected_profile.description,
                "sections": list(selected_profile.sections),
            },
            "started_at_epoch": started_at,
            "finished_at_epoch": finished_at,
            "duration_seconds": max(0, finished_at - started_at),
            "pod": {
                "namespace": namespace,
                "name": pod_name,
                "phase": phase,
                "deleted": pod_deleted,
                "delete_error": delete_error,
            },
            "privilege_boundary": {
                "privileged_ephemeral_pod": True,
                "host_network": True,
                "host_pid": True,
                "host_root_mount": "read-only",
                "service_account_token": False,
                "browser_supplied_shell": False,
                "fixed_profile": True,
            },
            "output_truncated": truncated,
            "findings": findings,
            "output": output,
        }
        header = (
            "OKE RDMA NODE DIAGNOSTIC REPORT\n"
            f"environment={self.s.environment_name}\ncluster={self.s.cluster_name}\n"
            f"node={node_name}\nprofile={selected_profile.name}\nphase={phase}\n"
            f"duration_seconds={report['duration_seconds']}\n"
            f"pod_deleted={pod_deleted}\n\n"
        )
        reports = write_reports(job_id, report, header + 'FINDINGS\n' + json.dumps(findings, indent=2) + '\n\n' + output)
        return {
            "status": status,
            "facts": facts,
            "collection_status": status,
            "health_status": health,
            "node": node_name,
            "profile": selected_profile.name,
            "title": selected_profile.title,
            "phase": phase,
            "duration_seconds": report["duration_seconds"],
            "findings": findings,
            "output_truncated": truncated,
            "pod_deleted": pod_deleted,
            "reports": reports,
            "note": (
                "The report was produced by a fixed, reviewed profile in an ephemeral privileged pod. "
                "No browser-supplied shell command was executed."
            ),
        }

    def _apply_documents(self, docs: list[dict[str, Any]], *, dry_run: bool = False) -> str:
        text = yaml.safe_dump_all(docs, sort_keys=False)
        args = ["apply", "--server-side", "--field-manager=oke-rdma-control", "-f", "-"]
        if dry_run:
            args.insert(1, "--dry-run=server")
        return self.kubectl(args, stdin=text, timeout=120).stdout

