# Troubleshoot a failed host diagnostic

When collection fails, fix the reported access, image or execution problem first. Then rerun host readiness and cross-node validation to check the NIC.

## From the UI

1. Open the failed Host diagnostic result and read the visible error and Next step.
2. For registry authentication errors, use the credential setup steps in [the UI guide](UI.md) or [the operator guide](OPERATOR-VM.md). Save `ocir-pull` in Setup & validation → Existing image pull secret. Never enter a token in the UI.
3. Download text (or JSON) from that result for the full output. Look for `EXECUTION_STATUS` and `EVENTS`. The diagnostic pod is normally deleted after saving its output, so a later `kubectl describe pod` may find nothing.
4. Correct the specific failure and rerun the diagnostic. Image-pull errors end collection early; a transient error may succeed on a new run after connectivity is restored.
5. Only after collection succeeds should you interpret link, MTU, parent IPv4 and OCA findings as host checks.

| Visible error / event | Meaning | What to check |
| --- | --- | --- |
| Registry authentication failed / Unauthorized | Registry rejected authentication, or no usable credentials were supplied | Host key matches image hostname; full namespace/[domain/]username; valid OCI auth token; configured secret name |
| Pull secret unavailable / FailedToRetrieveImagePullSecret | Kubernetes could not retrieve the named secret | Secret exists in the pod namespace; UI name is correct; secret type is a registry type |
| Registry access denied / Forbidden | Registry denied access | Identity and token, repository read permissions and repository path |
| Image reference unavailable / manifest unknown | Image or digest cannot be resolved, or repository access is concealed | pushed immutable digest, registry and repository path, permissions |
| Registry connection failed | Worker could not complete registry connection | DNS, HTTPS egress, routing, TLS/certificate error in events |
| FailedScheduling | Pod cannot be placed | Node readiness, taints and resources shown in scheduler events |
| DeadlineExceeded | Pod exceeded its allowed lifetime | Earlier events first: an image-pull failure can cause the final deadline |
| OOMKilled | Runtime reports memory exhaustion | Diagnostic resource allowance and workload; retain report before retry |
| ContainerStatusUnknown with exit 137 | Container state was unavailable at termination | Inspect earlier events and whether a container ever started; look for an explicit OOMKilled reason |
| Collection failed without a recognized cause | Script, runtime, API or log collection failed | Download report; inspect exit reason, events and execution error |

## On the operator VM

Confirm that kubectl targets the recreated cluster, then inspect secret metadata only:

```bash
kubectl config current-context
kubectl -n oke-rdma-diagnostics get secret ocir-pull
kubectl -n rdma-workloads get secret ocir-pull
```

Expected type: `kubernetes.io/dockerconfigjson`. Verify registry access with a worker image pull. Do not paste secret YAML or decoded credentials into support reports. Re-run the credential script in either guide to refresh both copies, then save the secret name in the UI and rerun diagnosis.

Podman uses the operator’s login; Kubernetes workers use the pull secret in the pod’s namespace. A new cluster needs its own secrets even if the registry repository and operator login still exist. Changing availability domain alone does not require a new regional registry hostname.

## Example: authentication failure followed by deadline

`Unauthorized → ErrImagePull → ImagePullBackOff → DeadlineExceeded` means the diagnostic image could not start. Restore registry access; do not reboot or replace the RDMA worker based on this sequence. RDMA health remains not established. The UI reports the earlier registry cause rather than treating the deadline as the root cause.

References: [OCI registry credentials](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrypullingimagesfromocir.htm), [OCIR login identity](https://docs.oracle.com/en-us/iaas/Content/Registry/Tasks/registrypullingimagesusingthedockercli.htm).
