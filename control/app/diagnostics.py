from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import time
from typing import Any


@dataclass(frozen=True)
class DiagnosticProfile:
    name: str
    title: str
    description: str
    expected_seconds: int
    timeout_seconds: int
    sections: tuple[str, ...]
    script: str


# These scripts are fixed, reviewed, and shipped with the Control Center. The
# browser chooses only a profile ID and an RDMA worker node; it never submits a
# shell command, arguments, environment variables, or script content.
_PROFILES: dict[str, DiagnosticProfile] = {
    "rdma-link-health": DiagnosticProfile(
        name="rdma-link-health",
        title="RDMA link health",
        description=(
            "Captures RDMA device-to-netdev mappings, link state, carrier, MTU, "
            "addresses and ethtool summaries for the Mellanox interfaces."
        ),
        expected_seconds=15,
        timeout_seconds=90,
        sections=(
            "Host identity and kernel",
            "RDMA links and device mappings",
            "Mellanox interface carrier, state, MTU and addresses",
            "ethtool link summary",
        ),
        script=r'''set -u
section() { printf '\n===== %s =====\n' "$1"; }
section "HOST"
date -u --iso-8601=seconds || date -u
hostname
uname -a
section "RDMA LINKS"
rdma link show 2>&1 || true
section "IB DEVICE TO NETDEV"
ibdev2netdev 2>&1 || true
section "MELLANOX INTERFACES"
for iface in ens300f0np0 ens300f1np1 ens800f0np0 ens800f1np1; do
  [ -e "/sys/class/net/$iface" ] || continue
  echo "--- $iface ---"
  ip -d link show "$iface" 2>&1 || true
  ip -br addr show "$iface" 2>&1 || true
  printf 'carrier='; cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo unknown
  printf 'operstate='; cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo unknown
  printf 'mtu='; cat "/sys/class/net/$iface/mtu" 2>/dev/null || echo unknown
  ethtool "$iface" 2>&1 | grep -E 'Speed:|Duplex:|Auto-negotiation:|Link detected:' || true
done
section "INFINIBAND INVENTORY"
ls -la /sys/class/infiniband 2>&1 || true
for dev in /sys/class/infiniband/*; do
  [ -e "$dev" ] || continue
  echo "--- $(basename "$dev") ---"
  for port in "$dev"/ports/*; do
    [ -e "$port" ] || continue
    printf 'port=%s state=' "$(basename "$port")"
    cat "$port/state" 2>/dev/null || true
    printf 'physical_state='; cat "$port/phys_state" 2>/dev/null || true
  done
done
''',
    ),
    "rdma-authentication": DiagnosticProfile(
        name="rdma-authentication",
        title="OCI RDMA authentication",
        description=(
            "Checks Oracle Cloud Agent, HPC RDMA plugin processes, dedicated wired "
            "wpa_supplicant sessions and recent authentication/configuration logs."
        ),
        expected_seconds=20,
        timeout_seconds=120,
        sections=(
            "Oracle Cloud Agent version and services",
            "HPC authentication and auto-configuration processes",
            "Per-interface wired wpa_supplicant state",
            "Recent RDMA/HPC agent logs",
        ),
        script=r'''set -u
section() { printf '\n===== %s =====\n' "$1"; }
section "HOST"
date -u --iso-8601=seconds || date -u
hostname
section "ORACLE CLOUD AGENT"
snap list oracle-cloud-agent 2>&1 || true
snap services oracle-cloud-agent 2>&1 || true
systemctl --no-pager --full status snap.oracle-cloud-agent.oracle-cloud-agent.service 2>&1 | tail -80 || true
section "HPC PROCESSES"
ps -leaf | grep -E 'oci-rdma-authentication|oci-hpc-configure|wpa_supplicant' | grep -v grep || true
section "WPA SOCKETS"
ls -la /run/wpa_supplicant 2>&1 || true
section "PER-INTERFACE WPA STATUS"
for iface in ens800f0np0 ens800f1np1; do
  [ -e "/sys/class/net/$iface" ] || continue
  echo "--- $iface ---"
  echo "WPA_STATUS_INCONCLUSIVE: control query omitted in read-only diagnostics; inspect socket/process evidence and authentication logs."
  pidfile="/var/run/wpa_supplicant-$iface.pid"
  if [ -f "$pidfile" ]; then
    printf 'pidfile='; cat "$pidfile" 2>/dev/null || true
  else
    echo 'pidfile=missing'
  fi
done
section "OCI RDMA AUTHENTICATION FILE LOGS"
for log in /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-hpc.log /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-rdma-authentication.log; do
  [ ! -f "$log" ] || tail -120 "$log"
done
section "HPC AUTO-CONFIGURATION LOGS"
find /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-hpc-configure -maxdepth 2 -type f -name '*.log' -exec tail -n 100 {} \; 2>&1 || true
section "RECENT OCA RDMA/HPC LOGS"
journalctl -u snap.oracle-cloud-agent.oracle-cloud-agent.service -b --no-pager 2>&1 \
  | grep -Ei 'rdma|hpc|wpa|802|auth|mlx5|ens800|error|fail|warning' \
  | tail -300 || true
section "RECENT WPA LOGS"
journalctl -u wpa_supplicant.service -b --no-pager 2>&1 \
  | grep -Ei 'ens800|eap|auth|fail|error|warning' \
  | tail -200 || true
''',
    ),
    "nic-device-inventory": DiagnosticProfile(
        name="nic-device-inventory",
        title="Physical NIC inventory",
        description=(
            "Reports physical NIC identity, driver, active partitions, RDMA devices "
            "and the current Kubernetes-visible host interface inventory."
        ),
        expected_seconds=15,
        timeout_seconds=90,
        sections=(
            "PF and PCI inventory",
            "sriov_numvfs and sriov_totalvfs",
            "VF link attributes",
            "RDMA and InfiniBand inventory",
        ),
        script=r'''set -u
section() { printf '\n===== %s =====\n' "$1"; }
section "HOST"
date -u --iso-8601=seconds || date -u
hostname
section "SR-IOV PF INVENTORY"
for iface in ens300f0np0 ens300f1np1 ens800f0np0 ens800f1np1; do
  [ -e "/sys/class/net/$iface" ] || continue
  echo "--- $iface ---"
  readlink -f "/sys/class/net/$iface/device" 2>/dev/null || true
  printf 'driver='; basename "$(readlink -f /sys/class/net/$iface/device/driver 2>/dev/null)" 2>/dev/null || echo unknown
  printf 'sriov_numvfs='; cat "/sys/class/net/$iface/device/sriov_numvfs" 2>/dev/null || echo unavailable
  printf 'sriov_totalvfs='; cat "/sys/class/net/$iface/device/sriov_totalvfs" 2>/dev/null || echo unavailable
  ip -d link show "$iface" 2>&1 || true
  ls -l "/sys/class/net/$iface/device"/virtfn* 2>/dev/null || true
done
section "RDMA LINKS"
rdma link show 2>&1 || true
section "INFINIBAND DEVICES"
ls -la /sys/class/infiniband 2>&1 || true
ibv_devices 2>&1 || true
section "PCI MELLANOX DEVICES"
lspci -Dnn 2>&1 | grep -Ei 'Mellanox|NVIDIA.*Ethernet|15b3' || true
''',
    ),
    "mlx5-kernel-logs": DiagnosticProfile(
        name="mlx5-kernel-logs",
        title="mlx5 and RDMA kernel logs",
        description=(
            "Collects bounded current-boot kernel messages related to mlx5, RDMA, "
            "PCI, firmware and link initialization."
        ),
        expected_seconds=15,
        timeout_seconds=90,
        sections=(
            "Current RDMA links",
            "Current-boot mlx5/RDMA kernel journal",
            "Bounded dmesg extract",
        ),
        script=r'''set -u
section() { printf '\n===== %s =====\n' "$1"; }
section "HOST"
date -u --iso-8601=seconds || date -u
hostname
uname -a
section "RDMA LINKS"
rdma link show 2>&1 || true
section "KERNEL JOURNAL - MLX5/RDMA"
journalctl -k -b --no-pager 2>&1 \
  | grep -Ei 'mlx5|rdma|infiniband|ens800|firmware|pci|link.*(down|up)|error|fail|warning' \
  | tail -500 || true
section "DMESG - MLX5/RDMA"
dmesg -T 2>&1 \
  | grep -Ei 'mlx5|rdma|infiniband|ens800|firmware|pci|link.*(down|up)|error|fail|warning' \
  | tail -500 || true
''',
    ),
    "full-rdma-support-bundle": DiagnosticProfile(
        name="full-rdma-support-bundle",
        title="Full RDMA support bundle",
        description=(
            "Runs the complete fixed diagnostic set for an administrator handoff: link, "
            "authentication, physical NIC inventory, network state and bounded logs."
        ),
        expected_seconds=45,
        timeout_seconds=180,
        sections=(
            "Host, kernel and network summary",
            "RDMA link and Mellanox PF health",
            "OCI Cloud Agent and per-interface authentication",
            "NIC partitioning and PCI inventory",
            "Bounded current-boot logs",
        ),
        script=r'''set -u
section() { printf '\n===== %s =====\n' "$1"; }
section "HOST"
date -u --iso-8601=seconds || date -u
hostname
uname -a
cat /etc/os-release 2>/dev/null || true
section "NODE NETWORK"
ip -br link 2>&1 || true
ip -br addr 2>&1 || true
ip route show 2>&1 || true
section "RDMA LINKS AND MAPPINGS"
rdma link show 2>&1 || true
ibdev2netdev 2>&1 || true
section "MELLANOX PF HEALTH"
for iface in ens300f0np0 ens300f1np1 ens800f0np0 ens800f1np1; do
  [ -e "/sys/class/net/$iface" ] || continue
  echo "--- $iface ---"
  ip -d link show "$iface" 2>&1 || true
  ip -br addr show "$iface" 2>&1 || true
  printf 'carrier='; cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo unknown
  printf 'operstate='; cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo unknown
  printf 'mtu='; cat "/sys/class/net/$iface/mtu" 2>/dev/null || echo unknown
  printf 'sriov_numvfs='; cat "/sys/class/net/$iface/device/sriov_numvfs" 2>/dev/null || echo unavailable
  printf 'sriov_totalvfs='; cat "/sys/class/net/$iface/device/sriov_totalvfs" 2>/dev/null || echo unavailable
  ethtool "$iface" 2>&1 | grep -E 'Speed:|Duplex:|Auto-negotiation:|Link detected:' || true
done
section "OCI CLOUD AGENT"
snap list oracle-cloud-agent 2>&1 || true
snap services oracle-cloud-agent 2>&1 || true
section "HPC PROCESSES"
ps -leaf | grep -E 'oci-rdma-authentication|oci-hpc-configure|wpa_supplicant' | grep -v grep || true
section "PER-INTERFACE WPA"
ls -la /run/wpa_supplicant 2>&1 || true
for iface in ens800f0np0 ens800f1np1; do
  [ -e "/sys/class/net/$iface" ] || continue
  echo "--- $iface ---"
  echo "WPA_STATUS_INCONCLUSIVE: control query omitted in read-only diagnostics; inspect socket/process evidence and authentication logs."
done
section "INFINIBAND AND PCI"
ls -la /sys/class/infiniband 2>&1 || true
ibv_devices 2>&1 || true
lspci -Dnn 2>&1 | grep -Ei 'Mellanox|NVIDIA.*Ethernet|15b3' || true
section "OCI RDMA AUTHENTICATION FILE LOGS"
for log in /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-hpc.log /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-rdma-authentication/oci-rdma-authentication.log; do
  [ ! -f "$log" ] || tail -120 "$log"
done
section "HPC AUTO-CONFIGURATION LOGS"
find /var/log/oracle-cloud-agent/plugins/oci-hpc/oci-hpc-configure -maxdepth 2 -type f -name '*.log' -exec tail -n 100 {} \; 2>&1 || true
section "RECENT OCA RDMA/HPC LOGS"
journalctl -u snap.oracle-cloud-agent.oracle-cloud-agent.service -b --no-pager 2>&1 \
  | grep -Ei 'rdma|hpc|wpa|802|auth|mlx5|ens800|error|fail|warning' \
  | tail -400 || true
section "RECENT MLX5/RDMA KERNEL LOGS"
journalctl -k -b --no-pager 2>&1 \
  | grep -Ei 'mlx5|rdma|infiniband|ens800|firmware|pci|link.*(down|up)|error|fail|warning' \
  | tail -500 || true
''',
    ),
}


