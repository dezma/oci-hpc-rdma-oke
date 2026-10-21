# Project review

Base: `https://github.com/dezma/oci-hpc-rdma-oke`, branch `main`, commit `9e611621db1a45138db406f9497ffdc525e2c09a`.

The delivery uses this checkout as its source. No commit or push was made to GitHub, and no running deployment was changed.

## Preserved

- The architecture PNG is unchanged and included in the source archive.
- The simplified Terraform UI-token output description is retained.
- The Ubuntu HPC image import instructions and download URLs are retained.
- The partial-capacity reconciliation implementation and its safeguards are retained.
- Existing Terraform resources, defaults, pinned dependencies and workload templates are retained. Schema version labels are aligned to the application version.

## Application integration

The application includes v2.10.0 scheduled monitoring, profile approval and drift comparison, optional idle-host refresh, optional nightly quick pair testing, authenticated Prometheus metrics and OCI Notifications alerts. Scheduled work shares the operation queue and starts disabled. Monitoring does not automatically repair or replace workers.

The monitoring form uses compact checkboxes beside their labels, responsive columns and clearer spacing. Notification delivery reads the topic endpoint before publishing, with timeouts and a three-attempt limit. Topic-read and publish permissions are documented separately.

The updater accepts verified file hashes from the GitHub base, the supported v2.9.1/v2.10.0 packages and this delivery. It keeps existing journals, settings and credentials and blocks installation during active operations. See `docs/MONITORING.md` for use.

## Review findings addressed

| Finding | Resolution |
| --- | --- |
| README had an extra closing parenthesis and a placeholder checkout directory | Corrected the link and used `cd oci-hpc-rdma-oke` |
| Capacity guide linked to a missing file | Included `docs/TESTING-CAPACITY.md` |
| Replacement heading, introduction and IAM explanation were incomplete | Removed the heading period, restored the decision guidance and documented pool-scoped DeleteNode plus compartment work-request reads |
| Guides referenced a nonexistent combined systemd service | Commands now inspect `oke-rdma-control-api.service` and `oke-rdma-control-worker.service` |
| Architecture image would be omitted from generated source ZIPs | Added it to the packaging allowlist and packaging regression coverage |
| Diagram labels differed from Terraform | Preserved the image and added linked notes explaining managed Bastion, private subnets, OCIR, subnet defaults and optional monitoring |
| Application/package/schema version labels differed | Aligned them to 2.10.0 |
| Build scripts lacked executable bits | Marked provisioning and bootstrap shell scripts executable |
| Repetitive warnings and compressed technical prose made guides difficult to follow | Rewrote performance/security explanations and edited every guide; preserved operational warnings about credentials, data loss and uncertain submissions |

The documentation now uses six instances of “evidence”, including UI labels, compared with 38 in the base. The reviewed README, security page and guides contain none of the flagged filler words “actual”, “deliberately”, “explicitly”, “intentionally”, “silently”, “legitimately” or “bounded”. Necessary limits are stated directly where verified.

## Validation

- 307 Python tests passed, including monitoring, notification endpoint selection, partial scaling, updater checks, guide snippets and packaging.
- UI DOM interaction tests passed, including monitoring controls and confirmations.
- Local Markdown links and linked headings resolve.
- Embedded Bash/Python snippets parse, and the guide's complete Dockerfile matches the source recipe.
- JavaScript and shell syntax checks passed.
- Terraform 1.5.7 formatting checks passed; HCL/schema contracts passed in the test suite.
- Pinned vendor checksums passed. The rebuilt ORM payload is checked against current application files and its internal checksum manifest.
- The source and ORM archives passed ZIP integrity checks and the ORM size budget.

## Validation still required in the deployment

No live OCI Apply, worker diagnostic, RDMA benchmark, notification delivery or updater run was performed. Terraform provider validation/plan was not run with initialized providers. Browser rendering could not be verified in this workspace; inspect the monitoring form at desktop/mobile widths after updating. External image-download link expiry and region-specific shape/image compatibility must be checked when provisioning.

The archive contains source and deployment artifacts only: no runtime state, credentials, Git metadata, Python caches, node dependencies or locally generated test output.
