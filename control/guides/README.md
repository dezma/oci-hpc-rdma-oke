# OKE HPC Control Center — deployment and user guide

Documentation for release **2.5.0**, revised 19 October 2026. These guides accompany the full release. Reading or downloading a guide does not change infrastructure.

## Choose your route

| Guide | Audience and outcome |
|---|---|
| [Web UI guide](README-UI.md) | Operate the five task pages, configure RDMA, validate hosts, install Dranet, measure performance, manage capacity and reset setup. |
| [Operator VM guide](README-OPERATOR-VM.md) | Complete setup and operations from SSH without a browser, repair registry secrets, collect Kubernetes evidence and use the local control API. Also covers direct Helm administration. |

Read this file for provisioning and access, then follow **one** operating guide. Both routes operate the same environment. Do not submit competing operations from the browser, terminal, Resource Manager and other automation.

## What the deployment provides

The reference deployment provisions a private OKE cluster, a managed CPU RDMA worker pool in an OCI Compute Cluster, networking, an operator VM and Bastion access. Dranet v1.5.0 publishes physical RDMA NICs through Kubernetes Dynamic Resource Allocation (DRA). Workload pods retain their own network namespace and receive an IPvlan child interface.

For the single-NIC reference profile, one independently allocated RDMA pod consumes one worker's RDMA slot. Multiple processes may run inside that pod. Ordinary pods can coexist. Adding workers adds capacity; it does not expand an already-running MPI job.

There are separate completion gates:

1. **Infrastructure provisioned:** Resource Manager Apply succeeds.
2. **Workers ready:** Kubernetes nodes join and become Ready.
3. **Host prerequisites verified:** inventory establishes the PF, address, MTU, shared RDMA mode and runtime requirements.
4. **Dranet ready:** the driver publishes usable devices.
5. **Cross-node validation passed:** a real pair of workers completes bandwidth and latency tests.

A successful Terraform Apply alone does not prove RDMA works.

## First-time user route and checkpoints

Follow this route in order. Stop at a failed checkpoint and use the named troubleshooting section; do not skip ahead to driver installation or reset.

| Stage | Where you work | Completion checkpoint | If it fails |
|---|---|---|---|
| Prepare | Workstation + OCI Console | Worksheet complete and registry access identified | Resolve missing inputs before creating resources. |
| Provision | OCI Resource Manager | Apply succeeded; outputs available | Read the exact failing resource/job. Do not recreate the stack as a first response. |
| Access | Workstation → Bastion → operator | SSH succeeds; local browser reaches login | Check source CIDR, matching SSH key, session expiry and operator health. |
| Configure | UI or operator | Digest saved and pull secret verified in both namespaces | Registry preparation in operator guide section 2. |
| Check hosts | UI or operator | Current inventory passes on intended workers | Diagnose collection first, then actual host readiness. |
| Install | UI or cluster administrator | Driver ready and devices published | Review privilege mode, driver logs and profile consistency. |
| Measure | UI or operator | Two-worker bandwidth AND latency complete; cleanup succeeds | Review the exact failed stage and reservations. |
| Application | Operator | Example pod consumes a claim and exposes its extra interface | Inspect pod events, allocation and image access. |

### Your deployment worksheet

Keep this privately. Record identifiers, not passwords/tokens in a shared document.

| Value to collect | Where to get it | Used at |
|---|---|---|
| Tenancy OCID | OCI Console tenancy details | Provisioning/IAM scope |
| Compartment OCID | Identity & Security → Compartments → chosen compartment | Provisioning/resource scope |
| Region and AD | OCI region selector and region availability domains | Worker placement |
| Environment name | Choose a unique descriptive prefix | Resource names and typed confirmations |
| OKE version | Versions offered for this region and accepted by the release | Cluster/node compatibility |
| Worker image OCID + Ubuntu release | Compute → Custom Images → approved HPC image | Worker initialization |
| Shape/count | Capacity planning and limits | 0–64 workers; default 2 |
| Operator dynamic-group OCID | IAM dynamic group details, if reusing | Instance-principal identity; verify matching rule |
| IAM ownership decisions | Administrator review of required statements | Four independent policy switches |
| Administrator public CIDR | Your workstation's current public egress address | Bastion client access |
| SSH public key file | Workstation `.pub` file matching your private key | Bastion session creation |
| Registry hostname | Host portion of your actual image repository | Registry login and pull secret |
| Registry username/auth token | Registry account and approved credential issuance | Secret creation only; never UI/support report |
| Tools image digest | Operator Podman push digest output | UI workload configuration |
| Stack OCID | Resource Manager stack details after stack creation | Optional scaling ownership |
| Operator IP/Bastion outputs | Successful stack Outputs | Private access |

