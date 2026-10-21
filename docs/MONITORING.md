# Monitoring, drift and alerts

Monitoring helps you notice changes after initial RDMA validation. It never repairs or replaces a worker automatically. All scheduled checks are disabled initially.

## Start in the UI

1. Finish **Setup & validation**, including host readiness and a successful quick RDMA test.
2. Open **Maintenance → Monitoring & drift**. Select **Run health sweep**, review the confirmation and enter the environment name. This collects a snapshot using existing host-check results.
3. Expand **Evidence and profile** for a healthy reference worker. Confirm its kernel, firmware, OFED, OCA, MTU, driver and RDMA mode are appropriate for your fleet. Missing versions mean the version check was unavailable.
4. Expand **Approve a fleet comparison profile**, enter that worker's exact name and confirm. Approval records a comparison profile; it changes no host settings. Approve a profile only after healthy host checks on the current boot.
5. Select **Enable scheduled monitoring**, choose the settings below, enter the environment name and save. Optional host refresh and nightly traffic need their own checkboxes.
6. Refresh the monitoring view to inspect health, profile differences and alert delivery. Scheduled jobs also appear in operation history. Pair measurements appear with other performance results.

The schedule runs on the operator VM even when your browser is closed. A stopped operator or worker service stops monitoring; an external metrics scraper is needed to notice that outage.

## Choose settings

| Field | Meaning |
| --- | --- |
| Check interval | Time between scheduled sweeps; default 900 seconds, minimum 300. |
| Evidence expires after | Host checks older than this become stale; default 7,200 seconds. A reboot also invalidates old evidence. |
| Refresh idle host-check results | Run one short-lived privileged, fixed host diagnostic per sweep, choosing the least recently checked idle worker. Disabled initially. |
| Nightly quick pair test | Test one eligible free pair per UTC day, rotating through pairs. Disabled initially. This creates temporary validation pods/claims and deliberate bandwidth/latency traffic. Use the matrix profile for all-pair coverage. |
| Nightly hour | Earliest UTC hour for that day's test. If busy then, it can run later that day. No backlog of missed days is replayed. |
| Claim review age | Alert when a retained claim exceeds this age; default 24 hours. This asks for review and does not establish that the claim is orphaned or safe to delete. |
| OCI Notifications topic | Optional existing topic OCID in the operator's region; blank retains alerts locally without sending. |
| Pause until | UTC time, at most seven days ahead. Pauses sweeps and nightly tests without changing workers. Clear it to resume. |

Host refresh visits at most one worker per sweep. Set expiry above `number of workers × interval`, allowing extra time for busy workers and long operations. For example, six workers at 15-minute intervals need more than 90 minutes for a complete rotation. Busy workers may still appear stale until a fresh check can run.

All monitoring jobs share the existing serial operation queue. Pending scaling, recovery, replacement, active OKE work requests or active Resource Manager jobs block scheduling. The view reports the scheduler's latest block. Checks do not bypass these locks. Any existing RDMA claim blocks active host refresh and pair tests; occupied or cordoned workers are excluded. A nightly job with fewer than two eligible workers is skipped for that day. These observations do not reserve idle workers against external controllers.

## Read results correctly

- **Healthy:** worker Ready and fresh successful host collection, with no recorded readiness issues.
- **Unhealthy:** observed readiness failure. Expand the results and use the worker diagnosis flow.
- **Unknown:** host collection is absent or failed. Check image access, pod events and diagnostic reports before concluding the hardware failed.
- **Stale:** observations expired or belong to an earlier boot. Run a fresh host check when the worker is available.
- **Maintenance:** worker cordoned or monitoring paused; this does not establish health.

Profile comparison flags changes in known fields. It excludes IP addresses and worker identity, and includes a hash of the configured RDMA settings. A field missing from the approved reference is not compared. Missing current values for known reference fields appear as incomplete. Review and approve a new reference after an intentional fleet change; one reference suits a homogeneous fleet.

Two consecutive sweeps must observe a changed condition before an alert is recorded. Conditions include worker health/absence, configuration drift, driver readiness, collection failure, long-held claims and the latest failed validation. Resolution also requires two observations. Short-lived faults between sweeps may go undetected. A small bandwidth regression does not itself trigger an alert; inspect the retained performance comparisons.

## Send alerts through OCI Notifications

1. In the same OCI region, open **Developer Services → Application Integration → Notifications**. Create a topic in your project compartment, or ask the compartment administrator for an existing topic.
2. Create an email or other supported subscription and complete its confirmation. Publishing sends to the topic's subscriptions.
3. Ask the compartment administrator to grant the operator’s existing dynamic group permission to read topics and publish. The application reads the topic endpoint before sending. Replace both names below:

