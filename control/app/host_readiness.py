"""Fixed, read-only host inventory used before publishing an RDMA PF to DRA."""
SCRIPT = r'''set -eu
python3 - <<'DRANET_HOST_PY'
import json, pathlib, socket, stat, subprocess

def read(path, default=''):
    try: return pathlib.Path(path).read_text().strip()
    except OSError: return default

def run(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if p.returncode: raise RuntimeError(' '.join(args) + ': ' + p.stderr[:300])
    return p.stdout

def optional(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return {'status': 'collected' if p.returncode == 0 else 'unavailable',
                'output': p.stdout[:4000] if p.returncode == 0 else '',
                'detail': p.stderr[:200] if p.returncode else ''}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'unavailable', 'output': '', 'detail': type(exc).__name__}

addresses = {i['ifname']: i.get('addr_info', []) for i in json.loads(run('ip', '-j', '-4', 'address', 'show'))}
interfaces = []
for iface in json.loads(run('ip', '-j', 'link', 'show')):
    name = iface['ifname']
    path = pathlib.Path('/sys/class/net') / name
    device = path / 'device'
    if not device.exists(): continue
    interfaces.append({
        'name': name, 'mtu': iface['mtu'], 'flags': iface.get('flags', []),
        'addresses': [a['local']+'/'+str(a['prefixlen']) for a in addresses.get(name, []) if a['family']=='inet'],
        'mac': iface.get('address', ''), 'carrier': read(path/'carrier'),
        'pci': device.resolve().name, 'driver': (device/'driver').resolve().name,
        'driver_evidence': optional('ethtool', '-i', name) if (device/'driver').resolve().name.startswith('mlx5') else {},
        'link_evidence': optional('ethtool', name) if (device/'driver').resolve().name.startswith('mlx5') else {},
        'num_vfs': int(read(device/'sriov_numvfs', '0')),
        'vf_links': len(list(device.glob('virtfn*'))),
    })
try: nri = stat.S_ISSOCK(pathlib.Path('/var/run/nri/nri.sock').stat().st_mode)
except OSError: nri = False
units = {}
for unit in ['sriov-config.service', 'sriov-config-post-network.service']:
    p = subprocess.run(['systemctl', 'is-enabled', unit], capture_output=True, text=True, timeout=10)
    units[unit] = p.stdout.strip() or 'not-found'
facts = {'system_serial': read('/sys/class/dmi/id/product_serial'), 'hostname': socket.gethostname(), 'boot_id': read('/proc/sys/kernel/random/boot_id'),
         'netns': run('rdma','system','show').strip(), 'rdma_links': run('rdma','link','show'),
         'ofed_version': optional('ofed_info', '-s'),
         'oca_version': optional('snap', 'list', 'oracle-cloud-agent'),
         'nri_socket': nri, 'interfaces': interfaces, 'sriov_boot_units': units}
print('DRANET_FACTS=' + json.dumps(facts))
DRANET_HOST_PY
'''