### Command-location legend

- **WORKSTATION:** your laptop/desktop, holding the SSH private key. Keep the SSH private key here; image builds run on the operator VM.
- **CLOUD SHELL:** optional place to issue authenticated OCI session-create commands. It does not automatically hold your workstation's private key or share its localhost.
- **OPERATOR VM:** the `opc@...operator...` SSH session. Build/push the tools image and run `kubectl`, the local API helper and operator health checks here.
- **WORKER VM:** the `ubuntu@oke-...` session on a specific worker. Use only for explicitly labelled host troubleshooting. Worker SSH is optional and may be disabled.

A browser pointed at `127.0.0.1` connects to that browser's machine. Open the UI tunnel on the same workstation where you open the browser. Do not paste an operator-only API command into Cloud Shell or a worker.

## 1. Prepare before provisioning

| Input | Requirement |
|---|---|
| Placement | Region/AD with capacity and limits for your selected bare-metal shape; reference shape BM.Optimized3.36. |
| Worker image | Approved Ubuntu HPC image compatible with the shape and selected OKE version, including RDMA drivers/tools and Oracle Cloud Agent HPC/RDMA plugins. |
| Kubernetes version | Full supported patch version accepted by the release's compatibility map and live OKE catalog. |
| Worker count | **0–64**, default **2**. One worker is allowed; cross-node validation needs two free, ready workers. |
| Network | Nonoverlapping VCN, pod, service and fabric ranges, plus outbound registry/package/API access. |
| Access | Workstation SSH key pair and administrator source CIDR. Keep the private key on your workstation. |
| Registry | Approved repository and credentials; build/push the tools image on the operator after provisioning. |
| IAM | Provisioning identity permissions plus operator instance-principal and node-pool permissions, whether created by this stack or supplied externally. |

Build and publish the tools image **after provisioning, on the operator VM**. Follow the linear [image build and pull-secret walkthrough](docs/IMAGE-PULL-SECRET.md): repository → token → Podman → Dockerfile → login → build/push → digest → namespace secrets → UI configuration → validation. Both UI and terminal guides include the complete steps. You may keep workers at zero during preparation.

## 2. Decide who owns IAM

All creation switches default to enabled. Disable a switch only when equivalent access already exists for the correct identity and scope.

| Resource Manager choice / variable | Meaning |
|---|---|
| `create_operator_dynamic_group` | Create the operator identity group; this is **not** a policy. |
| `existing_operator_dynamic_group_ocid` | Required when reusing a group; its matching rule must include the new operator VM. Leave empty when creating a group. |
| `create_operator_policy` | Create baseline operator access. |
| `create_nodepool_compute_cluster_policy` | Authorize the OKE node-pool principal to launch workers into the Compute Cluster. |
| `create_capacity_compartment_policy` | Create compartment permissions for optional scaling. |
| `create_capacity_tenancy_policy` | Create tenancy discovery/IAM read permissions for scaling refresh. |

Scaling policy creation also requires `capacity_stack_ocid` to be populated. A compartment-wide grant to an operator dynamic group does not establish tenancy access or the separate node-pool-principal grant. Supplying an existing group OCID does not change membership or grant permissions.

For an existing stack, disabling creation of an already-managed resource can plan its deletion. Review the plan and establish replacement access first. Do not delete state entries as a routine workaround.

## 3. Provision with Resource Manager