```text
Allow dynamic-group <operator-dynamic-group> to read ons-topics in compartment <project-compartment>
Allow dynamic-group <operator-dynamic-group> to use ons-topics in compartment <project-compartment> where request.operation = 'PublishMessage'
```

4. Copy the topic OCID into the monitoring form and save. The application does not require tenancy-wide notification permissions or create a topic/subscription for you.
5. Use the topic's Console **Publish message** action to test subscriber delivery. This tests the subscription path; application delivery additionally requires the operator's publish policy.

The application keeps at most 100 alerts. With no topic configured they remain pending locally. Adding a topic can send retained pending alerts. Each sweep attempts up to three due alerts. Each alert has at most three application attempts. Each attempt looks up the topic endpoint, then publishes, with a 15-second timeout per call and CLI retries disabled. Retries wait at least the configured exponential delay and run on a later sweep. A crash after publish may produce a duplicate: use `event_id` for deduplication. **Sent** means OCI accepted the publish. Check the subscription to confirm receipt. Failed alerts remain visible for review. Pausing/disabling monitoring pauses delivery, too.

If publication fails, check topic region/OCID, the operator dynamic group's membership, compartment read/publish policies and service reachability. If publication succeeds but no email arrives, check subscription confirmation and spam filtering. Do not paste authentication tokens into the UI.

References: [Oracle publishing guide](https://docs.oracle.com/en-us/iaas/Content/Notification/Tasks/publishingmessages.htm), [notification permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/notificationpolicyreference.htm).

## Export metrics

`GET /metrics` returns Prometheus text from stored monitoring results and retained jobs. Scraping never starts diagnostics. Metrics include worker state, check timestamps, drift count, free RDMA slots, claim age, retained job/alert counts and pair bandwidth/latency. Job counts cover the retained window of up to 500 jobs.

On the **operator VM**, create a separate read-only token. This command refuses to overwrite an existing token:

```bash
sudo python3.11 - <<'PY'
import os, pathlib, pwd, secrets
path = pathlib.Path('/etc/oke-rdma-control/metrics.token')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(secrets.token_urlsafe(32) + '\n')
account = pwd.getpwnam('opc')
os.chown(path, account.pw_uid, account.pw_gid)
print('Read-only metrics token created')
PY
```

No restart is needed. Transfer that token securely to a private Prometheus instance and restrict its file to the Prometheus service account. Keep the API private. The example below assumes an existing SSH tunnel on the Prometheus host forwarding its local port 18000 to operator port 8000; it does not create that tunnel or expose the API publicly.

Complete minimal `prometheus.yml`:

```yaml
global:
  scrape_interval: 60s
scrape_configs:
  - job_name: oke-rdma
    metrics_path: /metrics
    scheme: http
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/oke-rdma-metrics.token
    static_configs:
      - targets:
          - 127.0.0.1:18000
```

This token cannot access administration APIs. The administrator token is also accepted for metrics but is unnecessary for scraping. Never send a bearer token across an untrusted plaintext network.

Use `up{job="oke-rdma"} == 0` to detect scrape failure. Inspect `time() - oke_rdma_last_success_timestamp_seconds` for stale successful snapshots, and `oke_rdma_host_check_timestamp_seconds` for each host's check age. Zero means no successful observation exists. Retained slot counts and claim ages describe the last successful snapshot; gate their use on freshness and `oke_rdma_collection_success`.

`oke_rdma_pair_latest_attempt_success` describes the latest terminal attempt with an identified pair. Measurement metrics describe the last retained attempt that produced bandwidth; use `oke_rdma_pair_measurement_timestamp_seconds` and `oke_rdma_pair_measurement_success` alongside them. Automatically selected tests that fail before selecting a pair may have no pair-labelled result; inspect job outcomes and alerts. Check the latest attempt status before relying on an older bandwidth result.

See [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/) for your scraper's deployment options. This package does not install Prometheus or Grafana.

## Update an existing operator

Copy the full project to the operator VM. Finish active jobs and reconcile any pending scaling, recovery or replacement operation, then close the UI. From the project root:

```bash
sudo bash provisioning/update-operator.sh
```

The updater accepts the GitHub base used for this package (commit `9e61162`), known v2.9.1/v2.10.0 application files, or this package, checks file hashes, excludes the scheduler, backs up changed files and the queue, and verifies API/worker health. It preserves configuration, credentials and infrastructure. If installed hashes differ, stop and compare your deployment rather than bypassing verification. Hard-refresh the UI and confirm version **2.10.0**. New monitoring remains disabled until you save its settings.

For a new deployment, build/upload the ORM ZIP using the main README. Updating an existing application's code does not require an infrastructure Apply.
