# Provisioning reference

For Ubuntu HPC download links and custom-image import steps, see [Create the worker image](CUSTOM-IMAGES.md).

Use the [main README](../README.md) for the quick route and ORM build commands. Consult this reference for field explanations and detailed connection steps.

## Words you will see

| Term | Plain-language meaning |
| --- | --- |
| Tenancy | Your organisation's OCI account |
| Compartment | A grouping of OCI resources with its own access policies |
| OCID | OCI's unique identifier for a resource; copy it from that resource's details page |
| Region / availability domain (AD) | Geographic region / location within the region where workers are placed |
| Resource Manager / stack | OCI's Terraform service / the saved configuration and state that own this deployment |
| OKE / worker / node pool | Managed Kubernetes / a machine that runs pods / workers managed together |
| Operator VM | The private administration machine where this tool runs; log in as `opc` |
| Bastion | OCI service for temporary, controlled SSH access to private machines |
| OCIR / tools image / digest | Container registry / software used inside test containers / exact immutable image identity |
| Dynamic group / instance principal | IAM membership for machines / the operator authenticating as itself without your API key |
| RDMA / PF / NUMA | Direct network access to memory / the physical NIC interface / CPU and memory locality within a machine |
| Dranet / DRA / ResourceClaim | Device driver / Kubernetes device allocation / a request reserving an RDMA device for a pod |
| OCA | Oracle Cloud Agent, including host configuration/authentication plugins |

An OCI **Compute Cluster** places the physical workers; an OKE **Kubernetes cluster** schedules containers. They are different resources. This design uses both.

## The complete first-time route

| Step | Where | Done when |
| --- | --- | --- |
| 1. Gather inputs and permissions | OCI Console + administrator | Worksheet and IAM ownership are agreed |
| 2. Create and apply the stack | OCI Resource Manager | Apply succeeds and stack outputs exist |
| 3. Open private access | Workstation + Bastion | Operator SSH works and, for UI users, the browser login opens |
| 4. Build image and create pull secret | Operator VM | Package check passes, image is pushed, digest recorded and both secrets exist |
| 5. Configure and validate | UI or operator API | Hosts pass, devices are published, quick pair test and cleanup pass |
| 6. Run workloads and manage workers | UI + operator, or operator alone | Your own workload starts with its claim |

Use [the UI guide](UI.md) for browser operations or [the operator VM guide](OPERATOR-VM.md) for terminal operations after step 3. Both use the same [image-build and pull-secret procedure](../docs/IMAGE-PULL-SECRET.md).

## 1. Gather your inputs

In OCI Console, select the intended region. Use Console search to find **Compartments**, **Custom Images**, **Container Registry**, **Stacks** or **Bastion** if menu labels differ.

| Input | Where to obtain it / what to choose |
| --- | --- |
| Tenancy OCID | Tenancy details; the tenancy name alone is not an OCID |
| Deployment compartment OCID | Identity & Security → Compartments → chosen project compartment |
| Region and AD | Region selector and the stack placement dropdown; use the same placement for compatible workers |
| Environment name | A unique descriptive prefix, such as `hpc-oke-rdma`; used in confirmations |
| Worker shape and count | Reference: `BM.Optimized3.36`; begin with two workers for cross-node tests |
| Approved worker image | Ubuntu HPC image compatible with the shape and OKE version, with RDMA drivers and required Oracle Cloud Agent plugins; obtain approval from your image/cluster administrator |
| Ubuntu release and OKE patch version | Must match the image and the compatibility choices accepted by the form; do not guess from an image name |
| Operator image | Select an approved image name under Advanced operator VM → Approved operator image override; the selection pins its OCID |
| Public source CIDR | Your workstation's public egress IPv4 address followed by `/32`, or your approved corporate egress range; not its private Wi-Fi address |
| SSH key pair | A workstation private key and matching public `.pub` key; only the public key is supplied to Bastion |
| Network ranges | Defaults or administrator-approved nonoverlapping VCN, pod, service and RDMA ranges |
| Registry identity | OCIR repository, user permitted to push/pull, and that user's OCI auth token |
| IAM ownership | Which group/policies already exist and which the stack is permitted to create |

Keep private keys, auth tokens and the UI admin token out of shared worksheets and support bundles. OCIDs and resource names identify resources; they are not substitutes for credentials.

### If you do not have tenancy administrator access