from .host_readiness import SCRIPT as READINESS_SCRIPT
_PROFILES["dranet-readiness"] = DiagnosticProfile(
    name="dranet-readiness", title="Dranet host readiness",
    description="Checks shared RDMA mode, OCA parent IP, carrier, MTU, NRI socket, active VFs and SR-IOV boot ownership.",
    expected_seconds=10, timeout_seconds=90,
    sections=("RDMA mode", "Parent PF identity and IP", "NRI", "SR-IOV ownership"),
    script=READINESS_SCRIPT,
)

_NODE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_JOB_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer)\s+[^\s]+")
_SECRET_RE = re.compile(
    r"(?i)(authorization|bearer|password|passwd|private[_ -]?key|secret|token)\s*[:=]\s*[^\s]+"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]+PRIVATE KEY-----.*?-----END [^-]+PRIVATE KEY-----",
    re.DOTALL,
)


def profile(name: str) -> DiagnosticProfile:
    try:
        item = _PROFILES[name]
        if name in {'rdma-link-health', 'rdma-authentication', 'full-rdma-support-bundle'}:
            item = replace(item, script='(\n' + READINESS_SCRIPT + '\n) || true\n' + item.script)
        return item
    except KeyError as exc:
        raise ValueError(f"Unknown diagnostic profile: {name}") from exc


