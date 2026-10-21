# Web UI operating guide — OKE HPC v2.7.0

Start after infrastructure provisioning and Bastion access described in [README](README.md). This guide uses the browser for operations; private registry secrets must first be provisioned through Kubernetes by an authorized administrator.

## Contents

1. Connect and navigate
2. Prepare registry credentials
3. Configure the workload profile
4. Check host readiness
5. Enable Dranet
6. Measure RDMA and interpret freshness
7. Run an application
8. Inspect workers and reservations
9. Scale workers, including zero
10. Review operations and diagnostics
11. Maintain and reset

## First visit: the shortest complete route

Start here if you have just logged in. Allow each queued job to finish before continuing.

1. **Overview → Continue workflow** opens the appropriate setup step. A new environment normally says setup pending, not validated.
2. **Setup & validation → step 1:** save the tools image and profile. Prepare private registry credentials using the operator guide before step 2. Select **Review readiness** to see the exact missing namespace/secret if credentials are unavailable.
3. **Step 2 → Check selected hosts:** wait for host evidence. Each intended worker must pass.
4. **Step 3 → Install or verify Dranet:** wait for driver and device readiness.
5. **Step 4 → Run RDMA validation:** use two distinct free workers; wait for bandwidth, latency and cleanup.
6. **Download workload YAML:** review and deploy the example from the operator as described below.

If a button is unavailable, read the preceding step and the message alongside it. Installing Dranet does not repair a missing tools-image pull secret or a crashed host agent. **Maintenance is not part of normal first-time setup.**

## 1. Connect and navigate

Keep the Bastion UI tunnel open, browse to `http://127.0.0.1:18000`, enter the workspace token and select **Open workspace**. Check the environment name at the top before any action. **LIVE** means actions affect the configured deployment; demo data does not establish hardware readiness.

| Sidebar page | Use it for | First action |
|---|---|---|
| **Overview** | Current health, available slots, measurements, attention queue and next action | Read the next-action card. |
| **Setup & validation** | Four ordered setup steps | Start with workload configuration. |
| **Workers & scaling** | Onboarding, worker details, claims, warnings and Resource Manager scaling | Review the inventory before planning capacity. |
| **Operations** | Current/latest jobs, earlier attempts, evidence and exports | Open a job's evidence and output. |
| **Maintenance** | Preview and reset RDMA setup | Review the preview, never use reset as the first diagnostic step. |

Maintenance is always last. **Refresh status** refreshes observations. **Lock workspace** ends browser access; it does not cancel a queued job. The persistent operation banner follows you between pages. A busy button acknowledges the request; an accepted job has its own progress. A timeout in the browser is not proof that the operation failed—inspect Operations before retrying.

Progress counts completed phases, not a time estimate. Do not submit the same mutation from another tab while one is pending.

## 2. Build the tools image and configure registry access on the operator VM

An image pull secret is a Kubernetes object containing registry credentials. `ocir-pull` is the name you choose for that object. It is not an OCI image OCID, a token supplied by this tool, or the Control Center login token. This walkthrough builds and publishes the tools image on the operator VM, then creates the secrets that let workers pull it. Complete the steps in order. You can prepare everything with zero workers; testing an actual worker pull needs workers later.

### 1. Create or select the OCIR repository

Open OCI Console → Developer Services → Containers & Artifacts → Container Registry. Select your intended region and compartment. Create a private repository named `rdma-tools`, or select your existing approved repository. Record its registry hostname, tenancy namespace and repository name; there is no image digest yet if this is a new repository. For an illustrative reference `fra.ocir.io/exampletenancy/rdma-tools@sha256:...`:

| Prompt | What to enter | Where to get it |
|---|---|---|
| Registry host | `fra.ocir.io` in this example | Everything before the first slash in your actual tools-image reference; no `https://`, repository path or digest |
| Registry username | `<tenancy-namespace>/<username>`; for federated identities, `<tenancy-namespace>/<domain-name>/<username>` | Object Storage namespace from Tenancy Information, and the actual identity/domain shown in your profile |
| Registry auth token/password | The registry user's OCI auth token | Generate it as described below; do not use your Console password or API signing key |

For example, a federated username might be `exampletenancy/oracleidentitycloudservice/alice@example.com`. This is only an example: do not assume that your domain is `oracleidentitycloudservice` or `Default`. Use the identity format configured for your account. The tenancy namespace is not the tenancy OCID or Kubernetes namespace. The build user needs permission to push images to this repository. The user placed in the Kubernetes pull secret needs permission to read it. For a simple lab these can be the same user; for production prefer a separate read-only pull identity. Ask your tenancy administrator for these permissions before proceeding. The operator VM's instance-principal permissions do not automatically grant that user repository access.

