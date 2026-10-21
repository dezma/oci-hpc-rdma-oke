# Deployment reference

**New users:** follow [Start here](../README.md), then the [UI walkthrough](../README-UI.md) or [operator guide](../README-OPERATOR-VM.md). The detailed reference below is supplementary.

# Deploy the playground

## Prerequisites

Prepare the following before creating the Resource Manager stack.

| Requirement | What to provide or verify |
|---|---|
| OCI access | Permission to create VCNs/subnets/NSGs, OKE clusters/node pools, Compute Clusters/instances, Bastion, Object Storage and IAM policies/dynamic groups. Dynamic groups are created at tenancy scope. |
| Placement and capacity | An AD with capacity and service limits for the requested BM.Optimized3.36 workers (two are needed for cross-node validation) and an x86_64 operator VM. |
| Worker image | An approved Ubuntu 24.04 or 22.04 HPC custom image compatible with the chosen shape and OKE node package. Include RDMA drivers/tools, Oracle Cloud Agent, and the OCI HPC configuration and RDMA authentication plugins. |
| Host readiness | OCI HPC configuration assigns the fabric PF an address in `10.224.0.0/12` and MTU 4220. The runtime exposes `/var/run/nri/nri.sock`. Use an unpartitioned PF with no competing device-management controller. Bootstrap configures shared RDMA namespace mode. |
| Kubernetes | A full OKE patch version at least 1.34, offered in the region and present in `terraform/locals.tf`'s Ubuntu package compatibility map. |
| Networking | Nonoverlapping VCN/pod/service and fabric ranges; the default IPvlan child range is `10.208.0.0/12`. NAT/service access for OS packages, bootstrap downloads and container pulls. |
| Administrator access | A workstation SSH key pair and its public source CIDR, normally `/32`. The private key stays on the workstation. |
| Tools image | A registry image built from `workloads/rdma-tools/Dockerfile`, referenced by immutable digest. Private registries also require namespace-scoped pull secrets. |

Terraform checks the live OKE version and shape/image catalog. The Control Center checks the runtime and fabric prerequisites after the nodes join. A successful infrastructure apply alone does not validate RDMA.

## 1. Prepare the tools image

On a build machine with Docker and authenticated access to your registry, set an image name for your repository, then build and push:

```bash
export TOOLS_IMAGE='<registry>/<namespace>/rdma-tools:2.5.0'
docker build --platform linux/amd64 -t "$TOOLS_IMAGE" workloads/rdma-tools
docker push "$TOOLS_IMAGE"
docker image inspect "$TOOLS_IMAGE" --format '{{index .RepoDigests 0}}'
```

Keep the resulting `repository@sha256:...` reference for the UI. The image contains perftest and the fixed host-diagnostic tools. A public image needs no pull secret. For a private image, prepare credentials through your usual registry secret-management process; see step 4.

## 2. Create infrastructure in Resource Manager

Upload `oci-oke-rdma-quickstart-orm-v2.5.0.zip` as the Terraform configuration. Its `schema.yaml` and Terraform files are at the ZIP root. Select Terraform **1.5.x**; the configuration requires 1.5.7 or later within that minor.

Complete the form in order:

1. **Deployment:** tenancy, compartment, region, name and availability domain.
2. **Private access:** administrator CIDR and operator access model. `iam_admin` provides the simplest deployment path. The restricted option requires administrator RBAC and driver bootstrap, described below.
3. **SSH and port-forwarding TTL:** independent values, each 30–180 whole minutes; defaults are 120 minutes. The Bastion maximum is set to the larger value. Changes apply to newly created sessions.
4. **OKE and CPU RDMA workers:** full Kubernetes patch, image's Ubuntu release, custom image OCID, shape and worker count.
5. **Advanced networking/operator:** review CIDRs and VM sizing. Leave optional worker SSH disabled unless needed for your operating model.

Run Plan, review the resources and costs, then Apply. Workers use a private VCN-native pod network and the private Kubernetes endpoint. The operator VM has no public IP. Keep Terraform state private because it contains privileged configuration and the bootstrap token.

The compact archive carries the application and chart. During operator bootstrap, large dependencies are fetched automatically over HTTPS: Helm from `get.helm.sh`, Python packages from `pypi.org` / `files.pythonhosted.org`, and kubectl from `dl.k8s.io`. Allow their redirects through the operator’s outbound NAT path, along with Oracle Linux repositories and OCI APIs. Bootstrap verifies the application manifest, pinned Helm hash and runtime wheel hashes before starting the UI. No separate payload upload is required.

## 3. Connect to the Control Center

Use the stack outputs `operator_ssh_session_create_command` and `operator_ui_bastion_session_create_command` to create sessions with an authenticated OCI CLI. Cloud Shell can create the sessions if supplied with the **workstation's public key**.

