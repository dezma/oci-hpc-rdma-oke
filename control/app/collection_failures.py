"""Classify collection infrastructure failures without inferring RDMA health."""
import json
from .diagnostics import sanitize_output

PULL_REASONS = {'ErrImagePull', 'ImagePullBackOff', 'InvalidImageName'}


def pull_blocked(pod):
    status = pod.get('status', {})
    return any(c.get('state', {}).get('waiting', {}).get('reason') in PULL_REASONS
               for c in status.get('initContainerStatuses', []) + status.get('containerStatuses', []))


def finding(pod, events, error=''):
    # Use status/events, not arbitrary host logs, to identify collection failures.
    text, _ = sanitize_output(json.dumps({'status': pod.get('status', {}), 'events': events, 'error': error}))
    lower = text.lower()
    pull = pull_blocked(pod) or any(x in lower for x in ('pull image', 'pulling image', 'errimagepull', 'imagepullbackoff', 'failedtoretrieveimagepullsecret'))
    code, message, action = 'diagnostic_collection_failed', 'Host diagnostic collection failed; RDMA health was not established.', 'Download the diagnostic text report and inspect execution status, events and exit reason before retrying.'
    if pull:
        if any(x in lower for x in ('unauthorized', 'authentication required', 'incorrect username or password', 'status code: 401')):
            code, message = 'registry_authentication_failed', 'Registry authentication failed: the diagnostic image could not be pulled. RDMA was not tested.'
            action = 'Check the registry hostname, full namespace/[domain/]username and OCI auth token. Create or update ocir-pull in oke-rdma-diagnostics and rdma-workloads, save its name in Setup & validation, then rerun. Secret creation alone does not verify login.'
        elif 'failedtoretrieveimagepullsecret' in lower:
            code, message = 'registry_secret_unavailable', 'The diagnostic image pull secret could not be retrieved.'
            action = 'Check the configured pull-secret name and provision it in oke-rdma-diagnostics and rdma-workloads. Save the name in Setup & validation, then rerun.'
        elif any(x in lower for x in ('forbidden', 'denied', 'status code: 403')):
            code, message = 'registry_access_denied', 'Registry access was denied while pulling the diagnostic image.'
            action = 'Check the registry identity, auth token and permission to read this repository. Rerun after correcting access.'
        elif any(x in lower for x in ('manifest unknown', 'manifest_unknown', 'not found', 'invalidimagename')):
            code, message = 'registry_image_unavailable', 'The diagnostic image reference could not be resolved.'
            action = 'Verify the registry, namespace, repository and pushed digest. Some registries conceal inaccessible repositories; also check permissions.'
        elif any(x in lower for x in ('timeout', 'no such host', 'connection refused', 'network is unreachable', 'x509')):
            code, message = 'registry_connection_failed', 'The worker could not complete the registry connection.'
            action = 'Check worker DNS, HTTPS egress, routing and certificate errors in the report, then retry.'
        else:
            code, message = 'diagnostic_image_pull_failed', 'The diagnostic image could not be pulled; RDMA was not tested.'
            action = 'Inspect image-pull events in the report for credentials, repository/digest or worker connectivity errors, then retry.'
    elif 'failedscheduling' in lower:
        code, message = 'diagnostic_scheduling_failed', 'The diagnostic pod could not be scheduled.'
        action = 'Inspect scheduler events for worker readiness, taints and available resources. Correct the reported blocker and retry.'
    elif 'oomkilled' in lower:
        code, message = 'diagnostic_out_of_memory', 'The diagnostic container was killed for exceeding its memory allowance.'
    elif 'deadlineexceeded' in lower:
        code, message = 'diagnostic_deadline_exceeded', 'The diagnostic exceeded its deadline; RDMA health was not established.'
    return {'severity': 'block', 'code': code, 'message': message, 'next_action': action}