### 2. Obtain an auth token

If an approved registry credential already exists, obtain it from its owner or approved credential store. OCI does not redisplay an old token's value.

To create one: sign in as the registry user, open the profile menu → **User settings** → **Tokens and keys** → **Auth tokens** → **Generate token**. Give it a description such as `OKE RDMA image pulls`. Copy the displayed value immediately into your approved credential store; it is displayed only once. If the token controls are unavailable, ask your identity administrator to provision a suitable registry user/token. Never paste the token into the Control Center, chat, a support report or a command-line argument.

### 3. Prepare the operator VM

Run all following commands in the operator VM SSH session as `opc`, not on a worker or your laptop. This path uses rootful Podman on the supplied Oracle Linux 9 operator, avoiding rootless UID-map setup. Building an image does not require an RDMA NIC on the operator. Allow disk space for the image and build cache, and outbound access to Oracle Linux repositories, Docker Hub, Ubuntu package repositories and OCIR. A service gateway alone does not supply public-internet access.

```bash
uname -m
df -h /var /home/opc
sudo dnf install -y podman
sudo podman --version
mkdir -p "$HOME/rdma-tools-build"
cd "$HOME/rdma-tools-build"
```

Expected architecture: `x86_64` for this project's operator and workers. Stop and review the architecture if yours differs; this guide does not configure cross-architecture emulation. Installing Podman does not install or change the worker CRI-O runtime.

### 4. Write the release's tools-image Dockerfile

This is the complete `workloads/rdma-tools/Dockerfile` from v2.6.0. You can paste it on the operator without copying the full source archive. The ORM archive is not the container build context.

```bash
cat > Dockerfile <<'DOCKERFILE'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    perftest rdma-core ibverbs-utils libibverbs1 ibverbs-providers \
    iproute2 iputils-ping ethtool pciutils procps util-linux jq curl \
    python3 ca-certificates coreutils \
    && rm -rf /var/lib/apt/lists/*
# The same immutable image is used in two reviewed modes:
# 1. non-privileged Dranet IPvlan RDMA validation pods; and
# 2. ephemeral fixed-profile node diagnostics with a read-only /host mount.
# Host RDMA drivers remain owned by the OKE worker image and NVIDIA/OCI agents.
CMD ["sleep", "infinity"]
DOCKERFILE
```

The Ubuntu base tag and package repositories can change between builds. The pushed digest recorded below pins the exact output you tested; rebuilding later can produce a different digest. Keep the original digest when you want to reuse that build.

### 5. Log in to OCIR from the operator

Keep this SSH session open through step 8 so the non-secret shell variables remain available. Enter the exact hostname from step 1, the tenancy namespace, and repository name (`rdma-tools` if you created that name).

```bash
read -r -p 'Registry host (for example fra.ocir.io): ' RDMA_REGISTRY
read -r -p 'Tenancy Object Storage namespace: ' RDMA_TENANCY_NS
read -r -p 'Repository name (for example rdma-tools): ' RDMA_REPOSITORY
read -r -p 'Registry push username (including tenancy/domain): ' RDMA_PUSH_USER
RDMA_IMAGE_REPO="${RDMA_REGISTRY}/${RDMA_TENANCY_NS}/${RDMA_REPOSITORY}"
RDMA_BUILD_TAG="v2.6.0-$(date -u +%Y%m%dT%H%M%SZ)"
RDMA_TAGGED_IMAGE="${RDMA_IMAGE_REPO}:${RDMA_BUILD_TAG}"
printf 'Image to build: %s\n' "$RDMA_TAGGED_IMAGE"
sudo install -d -m 700 /var/lib/oke-rdma-image-build
sudo podman login --authfile /var/lib/oke-rdma-image-build/auth.json --username "$RDMA_PUSH_USER" "$RDMA_REGISTRY"
```

At the password prompt, enter the push user's **OCI auth token** from step 2. Do not put it in the command. Expect `Login Succeeded`; stop if login fails. The temporary auth file contains credentials and must not be included in support bundles.

### 6. Build, then push and capture the registry digest

Run each command only after the previous command succeeds:

