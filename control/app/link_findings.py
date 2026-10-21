"""Interpret current selected-PF evidence; never infer health from unused ports."""
import re


def findings(facts, pf, mtu=4220):
    iface = next((i for i in facts.get('interfaces', []) if i.get('name') == pf), {})
    if not iface:
        return []
    result = []
    def add(code, message, evidence, action, severity='block'):
        result.append(dict(code=code, severity=severity, message=message,
                           evidence=evidence, next_action=action))
    link = iface.get('link_evidence', {})
    output = link.get('output', '') if link.get('status') == 'collected' else ''
    no_cable = re.search(r'Link detected:\s*no\s*\(No cable\)', output, re.I)
    if iface.get('carrier') != '1':
        if no_cable:
            add('physical_link_unavailable', f'{pf}: RDMA physical link unavailable; NIC reports “No cable”.',
                f"PCI {iface.get('pci', 'unknown')}; carrier={iface.get('carrier', 'unknown')}; " + no_cable.group(0),
                'Compare compute-cluster membership with healthy workers and investigate NIC/fabric connectivity. Agent restart is not eligible for this finding. This does not prove a physically unplugged cable.')
        else:
            add('carrier_unavailable', f'{pf}: carrier is absent or unknown.',
                f"carrier={iface.get('carrier', 'unknown')}; flags={iface.get('flags', [])}",
                'Review link and authentication diagnostics before selecting recovery.')
    if iface.get('mtu') != mtu:
        add('mtu_mismatch', f'{pf}: MTU {iface.get("mtu", "unknown")}; expected {mtu}.',
            f"Observed MTU: {iface.get('mtu', 'unknown')}",
            'Review OCA RDMA configuration. Changing MTU alone does not restore missing carrier.')
    if link.get('status') == 'unavailable':
        add('link_evidence_unavailable', f'{pf}: detailed NIC link evidence unavailable.',
            link.get('detail', 'ethtool query failed'),
            'Use the host link diagnostic or run ethtool on the worker; do not interpret missing evidence as a healthy link.', 'warn')
    return result


def physical_link_blocked(facts, pf):
    return any(f['code'] == 'physical_link_unavailable' for f in findings(facts, pf))
