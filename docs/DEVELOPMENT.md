# Develop, test and package

## Build environment

Use Linux x86_64, Python 3.11+, Node.js 22, Terraform 1.5.7 or later, `curl`, `tar`, and `sha256sum`. A Linux x86_64 development container is suitable. The operator payload targets Oracle Linux 9 x86_64 and Python 3.11, independent of the builder's Python version.

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r control/requirements.lock pytest httpx python-hcl2==8.1.4
npm ci
./provisioning/fetch-vendor.sh
./provisioning/build-operator-payload.sh
terraform -chdir=terraform init -backend=false -input=false
./provisioning/validate.sh
npm run test:ui
```

The checks cover API authentication, allowed operations, DRA allocation and pod settings, host readiness, ownership-scoped cleanup, durable jobs, TTL/schema wiring, packaging exclusions, and UI interactions. Terraform validation requires its provider executables. UI tests exercise the DOM; also inspect layout and keyboard interaction in a browser when changing presentation or navigation.

## Local UI preview

Run the API and worker in separate terminals with the same environment:

```bash
export HPC_CONTROL_CONFIG="$PWD/.demo/no-live-config.json"
export HPC_CONTROL_STATE_DIR="$PWD/.demo"
export HPC_CONTROL_DB="$PWD/.demo/control.db"
export HPC_CONTROL_RDMA_CONFIG="$PWD/.demo/rdma.json"
export HPC_CONTROL_REPORT_DIR="$PWD/.demo/reports"
export HPC_CONTROL_DEMO_TOKEN=demo
export PYTHONPATH="$PWD/control"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# In the second terminal, after setting the environment above:
python -m app.worker
```

Open `http://127.0.0.1:8000` and enter `demo`. Sample inventory is labelled; actions simulate workflow state and never change OCI resources or report measured throughput. Use separate paths for demo state and any live configuration.

## Release archives

Run:

```bash
./provisioning/build-release.sh
```

The build downloads and verifies pinned Helm/chart inputs, constructs a compact application payload, and creates:

- `dist/oci-oke-rdma-quickstart-v<VERSION>.zip`: source suitable for a repository checkout, including documentation, tests and build scripts.
- `dist/oci-oke-rdma-quickstart-orm-v<VERSION>.zip`: Terraform and `schema.yaml` at the ZIP root, with the application payload, chart and dependency locks.
- `dist/SHA256SUMS`: checksums for the generated archives.

The source archive excludes generated binaries, payloads, dependencies, local state, credentials, logs and test output. The packager selects explicit source paths and verifies that the ORM payload matches the current application. It also rejects symlinks and state files in the payload. ORM packaging also enforces a conservative **10,000,000-byte** budget on both the configuration files and ZIP, below the Resource Manager stack upload limit. Keep release archives outside Git and attach them to a release using your publishing process.

`build-orm.sh` builds only the ORM archive. `package-release.py --mode source` packages source without downloading runtime dependencies. `VERSION` controls artifact names; keep application/package and ORM schema versions aligned when cutting a release.

## Runtime dependency lock

`control/runtime-requirements.lock` pins the complete operator dependency set and SHA-256 hashes for Python 3.11/Linux x86_64. Bootstrap downloads wheels with explicit `manylinux2014_x86_64` and `manylinux_2_28_x86_64` platform tags and requires hashes during download and installation. Update this lock together with `control/requirements.lock` when changing runtime dependencies; resolve and hash a complete target-compatible wheel set, then verify it with pip before release. Wheel binaries are never included in the ORM archive.

The operator requires outbound HTTPS to the dependency repositories listed in the deployment guide. Payload and runtime download progress appears in `/var/lib/oke-rdma-control/bootstrap-status.json`.

## Infrastructure validation

Use `terraform/terraform.tfvars.example` as a local template and supply your own inputs; do not commit the resulting values or Terraform state. `provisioning/list-oke-options.sh` lists available Kubernetes versions and bare-metal shapes using an authenticated OCI CLI.

Review the plan in an isolated compartment before applying. For a complete end-to-end check, follow [Deployment](DEPLOYMENT.md), complete host readiness and driver installation, and run cross-node RDMA validation. Verify both Bastion session lifetimes and workload cleanup. Destroy the environment when it is no longer needed.

## Workspace navigation

Maintenance must remain the final workspace feature in both sidebar navigation and page content. `arrangeWorkspace()` enforces this order and numbers sidebar items after all feature links are present. Keep the navigation regression test when adding features.
