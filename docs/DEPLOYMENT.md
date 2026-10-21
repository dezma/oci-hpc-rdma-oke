# Deployment reference

For Ubuntu HPC download links and custom-image import steps, see [Create the worker image](CUSTOM-IMAGES.md).

For the complete first-time walkthrough, start with [README](../README.md). This page explains prerequisites and the restricted-access route.

## Worker and network prerequisites

- Reference shape: BM.Optimized3.36 in a supported AD with available capacity, sufficient service limits/quota and compatible Compute Cluster placement.
- Approved Ubuntu HPC worker image with RDMA drivers/tools and Oracle Cloud Agent HPC configuration/RDMA authentication plugins. A stock operating-system image alone is insufficient.
- Full OKE patch version offered in the region and accepted by the compatibility mapping in `terraform/locals.tf`; select the matching Ubuntu release.
- Parent fabric interface `ens800f0np0` for the reference profile, configured by host services with its parent IPv4 address and MTU 4220. The reference parent range is `10.224.0.0/12`; the default child range is `10.208.0.0/12`.
- CRI-O NRI socket `/var/run/nri/nri.sock`, shared RDMA namespace mode, and no competing controller managing the same physical function.
- Nonoverlapping VCN, pod, service, parent and child address ranges. Ask the network administrator before changing defaults.
- Operator outbound HTTPS to OCI APIs, Oracle Linux repositories, `get.helm.sh`, `dl.k8s.io`, `pypi.org` and `files.pythonhosted.org`, including required redirects. Workers need their OS/package and registry paths. NAT/service routing must match the approved network design.

The ORM package carries the application payload and pinned chart. Bootstrap downloads and verifies the other required binaries/packages. Protect Terraform state and stack outputs because privileged configuration may be present.

## Image selection

Pin the operator using **Advanced operator VM → Approved operator image override**. The form displays image names and stores the chosen OCID. Direct Terraform uses the explicit `operator_image_ocid` variable. Select the approved worker image independently. Do not change either image as part of a worker-count-only operation.

Build the test tools image on the operator **after** provisioning. Follow [image and registry setup](IMAGE-PULL-SECRET.md). This container supplies the diagnostic and benchmark tools; the operator and worker VM images are selected during provisioning.

## Restricted Kubernetes access

With `operator_kubernetes_access_mode=least_privilege_rbac`, a cluster administrator must apply the complete `operator_rbac_manifest` stack output to the intended cluster. This permits curated workload and diagnostic operations but does not grant permission to create all Dranet infrastructure or use administrator-only recovery/replacement.

After successful host checks, a cluster administrator can install Dranet from the full source directory. Save the desired workload settings first and export complete values through the [operator API helper](OPERATOR-VM.md):

```bash
api GET /api/v1/exports/values > dranet-values.yaml
```

Only continue if the export succeeded and its contents match the intended interface/range:

```bash
./provisioning/fetch-vendor.sh
control/vendor/helm upgrade --install dranet control/vendor/dranet-v1.5.0.tgz \
  --namespace kube-system --values dranet-values.yaml --wait --timeout 3m
kubectl apply --server-side --field-manager=oke-rdma-bootstrap \
  -f workloads/dranet/deviceclass.yaml -f workloads/dranet/namespaces.yaml
kubectl -n kube-system get daemonsets,pods -o wide
kubectl get deviceclass dra.net
kubectl get resourceslices
```

Run each command only after the previous one succeeds. Refresh discovery in the Control Center and run the quick pair test. Record a passing quick pair test after manual installation.

## Provisioning checkpoints

| Checkpoint | Expected result |
| --- | --- |
| Infrastructure | Successful Resource Manager Apply and expected outputs |
| Operator bootstrap | Active `oke-rdma-control-api.service` and `oke-rdma-control-worker.service` services and successful `oke-rdma-doctor` checks |
| Kubernetes | Intended workers registered and Ready |
| Host | Successful inventory and no host readiness blockers |
| Driver | Ready Dranet pods and valid published devices |
| Function | Positive bandwidth and latency measurements with successful cleanup |

Use [IAM setup](IAM.md) before provisioning and [troubleshooting](TROUBLESHOOTING.md) when a checkpoint fails.