```bash
sudo podman build --platform linux/amd64 -t "$RDMA_TAGGED_IMAGE" .
sudo podman push --authfile /var/lib/oke-rdma-image-build/auth.json --digestfile /var/lib/oke-rdma-image-build/pushed.digest "$RDMA_TAGGED_IMAGE" "docker://$RDMA_TAGGED_IMAGE"
```

If push fails, stop; do not reuse a digest file from an earlier attempt. Once push succeeds, capture and validate its digest:

```bash
RDMA_IMAGE_DIGEST="$(sudo cat /var/lib/oke-rdma-image-build/pushed.digest)"
if [[ "$RDMA_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  RDMA_PINNED_IMAGE="${RDMA_IMAGE_REPO}@${RDMA_IMAGE_DIGEST}"
  printf '%s\n' "$RDMA_PINNED_IMAGE" | tee rdma-tools-image-reference.txt
else
  echo 'Invalid or missing pushed digest; stop and inspect the push result.' >&2
fi
sudo rm -f /var/lib/oke-rdma-image-build/auth.json
```

Expected result: `registry/namespace/repository@sha256:` followed by 64 hexadecimal characters. Keep `rdma-tools-image-reference.txt`; this is the value to save in the tool. The digest is the pushed manifest digest, not a local image ID. If you abandon the process earlier, remove the temporary auth file with the same cleanup command. A future push requires a fresh login.

### 7. Check whether the pull secret already exists

SSH to the operator VM and use its configured `kubectl` as `opc`. Confirm the cluster context before writing:

```bash
kubectl config current-context
kubectl get nodes
kubectl -n rdma-workloads get secret ocir-pull
kubectl -n oke-rdma-diagnostics get secret ocir-pull
```

`NotFound` means it must be created in that namespace. `Forbidden` means your Kubernetes access needs fixing; creating a new token will not solve it. With zero workers, `get nodes` can legitimately return no resources, and you can still create secrets. An existing secret's presence does not prove that its token remains valid. Do not dump its YAML or decode its data just to check it exists.

### 8. Create or rotate the pull secret in both namespaces

The UI does not create registry credentials. Even UI users perform this one-time step on the operator VM (or ask their Kubernetes administrator). The script below creates or updates `ocir-pull` in both namespaces; on rotation it replaces the stored registry entry in that named secret. Use it only for this tool's dedicated secret.

First ensure the namespaces exist. These commands do not grant privileged Pod Security access:

```bash
kubectl create namespace rdma-workloads --dry-run=client -o yaml | kubectl apply --server-side -f -
kubectl create namespace oke-rdma-diagnostics --dry-run=client -o yaml | kubectl apply --server-side -f -
```

Use the same registry hostname used for the push. Enter the pull identity and its token (the same credentials in a simple lab, or the separate read-only identity in production). Paste the complete block below into your interactive SSH terminal. It prompts for the three values; the token is hidden. It reads `/dev/tty` separately for input and output, avoiding the earlier “File or stream is not seekable” failure. Docker is not required. Credentials are supplied to kubectl through standard input, not process arguments.

Before running the Python block, prepare these three values:

| Prompt | What to enter | Example / where to find it |
| --- | --- | --- |
| Registry host (no https, e.g fra.ocir.io) | Only the hostname before the first slash in your tools-image reference. No scheme, repository path or digest. | For `fra.ocir.io/fr9qm01oq44x/oke-rdma/rdma-tools@sha256:…`, enter `fra.ocir.io`. |
| Registry username (namespace/[domain/]username) | Your tenancy Object Storage namespace, followed by the registry user's identity. Include its identity domain where applicable; brackets are explanatory, not literal. | `fr9qm01oq44x/alice` or `fr9qm01oq44x/Default/alice@example.com`. Use your actual namespace and profile identity, not these example users. The namespace is not the tenancy display name or OCID. |
| Registry auth token/password | An OCI **auth token belonging to that user**. It is used as the registry password. | In OCI Console, open the user's details → Auth tokens → Generate token (or obtain one through your identity administrator). Copy it when generated; input is hidden. Do not use the Console password, API private key or Kubernetes token. |

The user must have permission to read the image repository. A successful secret apply stores credentials; **it does not validate OCIR login**. A subsequent diagnostic pull verifies that the worker can use them. Operator `podman login` alone does not configure the Kubernetes pull secret.

