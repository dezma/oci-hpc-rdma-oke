# Contributing

Use the [development guide](docs/DEVELOPMENT.md) to run checks and build both release archives. Keep deployment instructions suitable for a new environment, and use synthetic identifiers in examples and fixtures.

For an issue, include the component version, shape, image family, Kubernetes version, failing operation and a redacted error. Do not attach tokens, private keys, Terraform state, kubeconfigs or unreviewed host logs to public issues.

For a pull request, describe the problem, resulting behavior and verification. Update full YAML examples when manifest generation changes. Cover meaningful orchestration, configuration or packaging changes with tests. Preserve ownership checks, diagnostics with time limits and cleanup scoped to this application's validation resources.

Test infrastructure changes in an isolated OCI environment with the appropriate service limits. Record results without committing environment-specific resource identifiers, generated payloads or local state. Use the release scripts to build distributable artifacts.
