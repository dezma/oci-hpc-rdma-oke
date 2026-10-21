# Security and operating model

The OKE API, workers and operator VM are private. OCI Bastion is the administrator ingress path. Both OCI NSGs and the operator firewall restrict UI access; the UI remains on the private endpoint. Workspace tokens stay in browser memory, not local storage. Terraform state and operator SSH are privileged surfaces.

The default operator instance principal has cluster-scoped administrator access plus read access to OCI Compute/Instance Agent and the private payload object. The optional restricted Kubernetes role can operate curated workloads and diagnostics, but a cluster administrator must install Dranet's privileged DaemonSet/RBAC once. Restrict UI access to trusted stack administrators.

Dranet infrastructure is privileged and uses host networking. RDMA workload pods use `hostNetwork: false`, no host PID/IPC, no service-account token, `privileged: false`, no privilege escalation, all capabilities dropped except IPC_LOCK. Devices are injected through DRA. Shared PF devices and IPvlan do not provide VF hardware isolation or a security boundary for mutually untrusted tenants.

The API accepts only validated settings, fixed operations and release-shipped diagnostic profile identifiers. It has no arbitrary shell, SSH proxy, command arguments, manifest upload, node-reboot or node-drain endpoint. Mutating actions and privileged diagnostics require the environment name. The durable worker serializes operations, stores a configuration snapshot, renews leases and marks interrupted jobs failed rather than replaying mutations automatically.

Host diagnostics create bounded, short-lived privileged pods with a read-only host root mount and no service-account token. A read-only mount is not a complete sandbox for a privileged container. The protections are trusted administrator access, fixed reviewed scripts, immutable image digests, deadlines, cleanup and bounded/redacted report output. Reports do not intentionally open key or certificate-secret files. Review reports before external sharing.

The payload contains application code, the pinned Dranet chart, dependency locks and checksums. Bootstrap checks the immutable payload and every file in its internal manifest. Helm is downloaded over HTTPS and verified against its pinned executable hash. Python 3.11 wheels are downloaded for explicit Linux x86_64 platform tags and installed with exact versions and required SHA-256 hashes. Workload images must use an immutable SHA-256 reference. OCI/OFED host drivers remain owned by the image and host agents.

This is a single-operator quickstart. Enterprise SSO, per-user authorization, tamper-resistant audit export, image signing policy, formal penetration/accessibility assessment and multi-replica persistence are separate production requirements. Infrastructure destruction, node replacement and disruptive maintenance remain reviewed Resource Manager/OKE administrator operations.


## Confirmed host recovery

See [Recovery safety and interruption procedure](docs/RECOVERY.md). Recovery is a separate administrative mutation with a writable host mount, one fixed OCA restart, verified admission protection, durable intent, and fail-closed interruption handling. Workspace tokens authorize administrators; there is no per-person role/approval system. Restricted RBAC is not escalated. Coordinate an idle maintenance window.

Registry prerequisite checks request only secret type in command output. Kubernetes `get secrets` permission nevertheless permits reading full secret contents; it is not metadata-only authorization. The restricted-mode manifest scopes this permission to the two operational namespaces. Do not store unrelated secrets there. Existing restricted deployments must have an administrator review and apply the updated RBAC output.
