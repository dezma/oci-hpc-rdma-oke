# Pinned operator dependencies

`lock.json` records the dependency versions and exact SHA-256 hashes:

- Helm v3.19.0, Linux x86_64 executable from the official Helm release.
- Dranet v1.5.0 chart from `oci://registry.k8s.io/networking/charts/dranet`.

Run `provisioning/fetch-vendor.sh` from the repository root to fetch missing files. The script verifies them against the committed lock before packaging. The binary and chart are generated inputs excluded from source archives and Git; their upstream licenses are included here.

The operator target is Oracle Linux 9 x86_64 with Python 3.11. The compact payload includes the chart, locks, licenses, application code and `PAYLOAD-SHA256SUMS`. It excludes the Helm executable and Python wheels. Bootstrap verifies the payload, downloads Helm using the same checksum lock, then downloads and verifies the exact Python wheel set listed in `control/runtime-requirements.lock`.

Upstream sources: [Helm v3.19.0](https://github.com/helm/helm/tree/v3.19.0) and [Dranet v1.5.0](https://github.com/kubernetes-sigs/dranet/tree/v1.5.0).
