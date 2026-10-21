## Build the tools image and configure registry access on the operator VM

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

```bash
python3 - <<'PY'
import base64, getpass, json, subprocess
def prompt(label):
    with open('/dev/tty', 'w') as output:
        output.write(label)
        output.flush()
    with open('/dev/tty', 'r') as terminal:
        return terminal.readline().strip()
registry = prompt('Registry host (no https://): ')
username = prompt('Registry username: ')
password = getpass.getpass('Registry auth token/password: ')
if not registry or not username or not password:
    raise SystemExit('All credential fields are required')
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