```bash
python3 - <<'PY'
import base64, getpass, json, subprocess
def prompt(label):
    with open('/dev/tty', 'w') as output:
        output.write(label)
        output.flush()
    with open('/dev/tty', 'r') as terminal:
        return terminal.readline().strip()
registry = prompt('Registry host (no https, e.g fra.ocir.io): ')
username = prompt('Registry username (namespace/[domain/]username): ')
password = getpass.getpass('Registry auth token/password (OCI auth token, not Console password): ')
if not registry or not username or not password:
    raise SystemExit('All credential fields are required')
if '://' in registry or '/' in registry or any(c.isspace() for c in registry):
    raise SystemExit('Registry host must be a hostname only, for example fra.ocir.io')
if '/' not in username or username.startswith('/') or username.endswith('/'):
    raise SystemExit('Use namespace/[domain/]username with your actual OCIR identity')
auth = base64.b64encode((username + ':' + password).encode()).decode()
config = json.dumps({'auths': {registry: {'auth': auth}}}).encode()
encoded = base64.b64encode(config).decode()
for namespace in ('rdma-workloads', 'oke-rdma-diagnostics'):
    secret = {'apiVersion': 'v1', 'kind': 'Secret',
              'metadata': {'name': 'ocir-pull', 'namespace': namespace},
              'type': 'kubernetes.io/dockerconfigjson',
              'data': {'.dockerconfigjson': encoded}}
    subprocess.run(['kubectl', 'apply', '--server-side',
                    '--field-manager=rdma-registry-setup', '-f', '-'],
                   input=json.dumps(secret), text=True, check=True)
PY
```

Expected output is a successful `secret/ocir-pull` apply message for each namespace. If server-side apply reports field ownership conflicts, reconcile with the existing secret owner; do not blindly force conflicts. A failure on the second namespace can leave the first updated: fix the error and rerun deliberately.

### 9. Save the image reference and secret name, then verify

1. Open **Setup & validation** → workload configuration.
2. Copy the complete value from `cat "$HOME/rdma-tools-build/rdma-tools-image-reference.txt"` into the tools-image field.
3. In **Existing image pull secret**, enter exactly `ocir-pull`. Enter only the name, never the token. Save the configuration.
4. Save. Terminal-only users can use the authenticated API helper and configuration request in the next guide sections, using this same image reference and `rdma_image_pull_secret` value `ocir-pull`.
5. Confirm both objects without exposing credentials:

```bash
kubectl -n rdma-workloads get secret ocir-pull -o custom-columns=NAME:.metadata.name,TYPE:.type
kubectl -n oke-rdma-diagnostics get secret ocir-pull -o custom-columns=NAME:.metadata.name,TYPE:.type
```

Both should report type `kubernetes.io/dockerconfigjson`. After workers exist, run **Check hosts** to exercise the diagnostics image, then **Quick pair check** when Dranet is ready to exercise the workload image. Inspect events on a failed attempt:

```bash
kubectl -n oke-rdma-diagnostics get events --sort-by=.metadata.creationTimestamp
kubectl -n rdma-workloads get events --sort-by=.metadata.creationTimestamp
```

A successful container start alone may use a cached image; a `Pulled` event indicating an actual successful download provides stronger evidence of working registry access. Secret creation while scaled to zero cannot validate a worker pull.

| Symptom | What to check next |
|---|---|
| `FailedToRetrieveImagePullSecret` | Exact secret name and the failing pod's namespace; both copies are required |
| `unauthorized` / `denied` | Exact hostname, username/domain format, token validity, repository-read permission |
| `manifest unknown` / `not found` | Repository path and published tag/digest; credentials do not create images |
| DNS error / connection timeout | Worker DNS and network path to the registry |
| `ImagePullBackOff` | Read the detailed preceding pull event; this is a retry state, not the root cause |
| Secret exists but warning continues | Refresh events and inspect the newest pod attempt; old events remain in history |

For a genuinely public image that needs no credentials, leave the UI secret field empty. When rotating credentials, update both namespaces, verify new pulls, and only then revoke the old token. Replacing the operator VM normally preserves secrets stored in the existing Kubernetes cluster; check them before generating new credentials.

Sources: [Oracle: generate an auth token](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsgenerateauthtokens.htm), [Oracle: registry login identity format](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionslogintoocir.htm), [Oracle: Kubernetes registry pulls](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrypullingimagesfromocir.htm).

