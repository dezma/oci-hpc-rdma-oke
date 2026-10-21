# Test capacity reconciliation on an existing operator

This application update does not require recreating the cluster, rebuilding the tools container or applying new Terraform configuration. Keep the current partially provisioned pool as the test case. Run commands from the operator VM after copying or cloning the full project there.

## 1. Prepare the existing environment

Inspect Resource Manager and the node pool's OKE work requests. Wait until provisioning is terminal. In the existing UI, acknowledge the failed Apply if it is still pending. The old UI may label acknowledgement as Complete; it does not roll back infrastructure. Do not erase the capacity journal.

Wait for queued/running Control Center jobs to finish and close the UI while installing. Finish any active recovery or replacement first. Stop application controllers and release workloads/claims before the later scale-down test. Record the current requested count, provisioned instances and node names.

## 2. Update application files

From the full project's root on the operator:

```bash
sudo bash provisioning/update-operator.sh
```

The script supports the GitHub base commit `9e61162`, known v2.9.1/v2.10.0 application files, and this package. It checks installed and replacement file hashes, refuses busy/pending operations, stops the API and worker, backs up the replaced application files and SQLite queue, installs the application changes and checks service health. Configuration, credentials, journals, claims and OCI resources are retained. If install/health verification fails, it restores the replaced application files and restarts services. It prints the backup path on success.

If it reports **Installed file differs**, stop and compare the deployment with the project; do not remove the check. If it reports a pending operation, resolve that operation in the existing UI first. The updater does not bypass the operation lock. A failed Apply can be acknowledged once related provisioning has stopped.

## 3. Verify the UI and plan

Reconnect your Bastion tunnel if needed and hard-refresh the browser. Confirm the footer shows v2.10.0.

For the 3 → 6 attempt that provisioned only 5 workers, expect:

- Current pool target: 6.
- OCI provisioned: 5, if all five are ACTIVE.
- Kubernetes registered / Ready: the observed counts.
- Failed Apply acknowledged, rather than a successful six-worker operation. Original raw historical evidence is retained; current counts are separate.

Enter **5**, check prerequisites and generate a reconciliation plan. Review the plan: it must change only the node-pool target from 6 to 5. At this point, no new Apply has been submitted. You can discard the plan if you are only testing the preview.

To reconcile for real, review and confirm Apply. OKE controls removal behaviour; even a 6 → 5 target alignment retains drain/workload guards. Once finished, refresh and verify requested/provisioned/registered counts agree. Run RDMA validation on surviving workers.

To shrink to **3**, either choose 3 as the corrective target directly, or generate a separate 5 → 3 plan after successful reconciliation to 5. Do not run both plans concurrently. Stop application controllers and release claims before either reduction.

## Expected safe blocks

An active OKE work request, unavailable work-request access, unmatched OCI/Kubernetes inventory, unready or cordoned surviving worker, active workload/claim/template, unsafe eviction setting, or unrelated Terraform change must stop the operation. A new operation must not be submitted automatically. Retain the job ID and evidence if any check fails.

## Validation scope

The package is checked with simulated partial-provisioning and UI tests. The updater and live OCI reconciliation must still be validated in your environment; no successful live deployment is claimed by those local checks.