Run the returned SSH commands on the workstation holding the corresponding private key. For the UI tunnel, use local port **18000**, target port **8000**, and keep the tunnel open. Open `http://127.0.0.1:18000` in that workstation's browser.

In the managed SSH session on the operator VM:

```bash
sudo cat /etc/oke-rdma-control/admin.token
```

Enter this token in the UI. If bootstrap is still running, check `oke-rdma-doctor`. Keep port 8000 private. When a Bastion session expires, create a new session using the same stack output.

## 4. Configure and validate RDMA

For a private tools image, first create the workload and diagnostic namespaces from the operator VM:

```bash
kubectl apply -f workloads/dranet/namespaces.yaml
```

Run that command from a copy of the source project, or apply the file from a workstation with private cluster access. Provision a registry pull secret with the **same name** in `rdma-workloads` and `oke-rdma-diagnostics`. Enter only its name in the UI; registry passwords are never entered there.

On first login, **Setup pending** is expected until the guided workflow is completed. Missing Dranet devices before installation are not fleet faults. Active operations show their current phase and completed phase count, while finished operations show a completion time and elapsed duration. Progress counts completed phases, not an estimated time remaining.

Follow the four steps in **Setup & validation**:

1. Save the tools image digest, optional pull-secret name, PF, child CIDR and traffic class. The reference defaults are `ens800f0np0`, `10.208.0.0/12`, TOS 41 and MTU 4220.
2. Check hosts. Resolve findings for image/runtime configuration, the OCA parent address, PF state, NRI or competing ownership before proceeding.
3. Install Dranet. The UI installs the pinned chart and DeviceClass, then checks readiness and the published devices.
4. Run RDMA validation with two free workers. The test creates a ResourceClaim and pod on each node, performs one bounded RDMA-CM bandwidth test followed by one small-message RDMA latency test, records measured results, and releases its own resources.

A passing validation reports positive measured bandwidth and latency, with successful cleanup. Bandwidth uses 64 KiB writes; latency uses 8-byte writes. Both use 5,000 iterations. Latency is reported in microseconds as half the measured round-trip time, following perftest methodology; it is not an independently timed one-way transit measurement. Average, median, and available percentiles are retained with the raw output. If latency fails after bandwidth succeeds, the bandwidth evidence remains available and the validation is marked failed. No fixed throughput value is a deployment requirement. Results include the device and test settings so you can compare equivalent runs.

Download **workload YAML** from the UI, review its CPU/memory resources and container command, and apply it to launch your application. Each independently scheduled RDMA pod needs its own claim. The complete static example is `workloads/dranet/workload.yaml`; replace its image placeholder before use.

## Restricted Kubernetes access

With `operator_kubernetes_access_mode=least_privilege_rbac`, a cluster administrator must apply the full `operator_rbac_manifest` stack output to the cluster. This enables the operator's curated workload and diagnostic operations. The role does not permit Helm to create Dranet's privileged infrastructure RBAC.

After host readiness is confirmed, the cluster administrator installs the driver from a source checkout with downloaded vendor inputs:

```bash
./provisioning/fetch-vendor.sh
control/vendor/helm upgrade --install dranet control/vendor/dranet-v1.5.0.tgz \
  --namespace kube-system --values workloads/dranet/values.yaml --wait --timeout 3m
kubectl apply --server-side --field-manager=oke-rdma-bootstrap \
  -f workloads/dranet/deviceclass.yaml -f workloads/dranet/namespaces.yaml
```

Use exported UI values if you changed the PF or child range. The UI then verifies the installed driver and runs normal operations. See [Security](../SECURITY.md) for the privileges of diagnostic pods and shared RDMA devices.

## Optional worker scaling

Enable the owning Resource Manager stack connection and opt-in IAM policies using [Capacity management](CAPACITY.md). For a new stack, create it without running Apply, copy its stack OCID into `capacity_stack_ocid`, and then plan/apply. The Control Center uses the same stack and Terraform state for every reviewed node-count change.

## Select IAM ownership

Before Apply, use **IAM policies and operator identity** to select which policies the stack creates. Existing permissions can be reused independently for all four policies, and an existing operator dynamic group can be supplied by OCID. Read [IAM provisioning choices](IAM.md) to verify the operator and node-pool principals, compartment and tenancy scopes, and existing-stack behavior.


## Required operator image pin (v2.6.0)

Resource Manager requires the named image dropdown under **Advanced operator VM → Approved operator image override**. Direct Terraform requires an explicit `operator_image_ocid`; latest-image lookup is removed. Existing stacks must pin the source image already recorded in state before scaling. See the root UI/operator guides for migration and review steps.
