# Operator VM guide — OKE HPC v2.7.0

Operate from SSH without opening the web UI. Complete provisioning and private SSH access using [README](README.md) first.

The primary terminal route calls the installed Control Center API on localhost. This preserves the same host checks, exact-plan scaling guards, job history and cleanup as the browser. Kubernetes inspection and secret repair use `kubectl` directly. Section 8 supplies a separate direct Helm administrator procedure. Do not run the Helm route and a queued driver operation simultaneously.

## Contents

1. Verify operator access and services
2. Prepare registry credentials
3. Define the terminal API helper
4. Save configuration and check hosts
5. Install Dranet and validate RDMA
6. Export and run a workload
7. Inspect failed diagnostics and reservations
8. Direct Helm administrator route
9. Scale from the terminal
10. Cleanup, reset and updates

## Choose your terminal route

- **Guarded terminal workflow (sections 1–7, 9–10):** no browser required; the API helper uses the same installed control service, persistent jobs and safety gates as the UI. Start here for routine operations.
- **Direct administrator workflow (section 8):** Kubernetes and Helm directly, requiring the matching full project and appropriate cluster credentials. Use the preceding sections for credential preparation and evidence; explicit API commands are labelled as such.
- **Worker-level repair:** only commands explicitly marked WORKER VM belong on a worker. Routine cluster commands belong on the OPERATOR VM.

Each API mutation returns a job record. Check that job's terminal state AND result before running the next command. Never paste all mutation examples as a single script.

## 1. Verify operator access and services

Run on the **operator VM**, not on a worker or in Cloud Shell:

```bash
source /etc/profile.d/oke-rdma-control.sh
oke-rdma-doctor
kubectl config current-context
kubectl get nodes -l oci-rdma=true -o wide
```

Expected: the API and worker services are active, health is ready, and the intended worker pool is visible. An intentionally zero-sized pool has no workers. Do not substitute credentials from another cluster if authorization fails; verify access mode and the generated operator RBAC.

Inspect bootstrap/service failures:

```bash
sudo cat /var/lib/oke-rdma-control/bootstrap-status.json
sudo systemctl --no-pager --full status oke-rdma-control-api.service oke-rdma-control-worker.service
sudo journalctl -u oke-rdma-control-api.service -u oke-rdma-control-worker.service -n 150 --no-pager
```

Configuration/token: `/etc/oke-rdma-control`. Durable application state: `/var/lib/oke-rdma-control`. Application/venv: `/opt/oke-rdma-control`. Review logs before sharing; do not publish tokens, registry credentials or Terraform state.

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

This is the complete `workloads/rdma-tools/Dockerfile` from v2.8.0. You can paste it on the operator without copying the full source archive. The ORM archive is not the container build context.

