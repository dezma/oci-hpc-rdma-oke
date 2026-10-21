"""Measurement provenance and freshness. Never synthesize missing metrics."""
import hashlib
import json
import math
import time

MAX_AGE_SECONDS = 86400


def context(nodes, settings):
    fleet = sorted([{'name': n['name'], 'uid': n.get('uid', ''), 'boot_id': n.get('boot_id', '')}
                    for n in nodes if n.get('mode') == 'dranet'], key=lambda n: n['name'])
    configuration = settings.rdma_config()
    return {'schema_version': 1, 'fleet': fleet,
            'configuration_sha256': hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()}


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def summarize(jobs, environment, settings, now=None):
    now = time.time() if now is None else now
    reset = environment.get('reset_at', 0)
    attempts = sorted([j for j in jobs if j.get('action') in {'rdma_validation', 'monitoring_benchmark'} and j.get('requested_at', 0) > reset],
                      key=lambda j: j.get('requested_at', 0), reverse=True)
    latest = attempts[0] if attempts else None
    measured = next((j for j in attempts if positive((j.get('result') or {}).get('average_gbps'))), None)
    result = {'latest_attempt': {k: latest.get(k) for k in ('id', 'state', 'requested_at', 'finished_at')} if latest else None,
              'measurement': None, 'validation_current': False, 'max_age_seconds': MAX_AGE_SECONDS}
    if not measured:
        return result
    r = measured['result']; recorded = r.get('measured_at')
    evidence = r.get('context'); current = context(environment['nodes'], settings)
    reasons = []
    if not evidence or evidence.get('schema_version') != 1:
        reasons.append('This historical run has no recorded fleet or configuration fingerprint.')
    else:
        if evidence.get('fleet') != current['fleet']:
            reasons.append('Workers were added, removed, replaced, or rebooted after this run.')
        if evidence.get('configuration_sha256') != current['configuration_sha256']:
            reasons.append('RDMA configuration changed after this run.')
        if not evidence.get('fleet') or any(not n.get('uid') or not n.get('boot_id') for n in evidence['fleet']):
            reasons.append('Worker identity or boot evidence was unavailable for this run.')
    if not positive(recorded) or recorded > now + 60:
        reasons.append('A reliable measurement timestamp is unavailable.')
    elif now - recorded > MAX_AGE_SECONDS:
        reasons.append('The measurement is more than 24 hours old.')
    latency = r.get('latency') or {}
    complete = latency.get('status') == 'passed' and positive(latency.get('average_us'))
    if not complete:
        reasons.append('Latency failed in this run.' if latency else 'Latency was not recorded in this run.')
    if latest['id'] != measured['id']:
        reasons.append('A newer validation attempt exists; these metrics belong to the earlier run.')
    if measured.get('state') != 'SUCCEEDED' or r.get('status') != 'passed':
        reasons.append('The run did not complete successfully; retained metrics are partial evidence.')
    sample = {'job_id': measured['id'], 'measured_at': recorded, 'nodes': r.get('nodes', []),
              'average_gbps': r['average_gbps'], 'latency': {k: latency[k] for k in ('average_us','median_us','p99_us','p999_us','measured_at') if k in latency} if complete else None,
              'freshness': 'current' if not reasons else 'review', 'reasons': reasons,
              'scope': 'Pair measurement only; it does not benchmark every worker or every network path.'}
    result.update(measurement=sample, validation_current=not reasons)
    return result
