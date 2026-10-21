# Security and operating model

## Access

The OKE API, workers and operator VM are private. Administrators connect through OCI Bastion. NSGs and the operator firewall restrict UI access. Browser tokens are held in memory. Protect Terraform state, operator SSH access and the UI token as administrator credentials.

The default operator instance principal has cluster-scoped administrator access, OCI Compute/Instance Agent read access and access to its private payload object. The optional restricted Kubernetes role supports the supplied workloads and diagnostics. A cluster administrator must install Dranet and perform recovery or replacement. Restrict UI access to trusted administrators; the application uses a shared administrator token and has no per-user approval roles.

## Containers and devices

Dranet runs as privileged infrastructure with host networking. Standard RDMA workload pods use their own network namespace, no host PID/IPC namespaces, no service-account token and only the IPC_LOCK capability. DRA supplies the device. Shared PF access and IPvlan are intended for trusted HPC workloads; use an appropriate isolation design for mutually untrusted tenants.

Host diagnostics run fixed scripts in short-lived privileged pods with a read-only host root mount. Profile execution limits range from 90 to 180 seconds. Privileged containers still require trusted administrator access, trusted image digests and restricted pod/exec permissions in the diagnostics namespace. Reports limit output and redact common credential patterns. Review exported reports before sharing them.

MPI test pods add the capabilities needed by their temporary SSH server. NUMA test pods add SYS_NICE for CPU/memory policy operations. See [performance measurement](docs/PERFORMANCE.md) for the test-specific requirements.

## Operations

The API accepts validated settings and fixed operations. It offers no arbitrary shell, manifest upload, node reboot or general drain endpoint. Privileged diagnostics and changes require an environment-name confirmation. The worker serializes jobs, records their settings and marks interrupted jobs failed. Review the recorded operation before retrying an uncertain remote action.

Recovery restarts Oracle Cloud Agent once, using a writable host mount. It blocks new pods on the worker while it runs and records the restart before submission. If interrupted, it retains protection for review instead of retrying the restart. See [recovery and interruption handling](docs/RECOVERY.md).

Worker replacement submits one reviewed OKE DeleteNode request with pool decrement and forced termination disabled. It requires an idle worker and released claims. Scaling uses a reviewed Resource Manager plan. Both require infrastructure permissions beyond the restricted Kubernetes role; see [capacity](docs/CAPACITY.md) and [replacement](docs/REPLACEMENT.md).

Registry prerequisite checks print only the secret type. Kubernetes `get secrets` permission also allows reading the secret contents. Restricted RBAC limits this grant to the two operational namespaces; keep unrelated secrets elsewhere. Have a cluster administrator review and apply the RBAC output for existing restricted deployments.

## Dependencies

The operator payload includes application code, the pinned Dranet chart and dependency checksums. Bootstrap verifies the payload and its files. Helm is downloaded over HTTPS and checked against its pinned hash. Python 3.11 wheels use exact versions, SHA-256 hashes and Linux x86_64 platform tags. Workload images use immutable SHA-256 references. Host drivers are supplied by the worker image and OCI agents.

## Monitoring and notifications

Scheduled monitoring starts disabled. Saving settings and approving a profile require the administrator token and environment-name confirmation. Passive sweeps read inventory and cached host checks. Optional host refresh runs one fixed privileged diagnostic on an idle worker per sweep. Nightly pair tests are a separate opt-in and generate RDMA traffic. Active checks skip while any RDMA claim exists; workers occupied by non-system, non-DaemonSet pods are excluded. External controllers can still schedule concurrently, so pair tests allocate devices through DRA.

The private `/metrics` endpoint accepts a separate read-only token or the administrator token. The metrics token cannot access administration APIs. Keep scraping private or tunnelled; protect bearer tokens in transit. Notification messages contain worker/claim identifiers and status, with no raw diagnostic logs or registry credentials. The operator publishes to an existing OCI Notifications topic using its instance principal. Alerts never trigger repair or replacement. See [monitoring](docs/MONITORING.md) for configuration and permissions.

## Deployment scope

This project uses one operator VM. Before broader production use, assess SSO, per-user authorization, external audit retention, image signing, backup/recovery and availability requirements for your organisation. Keep disruptive maintenance and infrastructure destruction under administrator review.