```bash
cat > Dockerfile <<'DOCKERFILE'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    perftest rdma-core ibverbs-utils libibverbs1 ibverbs-providers \
    iproute2 iputils-ping ethtool pciutils procps util-linux jq curl \
    python3 python3-mpi4py openmpi-bin libucx0 ucx-utils openssh-server openssh-client numactl passwd ca-certificates coreutils \
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

## 3. Define a terminal API helper

Paste this Bash function into the operator shell. It reads the token internally using sudo, sends it only to localhost, and prints JSON responses. It does not print the token or put it in command-line arguments. It is a shell-session helper, not a new installed product CLI.

```bash
api() {
  sudo python3.11 - "$@" <<'PY'
import json, pathlib, sys, urllib.request, urllib.error
method, path = sys.argv[1:3]
if not path.startswith('/api/v1/'):
    raise SystemExit('Use a local /api/v1/ path')
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
token = pathlib.Path('/etc/oke-rdma-control/admin.token').read_text().strip()
request = urllib.request.Request('http://127.0.0.1:8000' + path,
    data=data, method=method,
    headers={'Authorization': 'Bearer ' + token,
             'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read().decode()
        try: print(json.dumps(json.loads(body), indent=2))
        except json.JSONDecodeError: print(body, end='')
except urllib.error.HTTPError as error:
    print(error.read().decode(), file=sys.stderr)
    raise SystemExit(1)
PY
}
api GET /api/v1/environment
api GET /api/v1/rdma/config
```

For confirmation strings, obtain the exact environment name:

```bash
sudo python3.11 - <<'PY'
import json
with open('/etc/oke-rdma-control/config.json') as f:
    print(json.load(f)['environment_name'])
PY
```

Examples below use `hpc-oke-rdma`. Replace it if your environment differs. An accepted job response means **queued**, not completed. Record its returned job ID; poll the exact job before proceeding:

```bash
api GET /api/v1/jobs
api GET /api/v1/jobs/REPLACE_WITH_RETURNED_JOB_ID
```

Inspect status, result and error. After a request timeout, check job history before resubmitting. A successful HTTP response is not by itself evidence that the queued operation passed.

## 4. Save configuration and check hosts

Replace the image placeholder with your real immutable digest; the example is deliberately not a valid runnable image:

```bash
api PUT /api/v1/rdma/config '{
  "rdma_pf_name": "ens800f0np0",
  "rdma_mtu": 4220,
  "rdma_child_cidr": "10.208.0.0/12",
  "rdma_tools_image": "REPLACE_WITH_REPOSITORY@sha256:REPLACE_WITH_64_HEX_DIGEST",
  "rdma_image_pull_secret": "ocir-pull",
  "rdma_tos": 41
}'
api GET /api/v1/preflight
api POST /api/v1/jobs '{"action":"check_hosts","confirmation":"hpc-oke-rdma"}'
```

For a public image set the secret name to an empty string. Ensure the child CIDR does not overlap your VCN/pod/service/fabric ranges. Poll the host-check job until complete. Require successfully collected host facts and resolved findings before installation. Two Kubernetes Ready nodes alone do not establish RDMA readiness.

The checks target `oci-rdma=true,oke-rdma-mode=dranet`. Evidence may be reused for 24 hours and is invalidated after reboot. An empty facts object is a collection failure; investigate execution before changing every host setting.

## 5. Install Dranet and validate RDMA

Submit installation only after the host-check result passes:

```bash
api POST /api/v1/jobs '{"action":"install_dranet","confirmation":"hpc-oke-rdma"}'
```

Poll that job, then inspect publication:

```bash
kubectl -n kube-system get daemonsets,pods -o wide
kubectl get deviceclass dra.net
kubectl get resourceslices
api GET /api/v1/rdma/discovery
```

Require driver readiness and devices for eligible workers. Restricted access mode needs administrator bootstrap; see section 8.

With two free workers, submit the combined validation:

```bash
api POST /api/v1/jobs '{"action":"rdma_validation","confirmation":"hpc-oke-rdma","server_node":"","client_node":""}'
```

Empty node values select automatically. To pin endpoints, replace them with two distinct Kubernetes node names observed in your pool. Poll the job. Expected: measured bandwidth and latency and successful cleanup. The test uses 64 KiB bandwidth and 8-byte latency writes, 5,000 iterations each. Latency is perftest's half-round-trip estimate in microseconds. No fixed throughput is assumed.

Do not count historical bandwidth-only evidence as a combined pass. Host readiness and scale-out do not automatically benchmark the network. After fleet/configuration changes, run a new test; one passing pair does not validate every pair.

## 6. Export and run a workload

Export complete manifests using the saved profile:

```bash
umask 077
api GET /api/v1/exports/values > dranet-values.yaml
api GET /api/v1/exports/workload > rdma-workload.yaml
```

Check both helper commands succeeded before using their files. Review the entire workload YAML, including image, namespace, secret, ResourceClaim selector, resources and command. The example uses a tools container; replace its command with your intended application and use an image containing that application.

```bash
kubectl apply --dry-run=server -f rdma-workload.yaml
kubectl apply -f rdma-workload.yaml
kubectl -n rdma-workloads get pods,resourceclaims
```

Use actual names from the exported YAML for `kubectl describe pod`, `kubectl logs` and claim inspection. A Pending pod can mean no free RDMA NIC, an unresolved claim, an image issue or scheduling constraints; inspect events before guessing.

Each independently scheduled RDMA pod needs a claim. Do not launch extra replicas all referencing a single claim unless your application design explicitly supports that consumption model. Stop application controllers before deleting their pods. Release completed claims only after the consumers have stopped.

## 7. Inspect failed diagnostics and reservations

List supported fixed profiles and run one for a real node:

```bash
api GET /api/v1/diagnostics/profiles
api POST /api/v1/diagnostics/runs '{"node_name":"REPLACE_WITH_NODE_NAME","profile":"dranet-readiness","confirmation":"hpc-oke-rdma"}'
```

Poll the returned job. Retrieve the exact report path from its result; do not assume a host-check parent job ID equals each child diagnostic's report ID:

```bash
api GET /api/v1/diagnostics/reports/REPLACE_WITH_REPORT_ID/text
api GET /api/v1/diagnostics/reports/REPLACE_WITH_REPORT_ID/json
```

### Capture evidence while a diagnostic is still present

Use a second SSH session while the check runs:

```bash
kubectl -n oke-rdma-diagnostics get pods -w
```

After noting the exact pod name, stop the watch with Ctrl-C and inspect it before automatic deletion:

```bash
kubectl -n oke-rdma-diagnostics describe pod REPLACE_WITH_POD_NAME
kubectl -n oke-rdma-diagnostics get pod REPLACE_WITH_POD_NAME -o json
kubectl -n oke-rdma-diagnostics logs REPLACE_WITH_POD_NAME -c diagnostic
kubectl -n oke-rdma-diagnostics get events --field-selector involvedObject.name=REPLACE_WITH_POD_NAME --sort-by=.metadata.creationTimestamp
```

Inspect `status.containerStatuses[].state.terminated` for reason, exit code and message, plus pod reason/message and image events. A terminated-container error from log retrieval does not explain why the container stopped. v2.5.0 preserves status and bounded events before deleting the pod. If evidence collection itself fails, the report identifies that limitation. Namespace events eventually expire.

| Evidence | What to investigate next |
|---|---|
| FailedToRetrieveImagePullSecret | Secret name, existence and namespace; section 2. |
| Unauthorized image pull | Registry hostname, token validity and repository permissions. |
| Image not found | Repository and exact pushed digest. |
| Registry connection timeout | Egress, DNS, routing and registry reachability. |
| Nonzero exit / shell or command error | Tools-image contents and the diagnostic's actual output. |
| OOMKilled / deadline exceeded | Termination state, resource use and timing; do not infer solely from elapsed duration. |
| Facts collected with missing PF/address/NRI | Specific image/bootstrap/OCA/runtime prerequisite. |

### Inspect device claims before cleanup

```bash
kubectl get resourceclaims -A
kubectl get resourceclaimtemplates -A
kubectl -n rdma-workloads get pods -o wide
kubectl -n rdma-workloads describe resourceclaim REPLACE_WITH_CLAIM_NAME
```

A retained claim can reserve a NIC without a running pod. Inspect allocations and `reservedFor` consumers. Do not bulk-delete claims to fix an “allocated” label.

## 8. Direct Helm administrator route

This section uses `helm` and `kubectl` directly. It requires cluster-administrator privileges for driver infrastructure, independently of operator workload RBAC. Use it for the restricted-access bootstrap or deliberate manual administration. The terminal API remains available for guarded host checks, measurements and audit history.

1. Extract the full **matching v2.5.0 project** on the operator and change into its root (the directory containing `provisioning/`, `control/` and `workloads/`). The compact ORM ZIP is not the source checkout.
2. In restricted mode, have a cluster administrator save and apply the full `operator_rbac_manifest` Terraform output. Do not apply only a fragment. Verify operator access separately.
3. Complete the registry and host-check procedures above. Do not install the driver over an unresolved PF ownership problem.
4. Fetch the pinned vendor inputs:

```bash
./provisioning/fetch-vendor.sh
```

5. Export complete values using section 6 after saving your profile. From the project root, keep that exported file at `./dranet-values.yaml`, then install:

```bash
control/vendor/helm upgrade --install dranet control/vendor/dranet-v1.5.0.tgz \
  --namespace kube-system --values ./dranet-values.yaml --wait --timeout 3m
kubectl apply --server-side --field-manager=oke-rdma-bootstrap \
  -f workloads/dranet/deviceclass.yaml -f workloads/dranet/namespaces.yaml
control/vendor/helm status dranet -n kube-system
kubectl get deviceclass dra.net
kubectl get resourceslices
```

6. Inspect the installed DaemonSet and pod readiness; identify its actual name with `kubectl -n kube-system get daemonsets`. Use `kubectl -n kube-system rollout status daemonset/ACTUAL_NAME --timeout=180s`.
7. Run section 5's combined validation and section 6's application workflow.

Manual installation does not automatically create a successful Control Center installation job. Refresh discovery and verify configuration before declaring setup complete. Do not change PF/child range under allocated claims. Do not run a separate unguarded node-pool resize alongside Resource Manager ownership.

## 9. Scale from the terminal

The following route uses the same existing Resource Manager stack and exact-plan safeguards as the UI. It does not call a direct OKE resize API.

### Connect and review

```bash
api PUT /api/v1/capacity/connection '{"stack_id":"REPLACE_WITH_OWNING_ORM_STACK_OCID","confirmation":"hpc-oke-rdma"}'
api GET /api/v1/capacity
api GET '/api/v1/capacity/readiness?target=4'
```

Use the real `ocid1.ormstack...` value. Readiness checks are read-only and do not establish future plan contents or write permissions. Verify cycling disabled, nonzero eviction grace, force termination disabled, stack ownership and permissions. Resolve unrelated Terraform drift separately at the current count.

### Create a plan

```bash
api POST /api/v1/jobs '{"action":"capacity_plan","target_node_count":4,"confirmation":"hpc-oke-rdma"}'
```

Poll the returned job, then GET `/api/v1/capacity`. Planning changes the stack target variable but does not apply infrastructure. Review the returned operation, count change, gate result and OCI PLAN job. Copy the exact **capacity operation ID**; it is a 64-character value, not the API job UUID or OCI job OCID.

After reviewing an allowed current plan, submit:

```bash
api POST /api/v1/jobs '{"action":"capacity_apply","capacity_operation_id":"REPLACE_WITH_REVIEWED_OPERATION_ID","confirmation":"SCALE hpc-oke-rdma"}'
```

Poll its job and capacity state. An accepted apply is not completion. Wait for infrastructure convergence and host/device readiness. No automatic performance benchmark is performed.

### Discard or recover

Use only the action appropriate to the recorded operation's current phase:

```bash
# Before apply: restore the previous stack target after reviewing the pending plan.
api POST /api/v1/jobs '{"action":"capacity_discard","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"DISCARD hpc-oke-rdma"}'

# Continue monitoring/recovery of the recorded operation after interruption.
api POST /api/v1/jobs '{"action":"capacity_resume","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"hpc-oke-rdma"}'

# Only after reviewing a terminal failed/canceled Apply in OCI.
api POST /api/v1/jobs '{"action":"capacity_close","capacity_operation_id":"REPLACE_WITH_OPERATION_ID","confirmation":"CLOSE hpc-oke-rdma"}'
```

Do not run these three as a sequence. Discard is unavailable after apply submission. Closing a failure changes neither infrastructure nor desired stack variables. Never resubmit an uncertain Apply without checking the recorded OCI job.

### Zero and scale-in

Targets are **0–64**. For scale-in, pause controllers, stop application pods, preserve needed node-local data and release application RDMA claims/templates. Keep the pool idle through the operation. Infrastructure pods honor disruption budgets; the tool does not force-delete applications. OKE decides which workers are removed.

Use the same readiness/plan/review/apply sequence with target `0`, then later a positive target to restore workers. The cluster/operator remain provisioned. An initially empty deployment needs host checks and initial Dranet installation after its first scale-up. Cross-node validation needs at least two free workers.

For a blocked retained plan, from a matching full project root:

```bash
sudo /opt/oke-rdma-control/venv/bin/python provisioning/explain-capacity-plan.py --recorded
```

In an operator-update archive, the helper is at archive root. It reads the plan without applying it. Review safe changed-field output, then discard the blocked target and reconcile intentional non-scaling changes separately. Do not bypass the guard for IAM repair or a real eviction-setting change.

## 10. Cleanup, reset and updates

### Interrupted validation

Inspect the failed job first. To clean only the tool-owned validation resources:

```bash
api POST /api/v1/jobs '{"action":"cleanup_validation","confirmation":"hpc-oke-rdma"}'
```

Poll completion and inspect claims before retesting. This does not clean arbitrary application resources.

### Application cleanup

Stop the owning controller first. For the supplied example, after confirming these are the intended names:

```bash
kubectl -n rdma-workloads delete pod rdma-example --wait=true
kubectl -n rdma-workloads delete resourceclaim rdma-example --wait=true
```

### Deliberate RDMA reset

Stop application workloads and release their claims/templates. Retrieve the preview:

```bash
api GET /api/v1/reset-plan
```

Review every removal and blocker, require `can_reset`, and copy the returned current `plan_id`. Only then submit:

```bash
api POST /api/v1/jobs '{"action":"reset_rdma","reset_plan_id":"REPLACE_WITH_REVIEWED_PLAN_ID","confirmation":"RESET hpc-oke-rdma"}'
```

Poll the job. A changed inventory requires a fresh preview. Reset retains infrastructure, workers, host drivers, namespaces, pull secrets and history; it removes supported RDMA setup and owned test resources and clears relevant overrides/evidence. Repeat setup afterward. It does not repair a missing registry secret, reboot workers or destroy the cluster.

### Operator updates

Finish or reconcile pending capacity operations first, review the release instructions, extract its operator-update ZIP and run from that extracted directory:

```bash
sudo bash apply-operator-update.sh
oke-rdma-doctor
```

Updating the operator does not update the existing Resource Manager Terraform configuration. Handle Terraform/IAM/image baseline changes in a separately reviewed plan at the current count. These README files contain documentation only; they are not an operator update package.


## 11. Confirmed recovery from the operator terminal

**OPERATOR VM — local API route.** Pause workload controllers first; the initial implementation conservatively blocks when any ResourceClaims/templates exist in the cluster. Recovery requires cluster-administrator access mode. It establishes a temporary node placement barrier and temporarily blocks new claims/templates cluster-wide.

```bash
api POST /api/v1/jobs '{"action":"recovery_assess","node_name":"REPLACE_WITH_WORKER_NAME","confirmation":"hpc-oke-rdma"}'
```

Poll the returned job. Read its `eligible`, `probe`, `issues`, `action`, `protection` and report links. If eligible, use its current `preview_id` within five minutes:

```bash
api POST /api/v1/jobs '{"action":"recovery_run","recovery_preview_id":"REPLACE_WITH_PREVIEW_ID","confirmation":"RECOVER hpc-oke-rdma"}'
```

This performs one targeted agent-service restart after protection and current-evidence checks. It records intent before submission and does not replay an uncertain restart. Poll its job; do not submit another assessment or recovery during an active attempt.

If interrupted or failed, GET `/api/v1/environment` and inspect `recoveries`. To close an attempt without another restart:

```bash
api POST /api/v1/jobs '{"action":"recovery_finish","recovery_attempt_id":"REPLACE_WITH_ATTEMPT_JOB_UUID","confirmation":"FINISH hpc-oke-rdma"}'
```

This leaves the worker cordoned. Run host checks, inspect evidence and only then have an administrator deliberately restore scheduling. If the worker was replaced or is not Kubernetes Ready, finish refuses to modify it. See [Recovery safety and interruption procedure](docs/RECOVERY.md) for manual cleanup of retained resources.

## 12. Known-error decision table

| Error/evidence | Action | Verification |
|---|---|---|
| `api: command not found` | Define the function as `api()`, in this shell; do not name it `pi()`. | GET environment succeeds. |
| `File or stream is not seekable` | Use the separate read/write `/dev/tty` opens in section 2. | Hidden credential prompt appears. |
| Namespace not found | Apply the full namespace manifest before secrets. | Both namespace lookups succeed. |
| Missing `ocir-pull` | Create it in both namespaces; the UI only stores its name. | Registry prerequisite checks pass, then real image pull succeeds. |
| Facts unavailable / container terminated | Read retained exit status/events and logs. | New collection returns actual facts. |
| PF exists, down, MTU 1500, no address | Collect OCA configuration logs. Do not change PF name. | Determine configuration state and specific failure. |
| Metadata 404 → module panic → not responding | Assess confirmed recovery; current metadata must be 200. | Host readiness after one restart; no repeated restart loop. |
| Unused second port disabled | Check the selected PF, not all disabled ports. | Selected port active with configured MTU/address. |
| Worker shows no device | Complete host/driver setup before treating it as reserved. | Device publication plus claim inventory. |

The discovered OCA startup failure is supported by the captured deployment evidence; it is not a claim that all RDMA failures share that cause. Preserve timestamps and current-boot evidence.


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

### Run performance profiles from the operator VM

Use the authenticated `api` shell helper defined earlier in this guide. Define it again after opening a new SSH session. Confirm free endpoints in `api GET /api/v1/environment` and replace the example worker names below with actual Kubernetes node names.

```bash
api POST /api/v1/jobs '{"action":"rdma_validation","server_node":"10.70.1.177","client_node":"10.70.1.211","performance_profile":"sweep","confirmation":"hpc-oke-rdma"}'
api GET /api/v1/jobs
api GET /api/v1/environment
```

The confirmation must match your configured environment name. Choose `quick`, `sweep`, or `loaded`; arbitrary benchmark commands are not accepted. Inspect the submitted job's `state`, `result.status`, `result.extended`, `result.cleanup` and `result.provenance`. A queued job has not measured anything yet. The environment's `performance.pairs` provides directional history and comparison summaries. Use the Operations job report/download controls to retain evidence with its configuration, rather than copying a bandwidth number alone.


## v2.7.0: replace a defective worker

Use **Workers & scaling → Details → Preview worker replacement**, review the exact instance and blockers, then confirm. Follow the complete [worker replacement guide](docs/REPLACEMENT.md) for prerequisites, IAM, resume/close behavior and verification. This release requires an application-idle target and released claims; it does not automatically migrate live application workloads. A failed or incomplete RDMA check never becomes replacement success.

The three sidebar guide-download links have been removed. Use the separately supplied guide bundle for current documentation. Diagnostic, support and workload downloads remain available.


Scale-up capacity check (v2.7.2): review [the required OCI capacity-report step](docs/CAPACITY.md#required-capacity-report-step-before-scale-up-v272) before planning. The report is refreshed before planning and apply; unavailable reports block scale-up. Install the new tenancy-level report permission before using this update to scale out.


v2.7.3 adds read-only assessment with separate restart blockers and a single-worker OCA restart preview. See [Recovery and agent restart](docs/RECOVERY.md) for the UI workflow, CoreDNS handling and retained protection.


Performance v2.8.0: see [all eight measurement workflows](docs/PERFORMANCE.md). Rebuild the tools image for MPI and NUMA profiles. The guide distinguishes measured data, observation intervals and unavailable stages.

## When host diagnosis fails

Read [Host diagnostic troubleshooting](docs/DIAGNOSTIC-TROUBLESHOOTING.md) for visible error meanings, registry credential checks, failed scheduling, deadlines and report collection. Failed collection does not establish RDMA health.
