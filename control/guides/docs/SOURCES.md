# Primary references

Use the pinned Dranet documentation with this release. Verify OCI regional availability and image compatibility when choosing infrastructure inputs.

- [Dranet v1.5.0 OKE profile](https://github.com/kubernetes-sigs/dranet/blob/v1.5.0/site/content/docs/user/oci-oke-rdma.md) — OKE prerequisites, IPvlan, shared devices, allocation and child addressing.
- [Dranet v1.5.0 Helm chart](https://github.com/kubernetes-sigs/dranet/tree/v1.5.0/deployments/helm/dranet) — chart values and runtime requirements.
- [Oracle HPC OKE quickstart](https://github.com/oracle-quickstart/oci-hpc-oke) — HPC infrastructure and Kubernetes integration examples.
- [Kubernetes Dynamic Resource Allocation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) — DeviceClass, ResourceSlice, ResourceClaim and scheduling concepts.
- [OCI Ubuntu node-package compatibility](https://docs.oracle.com/en-us/iaas/Content/ContEng/Concepts/contengCompatibilitymatrix_topic.htm) — selecting the package for an OKE minor version.
- [OCI Bastion port-forwarding sessions](https://docs.oracle.com/en-us/iaas/Content/Bastion/Tasks/create-session-port-forwarding.htm) — session creation, connection and lifetime.
- [OCI service limits](https://docs.oracle.com/en-us/iaas/Content/General/service-limits/default.htm) — Resource Manager stack ZIP upload limit.
- [OCI Resource Manager](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/home.htm) — stacks, jobs, Terraform configuration and state.

The dependency lock in `control/vendor/lock.json` records the release versions and SHA-256 hashes used to build the operator payload. Third-party licenses are retained in `control/vendor/`.

- [Oracle BM.Optimized3.36 OSU Micro-Benchmarks tutorial](https://oracle-japan.github.io/ocitutorials/hpc/benchmark/run-omb-hpc/): shape-specific latency and bandwidth reference, with explicit benchmark settings.

- [Resource Manager API and permission reference](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/resourcemanagerpolicyreference.htm): stack/job permissions and PLAN/APPLY restrictions.
- [Resource Manager security](https://docs.oracle.com/en-us/iaas/Content/Security/Reference/resourcemanager_security.htm): resource access required by Terraform jobs.
- [OKE node-pool scaling](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengscalingnodepools.htm): Compute Cluster placement and managed pool resizing.