Ask your administrator to provide the required operator dynamic group and any policies you cannot create. In the Resource Manager form, select the corresponding reuse options and supply the existing group OCID. Disable policy creation only when the correct equivalent access exists. See [IAM setup](../docs/IAM.md).

Capacity reports use the managed node pool's **compartment**, and the operator can use compartment-scoped report permission. Other deployment and Terraform discovery operations can still require separately granted access. Your personal permissions, Resource Manager job permissions and the operator's instance-principal permissions are separate.

## 2. Deploy with Resource Manager

**Where: OCI Console.**

1. Open **Resource Manager → Stacks**, select the deployment compartment and choose **Create stack**.
2. Upload the ORM ZIP built using the commands in the main README. Terraform files and `schema.yaml` are at its root.
3. Select a Terraform version supported by Resource Manager and meeting the minimum of **1.5.7**. Later versions are allowed by the configuration but have not all been tested.
4. Fill the Deployment fields: tenancy, compartment, region, environment name and AD.
5. Review Private access: your allowed source CIDR and Kubernetes access mode. `iam_admin` enables the administrative workflow when authorised. `least_privilege_rbac` requires a cluster administrator to apply RBAC and install the driver; see [restricted access](../docs/DEPLOYMENT.md).
6. Review IAM policies and operator identity with your administrator. Do not leave creation enabled for resources you are not authorised to create.
7. Set the worker image, its Ubuntu release, full OKE patch version, shape and worker count. Two workers are needed for the first pair test; zero workers is suitable for preparing the operator first.
8. Select the approved **operator image name** in the dropdown. Pin both worker and operator images so later scaling does not also propose an image change.
9. Review advanced network settings. Keep optional worker SSH disabled unless your operating process requires it. Managed SSH and UI forwarding sessions default to two hours and can be set independently.
10. Create the stack **without running Apply yet**. If you want UI scaling, copy this stack's OCID, edit its variables, and set **Owning Resource Manager stack OCID** to that same value. Review the related scaling IAM choices.
11. Run **Plan**. Check the selected images, shape, worker count, network ranges, placement and IAM targets. Run **Apply** only after reviewing the plan.
12. Wait for Apply to succeed and open **Outputs**. Keep the stack OCID and job ID. Operator bootstrap may continue after infrastructure provisioning finishes.

**Checkpoint:** stack Apply succeeded. If it fails, read the first failing resource and error; use [troubleshooting](../docs/TROUBLESHOOTING.md). Do not repeatedly create new stacks as a first response.

The deployment includes private networking, OKE, its worker pool, Compute Cluster, operator and Bastion. The operator needs outbound HTTPS for packages, pinned tools and OCI APIs. Workers need access to their packages, OCI services and container registry. See [deployment reference](../docs/DEPLOYMENT.md) for network and image prerequisites.

## 3. Connect privately

**Workstation** means the computer on which you will open the browser and keep your SSH private key. **Cloud Shell** can create OCI sessions, but its `localhost` is not your workstation's `localhost`.

1. In stack Outputs, find `operator_ssh_session_create_command` and `operator_ui_bastion_session_create_command`.
2. Run the SSH session-create command in an authenticated OCI CLI, such as Cloud Shell. Substitute the path to your **workstation's public key**. If using Cloud Shell, upload only the public `.pub` file there.
3. Open the created Bastion session in OCI Console, wait for **Active**, and copy its SSH connection command.
4. On your workstation, replace the private-key placeholders in that command with the path to the matching private key. Run it in **Terminal A**. Expected prompt: `opc@...operator...`.
5. For UI use, create the separate port-forwarding session from the UI output. Copy its SSH command, use local port **18000** and target port **8000**, and run it in workstation **Terminal B**. Leave that terminal open; a quiet tunnel is normal.
6. Open `http://127.0.0.1:18000` in the browser on that workstation. If local port 18000 is occupied, choose another local port in both the tunnel command and browser URL; keep target port 8000.
7. In Terminal A, retrieve the UI token:

```bash
sudo cat /etc/oke-rdma-control/admin.token
```

8. Enter it in the browser and select **Open workspace**. Verify the environment name.

A session expiry requires a new Bastion session and connection, not a new cluster. Keep port 8000 private. Do not copy your private SSH key onto the operator.

**Checkpoint:** operator SSH works; UI users can log in. Continue with [UI setup](UI.md) or [terminal setup](OPERATOR-VM.md).