1. Create a stack using `oci-oke-rdma-quickstart-orm-v2.5.0.zip`. Its Terraform files and `schema.yaml` are at ZIP root.
2. Use Terraform 1.5.x meeting the release requirement of at least 1.5.7.
3. Enter tenancy, deployment compartment, region, environment name and availability domain.
4. Set administrator CIDR, SSH public key and access mode. `least_privilege_rbac` requires cluster-administrator RBAC/bootstrap work; do not expect that mode to install privileged infrastructure unaided.
5. Select the full OKE version, compatible Ubuntu release/image OCID, shape and count. Retain two workers for immediate cross-node testing.
6. Review advanced networking/operator settings and IAM ownership choices above.
7. If UI scaling is required, create the stack **without running Apply**, copy its stack OCID, then set `capacity_stack_ocid` to that same stack before Plan/Apply.
8. Generate Plan and inspect changes. On a new stack, verify the selected shape, image, placement and IAM targets. On an existing stack, inspect replacements and bootstrap/image changes carefully.
9. Apply the reviewed configuration. Retain the job ID and outputs.

The operator fetches pinned dependencies during bootstrap. Permit outbound access to the release's package and image sources, including Helm, kubectl, Python packages, OS repositories and OCI services. Infrastructure completion may precede operator bootstrap completion.

Zero-worker deployments retain the cluster/operator and can be scaled up later. They cannot run workload/driver pods on this pool until workers exist. An initially empty deployment still needs the first host check and Dranet installation after scale-up.

## 4. Connect privately

1. In stack outputs locate `operator_ssh_session_create_command` and `operator_ui_bastion_session_create_command`.
2. Run the appropriate session-create command using an authenticated OCI CLI. If using Cloud Shell to create the session, supply the **workstation's public key**.
3. Run the returned SSH connection command on the workstation holding the corresponding private key.
4. For web access, keep a port-forwarding session open from local port **18000** to operator port **8000**. Open `http://127.0.0.1:18000` on that workstation.
5. In the operator SSH session, retrieve the login token:

```bash
sudo cat /etc/oke-rdma-control/admin.token
```

Enter it in the UI; do not paste it into support reports. Keep port 8000 private. Managed-SSH and UI session lifetimes default to 120 minutes, independently configurable from 30 to 180 minutes. When one expires, create a new session; no cluster redeployment is needed.

If access fails, run `oke-rdma-doctor` on the operator. The operator guide includes service and bootstrap checks.

### Access walkthrough: from stack outputs to first login

**OCI CONSOLE / AUTHENTICATED CLI**

1. Open the owning stack → Outputs. Copy the managed-SSH session creation command and run it using your authenticated OCI CLI. Substitute the real public-key path where required.
2. Open the created Bastion session details and copy its SSH command. Replace private-key placeholders with the matching private key on your workstation. Wait until the session is Active.
3. Separately create the port-forwarding session from the UI session-create output. Copy its SSH command and choose local port 18000 with target operator port 8000. Do not swap session OCIDs or use a key from another machine.

**WORKSTATION**

4. Terminal A: run the managed-SSH connection command. Expected prompt: `opc@...operator...`.
5. Terminal B: run the UI port-forwarding command and leave it open. A quiet terminal is normal for a working tunnel; closing it closes the tunnel.
6. Browser: open `http://127.0.0.1:18000`. Expected: **Cluster Administration** login.

**OPERATOR VM (Terminal A)**

7. Run `sudo cat /etc/oke-rdma-control/admin.token`, enter it in the browser and select **Open workspace**. Expected: the environment name and five task pages.
8. Check that the environment is the one you just created. Open **Setup & validation** and continue with the UI guide.

If port 18000 is already in use, select another local port in the tunnel command and use the same port in the browser URL. Do not change target port 8000. If a session expires, create a replacement session and reconnect; refreshing the browser alone cannot renew Bastion.

### Move a workload file to the operator

The browser download lands on your workstation. It does not appear on the operator automatically. Use an approved file-transfer route over the same Bastion SSH access. For `scp`, reuse the `ProxyCommand`, private-key paths and target from the working managed-SSH command; do not use a port-forwarding-only session as an SSH shell. For a small nonsecret YAML, an alternative is to open an editor on the operator, paste the complete downloaded YAML and save it as `rdma-workload.yaml`. Then run `kubectl apply --dry-run=server -f rdma-workload.yaml` there before applying it. Never transfer your private SSH key to the operator.