Build references: [Oracle Linux Podman installation](https://docs.oracle.com/en/operating-systems/oracle-linux/podman/install.html), [Podman push and digest file](https://docs.podman.io/en/stable/markdown/podman-push.1.html).

## 3. Setup & validation → Set the workload configuration

Expand step **1** and populate:

| Field | Reference value / instruction |
|---|---|
| RDMA interface | `ens800f0np0` for the reference shape; verify against actual host inventory. |
| Child IPv4 CIDR | `10.208.0.0/12`; must not overlap VCN, pod, service or parent fabric ranges. |
| Tools image · immutable digest | Your pushed `repository@sha256:...` image. |
| Existing image pull secret | Example `ocir-pull`, already provisioned in both namespaces. |
| RDMA traffic class | `41` reference default; MTU is fixed at `4220`. |

Select **Save configuration**. Resolve field errors before continuing. **Review readiness** provides a prerequisite overview but is not a hardware measurement. After Dranet installation, PF and child-range settings are locked because changing them can affect allocated devices. Plan such changes through maintenance rather than editing backend files to bypass the lock.

## 4. Confirm the hosts are ready

Expand step **2**, then select **Check selected hosts**. If asked, type the exact displayed environment name and select **Run operation**. The scope is nodes labelled `oci-rdma=true` and `oke-rdma-mode=dranet`.

The operation uses short-lived privileged diagnostic pods with a read-only host inventory script. Current host evidence can be reused for 24 hours and is invalidated by a reboot.

Expected evidence includes an unpartitioned mlx5 PF, link/carrier and MTU 4220, OCA parent IPv4 address in the fabric range, shared RDMA namespace mode, NRI socket and no conflicting device controller.

If the check fails:

1. Open **Evidence and output**.
2. Download the per-worker **text** and **JSON** reports.
3. If `facts` is empty, troubleshoot diagnostic execution first. v2.5.0 reports inventory unavailable without inventing multiple host failures. Earlier releases could show cascading warnings.
4. For image warnings, repair the secret/image access and retry host checks.
5. For a successfully collected inventory with a real hardware/runtime finding, investigate that specific prerequisite. **Query OCI agent health** provides separate agent evidence.

Do not proceed simply because Kubernetes says Ready. Do not reinstall the cluster solely because host collection failed.

## 5. Enable Dranet

After host checks pass, expand step **3**, select **Install or verify Dranet**, review the confirmation and submit. This installs/verifies the pinned chart, OKE IPvlan profile and `dra.net` DeviceClass.

Use **Download full Helm values** to review the actual profile. Expected outcome: driver readiness plus published devices on eligible workers. A running driver pod without usable devices is not sufficient.

For restricted Kubernetes access, an administrator must apply the generated RBAC and install privileged driver infrastructure. Use the administrator procedure in the operator guide; then return here to verify. Do not broaden RBAC blindly after an authorization failure.

## 6. Measure cross-node RDMA

Expand step **4**. Select two different, free, ready workers, or leave automatic selection. Select **Run RDMA validation**, review and confirm.

The test reserves one physical NIC per endpoint, runs bandwidth followed by latency, and removes its test pods and claims afterward. It uses 64 KiB bandwidth writes and 8-byte latency writes with 5,000 iterations each. Latency is reported in microseconds using perftest's half-round-trip estimate.

Expected outcome: a successful operation, positive measured bandwidth and latency, and successful cleanup. There is no universal bandwidth/latency threshold for every deployment. Compare equivalent placement, image and configuration.

If there are fewer than two free workers, stop here. One worker supports a single RDMA workload but cannot perform cross-node validation. Do not delete another application's claims to free slots without its owner's approval.

If a test is interrupted, inspect its result before selecting **Clean up interrupted test**. This action targets tool-owned validation resources, not arbitrary application workloads. Retry only after cleanup and readiness are resolved.

### Measurement freshness

Overview displays bandwidth and latency from the same recorded run. Review the recorded time, participating nodes and freshness explanation. Evidence may require review after 24 hours, a worker addition/removal/replacement/reboot, changed configuration, an incomplete run or a newer attempt. Old runs without fleet fingerprints cannot establish current validation.

An old bandwidth-only result cannot acquire latency retroactively; run a new combined test. An old failed attempt stays in Operations history. A passing pair validates that pair—not every possible path in the fleet. Scaling performs readiness checks, not an automatic performance benchmark.

## 7. Run your own application

1. Select **Download workload YAML** at the bottom of Setup & validation.
2. Review the complete ResourceClaim and Pod. Verify namespace, image, pull-secret reference, CPU/memory resources and application command.
3. Transfer the file to the operator VM and apply it:

```bash
kubectl apply -f rdma-workload.yaml
kubectl -n rdma-workloads get pods,resourceclaims
```

The supplied example is named `rdma-example` and normally keeps a tools container running; it is not a complete MPI application. On the **OPERATOR VM**, verify it after Apply:

```bash
kubectl -n rdma-workloads wait --for=condition=Ready pod/rdma-example --timeout=180s
kubectl -n rdma-workloads describe resourceclaim rdma-example
kubectl -n rdma-workloads exec rdma-example -- ip -br address
kubectl -n rdma-workloads exec rdma-example -- rdma link show
```

Expected: the claim has a device allocation; the pod has its normal network interface and an additional IPvlan child address from your configured range. This checks access, not application performance. If the pod is Pending, inspect `kubectl -n rdma-workloads describe pod rdma-example` and its claim. A quiet `kubectl logs` is normal for a sleeping example container.

For cleanup after testing:

```bash
kubectl -n rdma-workloads delete pod rdma-example --wait=true
kubectl -n rdma-workloads delete resourceclaim rdma-example --wait=true
```

Return to Workers & scaling and refresh; the slot should become free. Use actual names if you renamed the example.

Use the names in the downloaded file when inspecting logs. Each independently scheduled RDMA pod needs its own claim. The downloaded example is a starting manifest, not an automatic multi-node MPI launcher.

Stop the workload's controller first if it would recreate pods. Delete completed pods before releasing their claims. A retained claim may reserve its NIC even with no running pod.

## 8. Workers & scaling → inventory

Use the worker filter and **Inspect** control to investigate a node. Review onboarding in order: OCI provisioning, Kubernetes registration/readiness, Dranet device publication, and host evidence. These stages are distinct from measured performance.

Review **Claim reservations** for namespace, allocation and consumers. An “allocated” selector label is not evidence that RDMA configuration is healthy. Load **Recent warning events → Refresh events** when diagnosing a problem.

From worker details choose a fixed **Diagnostic profile**, inspect its description and select **Run fixed diagnostic**. Review the resulting job and downloadable report. Profiles are curated checks, not arbitrary browser shell execution.

### Recover a recognized OCA initialization failure

Use this only after a diagnostic shows the specific host-configuration failure, not as a general-purpose restart button.

1. **Workers & scaling → affected worker → Details → Recover RDMA initialization → Assess recovery**.
2. Confirm the environment to collect evidence. Assessment itself does not restart OCA.
3. Wait for its result. It requires current metadata HTTP 200, an active agent, current-boot configuration crash/unresponsive evidence and failed host readiness. A healthy worker is not eligible.
4. Review the affected worker, findings, report and proposed action. Pause application controllers. All existing cluster ResourceClaims/ResourceClaimTemplates block this first version, even if they belong to another worker.
5. Select **Review recovery**, type `RECOVER <environment name>`, and confirm. The assessment expires after five minutes and is rechecked before restart.
6. The operation cordons the worker and verifies temporary admission protection. New pods/bindings targeting the worker are denied, and new ResourceClaims/ResourceClaimTemplates are temporarily denied cluster-wide. Existing system DaemonSets may remain; their replacement pods wait while the guard is active. This requires a coordinated administrative maintenance window.
7. OCA is restarted **once**, interrupting its other plugins briefly. Host readiness is checked for up to five minutes after restart. It does not run a bandwidth test or reboot the worker.
8. On success, temporary protection is removed and the original scheduling state is restored. A worker originally cordoned stays cordoned.
9. On failure/interruption, read the recorded error. **Finish recorded recovery** removes temporary resources without another restart and leaves the worker cordoned. An administrator checks readiness before deliberately uncordoning it.

Recovery requires live cluster-administrator mode. Restricted mode is not silently escalated. Administrators must not remove/bypass protection, change the node or place static workloads on the host during recovery. The admission guard covers Kubernetes API placement, not privileged direct host access.

**Expected result:** a completed recovery plus current host readiness. Follow with normal driver verification and a new combined validation when appropriate. A failed or uncertain restart is never automatically repeated.

## 9. Workers & scaling → Scale workers

### Connect the owning stack

Enter the existing **Resource Manager stack OCID**, not a compartment, cluster, node pool or Compute Cluster OCID. Enter the exact environment name, then select **Verify and save connection**. The stack must own this cluster and pool. A saved connection does not prove all write permissions exist.

### Prepare the baseline

Confirm scaling IAM, cycling disabled, nonzero eviction grace and force termination disabled. Reconcile intended IAM, image, operator payload or other infrastructure changes separately at the current worker count. A size-only scaling operation must not carry unrelated changes.

### Generate, review, apply

1. **Refresh capacity** and choose a whole target from **0 to 64**.
2. Select **Check scaling prerequisites**. This is read-only. It does not prove future Terraform-plan contents or write permissions. The review expires after five minutes or a target change.
3. Select **Generate scale plan** and confirm the environment. This updates the stack's desired `node_count` and creates an ORM PLAN job; it does not resize workers yet.
4. Review the current/target counts and allowed change summary. Replacements and unrelated changes are blocked.
5. Select **Review and apply plan**, type `SCALE <environment name>`, and submit. The backend rechecks and applies the reviewed plan. Approval expires after 30 minutes.
6. Track OCI Apply, worker readiness, device publication and new/rebooted-worker host checks. Do not launch a second apply because a page appears slow.

**Discard target** restores the previous stack target before apply. It is not infrastructure rollback. If inputs changed externally, reconcile with the stack administrator rather than overwriting them. Once apply is submitted it cannot be discarded through this action.

**Resume recorded operation** continues an existing recorded operation after interruption; inspect OCI job state if submission is uncertain. **Close failed apply** acknowledges a terminal failed/canceled apply after review; it does not roll infrastructure back or restore stack variables.

### Scale down, including zero

Pause application controllers, stop workloads, preserve required node-local data, and release application RDMA claims/templates. Keep controllers paused through the reduction. The tool requires an idle application pool and checks worker readiness; it does not promise which particular workers OKE removes or bypass disruption budgets.

Target **0** removes worker capacity while retaining cluster/operator infrastructure and their continuing costs. If no other worker pool exists, in-cluster services and workloads have nowhere to run. Scale back up through the same workflow. Existing Dranet configuration reconciles on new workers; an initially empty installation needs its initial setup after scale-up.

### Status glossary

| Status | Meaning | Does not prove |
|---|---|---|
| Kubernetes Ready | Kubelet reports readiness | Fabric addressing or authentication |
| Host ready | Required host facts passed for the observed boot | Cross-node performance |
| Dranet ready | Driver/profile and published devices pass checks | Every network path works |
| Device not published | No device observed yet | A claim has reserved it |
| Allocated/reserved | A claim holds the device | Its consumer is healthy or running |
| Diagnostic collection completed | The fixed collection command returned evidence | Authentication passed |
| Health needs attention | Evidence contains a relevant blocking signature | The exact repair without further checks |
| Validation passed | The recorded pair completed measured tests | All workers or future configurations |
| Historical/stale | Evidence needs review for current use | The old test never succeeded |

## 10. Operations and support evidence

Open **Operations**, select a filter and inspect the latest attempt. Enable **Include earlier attempts** for history. Record job IDs, timestamps, failed stage and sanitized output when requesting help.

Use **Download support bundle** from Overview or Operations for bounded configuration/inventory/operation summaries. It excludes secret contents, tokens, raw logs, job payloads and Terraform state/plans. It still includes infrastructure identifiers; review before sharing. Individual diagnostic reports are downloaded separately.

## 11. Maintenance — last in navigation

Use reset only when intentionally returning RDMA application setup to its provisioned baseline:

1. Stop application controllers and workloads; release their RDMA claims/templates.
2. Open **Maintenance → Preview RDMA reset**.
3. Read the removal inventory and blockers. Changed inventory requires a new preview.
4. Type exactly `RESET <environment name>` and confirm only the reviewed operation.
5. Wait for completion, then return to Setup & validation and repeat setup/measurement.

Reset removes the supported driver and owned RDMA setup/test resources and clears relevant setup evidence/runtime overrides. It retains Terraform infrastructure, workers, host drivers, OCA configuration, namespaces, pull secrets, history and reports. It is not a cluster destroy or a worker reboot. Application claims block removal; reset does not delete applications to unblock itself.

For full infrastructure removal, export evidence, stop applications and review a Resource Manager Destroy separately. Deleting only the stack record is not infrastructure destruction.


## Diagnosing a worker that has an RDMA address but no carrier

After a scale-out, open the worker's diagnostics and run **RDMA link health**, then **OCI RDMA authentication**. In the setup host-readiness section, expand the evidence and download the JSON or text report. A completed diagnostic means collection finished; it does not mean authentication succeeded.

The UI evaluates only the configured PF. A current ethtool `Link detected: no (No cable)` with absent carrier produces **RDMA physical link unavailable**. Check the instance's compute-cluster membership against healthy workers, then investigate NIC/fabric connectivity. This evidence suggests a physical connection problem; it cannot prove a cable was physically unplugged. Do not switch to the management interface or manually assign fabric addresses.

MTU mismatch is a separate finding. Changing MTU does not restore missing carrier. Authentication logs may say the selected interface is not ready while the module reports COMPLETED; review the timestamped evidence and rerun diagnostics after the link recovers. A generic WPA process is not authentication proof.

Missing ethtool or a failed query is reported as unavailable evidence, never as a healthy link or a confirmed cable fault. Agent-restart recovery is blocked for a current selected-PF no-cable finding, including a recheck immediately before restart. Older reports remain historical evidence; collect a fresh report after repair.


## Scale-in and single-worker diagnosis (v2.5.2)

In Setup & validation, expand step 2 and choose **Diagnose / recover this worker** on the affected host. The worker dialog opens with the full RDMA support bundle selected. Run the diagnostic, review findings, and download the report. **Assess recovery** checks eligibility without restarting anything. Only an eligible assessment offers separately confirmed recovery; physical no-cable evidence blocks an agent restart. These are the same actions available in Workers & scaling.

Scale-in no longer requires healthy RDMA or a ready Dranet device on workers being removed. Kubernetes readiness, pool membership, safe eviction, workload and claim checks, and the reviewed size-only Terraform plan still apply. Zero removes every worker in this pool. Partial scale-in lets OKE choose removals and is not a way to target one faulty worker. Existing-worker RDMA health remains required for scale-out, with the worker and actual issue shown.


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

### Run and interpret tests in the UI

1. Open **Setup & validation**. Finish host checks, review each selected PF, and enable Dranet. Resolve failed findings before benchmarking.
2. In **Measure cross-node RDMA**, select two free workers and choose **Quick**, **Message-size sweep**, or **Latency under concurrent traffic** in the performance-profile selector.
3. Review the confirmation, including the selected profile and traffic impact, and start validation. Follow the job progress rather than repeatedly clicking Run.
4. Open the completed result. Review bandwidth, latency, extended samples where selected, and cleanup status. A failed extended profile makes the whole attempt fail even if the initial quick test succeeded.
5. Review **Pair performance and baseline comparison**. Read the direction, profile, timestamp and state before comparing numbers. Swap the two selected workers and run again only if you need the reverse direction.
6. For capacity operations, expand the observed timeline. Kubernetes Ready does not prove RDMA readiness; check device and host findings separately.

Acceptance check: a successful quick test has positive measured bandwidth and latency, successful cleanup, and a current directional row. A second comparable run shows baseline changes. A reboot makes older evidence historical until you rerun. For sweep and concurrent-traffic profiles, inspect the extended result as well as the quick metrics.


## v2.7.0: replace a defective worker

Use **Workers & scaling → Details → Preview worker replacement**, review the exact instance and blockers, then confirm. Follow the complete [worker replacement guide](docs/REPLACEMENT.md) for prerequisites, IAM, resume/close behavior and verification. This release requires an application-idle target and released claims; it does not automatically migrate live application workloads. A failed or incomplete RDMA check never becomes replacement success.

The three sidebar guide-download links have been removed. Use the separately supplied guide bundle for current documentation. Diagnostic, support and workload downloads remain available.


Scale-up capacity check (v2.7.2): review [the required OCI capacity-report step](docs/CAPACITY.md#required-capacity-report-step-before-scale-up-v272) before planning. The report is refreshed before planning and apply; unavailable reports block scale-up. Install the new tenancy-level report permission before using this update to scale out.


v2.7.3 adds read-only assessment with separate restart blockers and a single-worker OCA restart preview. See [Recovery and agent restart](docs/RECOVERY.md) for the UI workflow, CoreDNS handling and retained protection.


Performance v2.8.0: see [all eight measurement workflows](docs/PERFORMANCE.md). Rebuild the tools image for MPI and NUMA profiles. The guide distinguishes measured data, observation intervals and unavailable stages.

## When host diagnosis fails

Read [Host diagnostic troubleshooting](docs/DIAGNOSTIC-TROUBLESHOOTING.md) for visible error meanings, registry credential checks, failed scheduling, deadlines and report collection. Failed collection does not establish RDMA health.