def public_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "title": item.title,
            "description": item.description,
            "expected_seconds": item.expected_seconds,
            "timeout_seconds": item.timeout_seconds,
            "sections": list(item.sections),
        }
        for item in _PROFILES.values()
    ]


def validate_node_name(value: str) -> str:
    value = value.strip()
    if not _NODE_RE.fullmatch(value):
        raise ValueError("Invalid Kubernetes node name")
    return value


def report_dir() -> Path:
    return Path(os.environ.get("HPC_CONTROL_REPORT_DIR", "/var/lib/oke-rdma-control/reports/node-diagnostics"))


def report_path(job_id: str, extension: str) -> Path:
    if not _JOB_RE.fullmatch(job_id):
        raise ValueError("Invalid diagnostic job ID")
    if extension not in {"json", "txt"}:
        raise ValueError("Unsupported report format")
    return report_dir() / f"{job_id}.{extension}"


def sanitize_output(value: str, *, max_bytes: int = 524_288) -> tuple[str, bool]:
    value = _ANSI_RE.sub("", value)
    value = _PEM_RE.sub("[REDACTED PRIVATE KEY]", value)
    value = _BEARER_RE.sub(r"\1 [REDACTED]", value)
    value = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = "".join(ch for ch in value if ch in "\n\r\t" or ord(ch) >= 32)
    encoded = value.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_bytes
    if truncated:
        encoded = encoded[:max_bytes]
        value = encoded.decode("utf-8", errors="ignore") + "\n[OUTPUT TRUNCATED]\n"
    return value, truncated


def write_reports(job_id: str, report: dict[str, Any], output: str) -> dict[str, str]:
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o750)

    json_path = report_path(job_id, "json")
    txt_path = report_path(job_id, "txt")
    json_tmp = json_path.with_suffix(".json.tmp")
    txt_tmp = txt_path.with_suffix(".txt.tmp")

    json_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    txt_tmp.write_text(output)
    os.chmod(json_tmp, 0o640)
    os.chmod(txt_tmp, 0o640)
    os.replace(json_tmp, json_path)
    os.replace(txt_tmp, txt_path)
    prune_reports()
    return {
        "json": f"/api/v1/diagnostics/reports/{job_id}/json",
        "text": f"/api/v1/diagnostics/reports/{job_id}/text",
    }


def prune_reports(*, max_reports: int = 100, max_age_days: int = 30) -> None:
    directory = report_dir()
    if not directory.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    json_files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    keep_ids = {item.stem for item in json_files[:max_reports] if item.stat().st_mtime >= cutoff}
    for path in directory.iterdir():
        if path.is_file() and path.suffix in {".json", ".txt"} and path.stem not in keep_ids:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