## 5. Finish post-provisioning setup

Choose the [UI guide](README-UI.md) or [operator VM guide](README-OPERATOR-VM.md). In either route:

1. Prepare registry credentials in **both** `rdma-workloads` and `oke-rdma-diagnostics` for a private tools image.
2. Save the image digest and network settings.
3. Collect successful host inventory.
4. Install or verify Dranet.
5. Measure bandwidth and latency with two free workers.
6. Deploy your own complete ResourceClaim + Pod manifest.

## Troubleshooting entry points

| Symptom | Start here |
|---|---|
| `FailedToRetrieveImagePullSecret (ocir-pull)` | Operator guide, registry section: check the secret in the **pod's namespace**. |
| Empty `facts: {}` plus many readiness failures | Operator guide, diagnostic evidence section. Missing inventory does not prove every host prerequisite failed. |
| No free slots / allocated workers | Inspect ResourceClaims and consumers before deleting anything. |
| Tenancy policy creation fails | Check provisioning caller scope and IAM creation switches. Existing operator permissions are a separate question. |
| Scaling includes IAM/image/eviction changes | Reconcile the baseline separately at the current count; do not bypass the size-only guard. |
| Old failed validation visible | Operations history is not current health; inspect measurement freshness and the latest attempt. |

## Scope and documentation verification

These guides were checked against the available v2.5.0 source, UI labels, API models and bootstrap paths. Browser screenshots could not be captured because Chromium fails to launch in the build environment; no mock screenshots are presented as verified UI. They are not evidence of a live deployment test. Examples use placeholders where an environment-specific value is required. The operator guide's terminal workflow uses the installed control service for guarded operations; its Helm section is an explicitly separate administrator route.

Upstream reference for namespace-scoped registry secrets: [Kubernetes: pull an image from a private registry](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/).


## Pin the operator image before worker scaling

Resource Manager shows a dropdown of image **names**, not a text field called `operator_image_ocid`. In the stack's variable form, expand **Advanced operator VM** and find **Approved operator image override**. That dropdown saves the selected image's OCID in the internal Terraform variable `operator_image_ocid`. Image pinning is a deployment requirement in v2.6.0 for both Resource Manager and direct Terraform. There is no automatic latest-image lookup.

For a new deployment, choose a specific approved Oracle Linux 9 image compatible with the operator shape. Record both its full name and OCID. For an existing deployment, choose the image already recorded for the operator, not the newest image. The legacy empty setting selects the newest compatible image on each plan, so a later scale plan can also propose an operator image change. An application-only operator update does not change the stack's form or saved variables.

If scaling is blocked on `oci_core_instance.operator` and `source_details[0].source_id`, inspect the recorded plan first. Obtain the **before** image OCID, then look up its display name in OCI Compute Images or from the operator VM:

```bash
# Run on the operator VM. Substitute the before-image OCID from the recorded plan.
oci compute image get --image-id '<RECORDED_IMAGE_OCID>' \
  --auth instance_principal --query 'data.{name:"display-name",id:id}'
```

If image lookup is denied, use an administrator with image-read access. If the old image is unavailable or absent from the dropdown, stop and review the stack variable with an administrator; do not substitute a similarly named or newer image.

1. Copy the recorded image name and OCID before discarding the plan.
2. In the Control Center choose **Discard target**. This restores the previous worker-count target without resizing workers.
3. Edit the Resource Manager stack variables. Under **Advanced operator VM → Approved operator image override**, select the matching image name and verify its OCID. Keep the worker count at the current count (three in the reported case). Save the variables.
4. Refresh capacity in the Control Center and generate a fresh plan for the intended worker count. Only the worker-count change should remain. Investigate any other changed resource before applying.

For direct Terraform use, set `operator_image_ocid` explicitly in your tfvars. An empty or missing value is rejected; no latest-image fallback is available. Image upgrades are separate maintenance changes requiring their own plan review. Do not weaken the size-only scaling gate or add lifecycle ignores to hide an unreviewed image change.


## v2.6.0 deployment gate and performance evidence

