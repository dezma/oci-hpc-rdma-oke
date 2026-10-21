# OKE HPC Control Center

Deploy a private Kubernetes environment for CPU RDMA workloads on Oracle Cloud Infrastructure (OCI), then configure, test and operate it through a web UI or an operator VM.

![architecture](docs/oci-hpc-arc.png)

**Start here.**This project creates billable infrastructure. Review your organisation's limits, budget and data-retention requirements before Apply

## Get the project and build the ORM ZIP

1. Clone your GitHub repository. Replace the example URL with your actual repository URL:

```bash
git clone https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
cd YOUR-REPOSITORY
```

If you downloaded the source ZIP instead, extract it and open a terminal in its project folder. You should see `README.md`, `VERSION`, `terraform/`, `control/`, `provisioning/` and `workloads/`.

2. Check that the required commands are installed:

```bash
for tool in bash git python3 curl tar sha256sum; do
  command -v "$tool" || printf 'Missing prerequisite: %s\n' "$tool"
done
```

Install any missing prerequisite using your Linux distribution's package manager before continuing.

3. Build the deployment ZIP from the project root. The permission command also handles source files uploaded to GitHub through a browser without executable permissions:

```bash
chmod +x provisioning/*.sh control/bootstrap/*.sh
bash provisioning/build-orm.sh
```

The script verifies pinned vendor downloads, builds the operator application payload, and packages Terraform with `schema.yaml` at the ZIP root. Wait for the script to finish successfully. If downloads fail, check outbound connectivity and retry; do not bypass checksum verification or use an incomplete archive.

4. Locate and verify the generated archive:

```bash
ls -lh dist/*-orm-*.zip
(cd dist && sha256sum -c SHA256SUMS)
```

The filename is `dist/oci-oke-rdma-quickstart-orm-v<VERSION>.zip`, where `<VERSION>` comes from the project's `VERSION` file. The checksum check should report `OK`. If you built on a remote machine, download that ZIP to your workstation for the Console upload.

5. Complete the OCI preparation below, then upload the generated ORM ZIP when creating the Resource Manager stack. Upload the ORM ZIP itself, without extracting or wrapping it in another ZIP. Do not upload the GitHub source ZIP as the stack configuration.

Rebuild the ORM ZIP after changing application, Terraform or bundled documentation files. Keep generated archives and payloads out of GitHub; the repository's `.gitignore` excludes `dist/` and the generated payload directory. All user guides stay in the project; no separate guides archive is required.



### If you do not have tenancy administrator access

Ask your administrator to provide the required operator dynamic group and any policies you cannot create. In the Resource Manager form, select the corresponding reuse options and supply the existing group OCID. Disable policy creation only when the correct equivalent access exists. See [IAM setup](docs/IAM.md).

Capacity reports use the managed node pool's **compartment**, and the operator can use compartment-scoped report permission. Other deployment and Terraform discovery operations can still require separately granted access. Your personal permissions, Resource Manager job permissions and the operator's instance-principal permissions are separate.

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

## 2. Deploy with Resource Manager

**Where: OCI Console.**

1. Open **Resource Manager → Stacks**, select the deployment compartment and choose **Create stack**.
2. Upload the ORM ZIP supplied in this package. Terraform files and `schema.yaml` are at its root.
3. Select Terraform **1.5.x**, meeting the configuration's minimum of 1.5.7.
4. Fill the Deployment fields: tenancy, compartment, region, environment name and AD.
5. Review Private access: your allowed source CIDR and Kubernetes access mode. `iam_admin` enables the administrative workflow when authorised. `least_privilege_rbac` requires a cluster administrator to apply RBAC and install the driver; see [restricted access](docs/DEPLOYMENT.md).
6. Review IAM policies and operator identity with your administrator. Do not leave creation enabled for resources you are not authorised to create.
7. Set the worker image, its Ubuntu release, full OKE patch version, shape and worker count. Two workers are needed for the first pair test; zero workers is suitable for preparing the operator first.
8. Select the approved **operator image name** in the dropdown. Pin both worker and operator images so later scaling does not also propose an image change.
9. Review advanced network settings. Keep optional worker SSH disabled unless your operating process requires it. Managed SSH and UI forwarding sessions default to two hours and can be set independently.
10. Create the stack **without running Apply yet**. If you want UI scaling, copy this stack's OCID, edit its variables, and set **Owning Resource Manager stack OCID** to that same value. Review the related scaling IAM choices.
11. Run **Plan**. Check the selected images, shape, worker count, network ranges, placement and IAM targets. Run **Apply** only after reviewing the plan.
12. Wait for Apply to succeed and open **Outputs**. Keep the stack OCID and job ID. Operator bootstrap may continue after infrastructure provisioning finishes.

**Checkpoint:** stack Apply succeeded. If it fails, read the first failing resource and error; use [troubleshooting](docs/TROUBLESHOOTING.md). Do not repeatedly create new stacks as a first response.

The deployment includes private networking, OKE, its worker pool, Compute Cluster, operator and Bastion. The operator needs outbound HTTPS for packages, pinned tools and OCI APIs. Workers need access to their packages, OCI services and container registry. See [deployment reference](docs/DEPLOYMENT.md) for network and image prerequisites.

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

**Checkpoint:** operator SSH works; UI users can log in. Continue with [UI setup](README-UI.md) or [terminal setup](README-OPERATOR-VM.md).

## What a completed setup proves

Infrastructure Apply, Kubernetes Ready, host RDMA readiness, Dranet device readiness and measured cross-node performance are separate checks. Complete each in order. A passing pair test proves that pair worked at that time; it does not measure every worker combination or guarantee your application's performance.

Each independently allocated RDMA pod consumes one worker NIC slot in the reference profile. Multiple processes may run within that pod. Increasing worker count adds slots; it does not resize an already-running MPI application.

## Find help

| Need | Guide |
| --- | --- |
| Build/push the image and create credentials | [Image and registry setup](docs/IMAGE-PULL-SECRET.md) |
| Web operations | [UI guide](README-UI.md) |
| Terminal operations and API helper | [Operator VM guide](README-OPERATOR-VM.md) |
| Access, image, host, performance or scaling error | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Failed host diagnostic | [Diagnostic troubleshooting](docs/DIAGNOSTIC-TROUBLESHOOTING.md) |
| Metrics and their limitations | [Performance](docs/PERFORMANCE.md) |
| Scale workers | [Capacity](docs/CAPACITY.md) |
| Restart an eligible worker agent | [Recovery](docs/RECOVERY.md) |
| Replace a defective worker | [Replacement](docs/REPLACEMENT.md) |
| Stop workloads, pause capacity or remove deployment | [Operations](docs/OPERATIONS.md) |
| Privilege model | [Security](SECURITY.md) |
