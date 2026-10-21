# Architecture

## Infrastructure and ownership

Resource Manager provisions a private enhanced OKE cluster, VCN-native pod networking, an OCI Compute Cluster, a managed CPU RDMA node pool, an operator VM, private payload storage and OCI Bastion. The worker image and Oracle Cloud Agent own the physical RDMA drivers, fabric configuration and authentication. Dranet manages workload access to the selected fabric NIC.

| Component | Responsibility |
|---|---|
| Resource Manager / Terraform | Network, IAM, OKE, managed workers, Compute Cluster, operator and Bastion |
| Ubuntu HPC image / OCI agents | Host drivers, physical fabric interface, parent address, authentication and host services |
| Dranet DaemonSet | NIC discovery, DRA ResourceSlices, claim preparation, IPvlan and device injection through NRI |
| Kubernetes scheduler | Match ResourceClaims to nodes and available devices |
| Control Center API and worker | Configuration, fixed operations, readiness checks, job state, results and exports |
| Workload owner | Container image, application resources, workload lifecycle and claim cleanup |

The operator API and durable worker run as separate services. The worker serializes operations and records settings with each job. Diagnostic and validation output remains attached to the operation that produced it.

## Workload networking

The primary pod interface is the normal OKE VCN-native interface. Dranet adds an IPvlan child on the selected physical RDMA NIC using the OKE cloud profile. The PF stays on the host, and the child shares its MAC address. The workload uses its own network namespace with `hostNetwork: false`.

The profile requires shared RDMA namespace mode. The pod accesses the physical RDMA device; the additional IPvlan interface supplies the pod's fabric address and routing. The OCA parent range is `10.224.0.0/12`; the default child range is `10.208.0.0/12`. Child, parent and VCN/service ranges must not overlap. The parent address must be present before a claim is prepared.

The reference fabric MTU is 4220. Validation uses RDMA-CM and binds the child IPv4 address; the raw perftest report retains the GIDs selected by the driver. Applications should use the discovered device and addresses rather than assume a fixed GID index.

## Allocation and capacity

The profile publishes one eligible physical RDMA NIC per reference worker. A ResourceClaim reserves that device for one RDMA-consuming pod. A retained claim continues reserving capacity even if its pod has finished. Multiple application processes can share the device within a pod, and ordinary pods can coexist on the node.

ResourceClaims and scheduler affinity select devices and nodes together. Workload manifests do not bypass the scheduler with `spec.nodeName`. A two-node validation requires two Ready, schedulable workers with free claims.

## Access and isolation

Bastion provides managed SSH and port forwarding to the private operator. The browser authenticates with an operator token held only in memory. The infrastructure Dranet daemon and fixed host-diagnostic pods are privileged. Application pods are unprivileged, have no host PID/IPC namespace or service-account token, and retain only the IPC_LOCK capability required by the profile.

Shared PF access is suitable for trusted HPC workloads. It does not provide dedicated VF isolation or an authorization boundary between untrusted tenants. Review [Security](../SECURITY.md) before broadening access.