Before deployment, select an approved operator image using **Advanced operator VM → Approved operator image override** in Resource Manager. The dropdown displays image names. For an existing deployment, select the image already recorded for `oci_core_instance.operator` in Terraform state, then verify the OCID. Do not choose a newer image just to satisfy the required field. Direct Terraform deployments must set `operator_image_ocid` explicitly.

Before a capacity plan changes `node_count`, the application checks that the saved image matches the recorded image. Missing, mismatched, or unreadable image evidence blocks planning without changing the scale target. The existing size-only plan review remains mandatory. Image upgrades are separate maintenance operations, not part of resizing.

The application-only update does not replace your Resource Manager configuration or save its variables. Existing stacks must still save the correct image selection. Review any Terraform bundle migration separately; do not apply unrelated changes to make a scale plan pass. If an older pending plan already saved a new target, discard that unapplied plan first, verify the restored target and image selection, then create a fresh plan. Do not discard an applied operation; resume or reconcile it using the capacity guide.

### What the performance view measures

| Feature | Meaning | Limitation |
|---|---|---|
| Directional pair matrix | Latest attempt for each selected server/client direction | Untested directions remain unmeasured; no automatic all-pairs traffic |
| Baseline comparison | Bandwidth and mean-latency change against the earliest comparable retained successful run | Requires matching recorded worker identities, kernel/NIC evidence, settings, profile and tools image; not proof of cause |
| Message-size sweep | RDMA-write bandwidth and latency at 8 B, 64 B, 512 B, 4 KiB, 64 KiB and 1 MiB | Fixed 5,000 iterations per sample; up to four additional minutes |
| Latency under concurrent traffic | Idle versus concurrent RDMA-write latency, with a 30-second background bandwidth run | Confirms process overlap and positive throughput; does not prove continuous line-rate saturation |
| Observed timeline | Capacity phase transitions, Kubernetes Ready observation, device-check completion, and validation stages | Observation times are not independent OCI boot, OCA authentication, or fabric convergence measurements |

Latency is perftest's half-round-trip measurement, not synchronized one-way latency. Missing metrics are never zero-filled. Failed attempts, changed worker boot identities, changed settings and measurements older than 24 hours are distinguished from current measurements. The matrix uses at most 100 retained jobs and reset boundaries. Older runs without sufficient provenance cannot establish a baseline. Worker source image OCIDs and actual CPU/NUMA placement are not collected; comparisons therefore describe observed software and NIC conditions, not complete hardware equivalence.

Deep tests require free claims and application-idle endpoints. They deliberately generate traffic and can affect other traffic on shared infrastructure. Run them during a suitable test window. Each extended profile has a four-minute budget; individual subprocesses and validation pods also have time limits. A timeout is a failed or inconclusive measurement, not a low-bandwidth result. Owned pods and claims are cleaned up after the attempt; inspect cleanup findings before retrying.

Start with quick validation on two healthy workers, repeat the same direction and settings to establish a baseline, then run a sweep or concurrent-traffic test. Keep a known-good report before image, driver or capacity changes. A different profile has a separate comparison key. MPI collectives, NUMA benchmarks and automatic full-fabric sweeps are not included in this release.


Registry setup: follow the complete [image pull secret walkthrough](docs/IMAGE-PULL-SECRET.md) for token creation, username formats, both namespace secrets, UI configuration, and troubleshooting. The UI stores a secret name; it does not create credentials.


## v2.7.0: replace a defective worker

Use **Workers & scaling → Details → Preview worker replacement**, review the exact instance and blockers, then confirm. Follow the complete [worker replacement guide](docs/REPLACEMENT.md) for prerequisites, IAM, resume/close behavior and verification. This release requires an application-idle target and released claims; it does not automatically migrate live application workloads. A failed or incomplete RDMA check never becomes replacement success.

The three sidebar guide-download links have been removed. Use the separately supplied guide bundle for current documentation. Diagnostic, support and workload downloads remain available.


Performance v2.8.0: see [all eight measurement workflows](docs/PERFORMANCE.md). Rebuild the tools image for MPI and NUMA profiles. The guide distinguishes measured data, observation intervals and unavailable stages.

For a failed diagnostic, start with [Host diagnostic troubleshooting](docs/DIAGNOSTIC-TROUBLESHOOTING.md).
