#!/usr/bin/env python3
"""Package only explicit source inputs and a separately verified ORM payload."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATTERNS = (
    'README.md', 'README-*.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'VERSION',
    'RELEASE.json', 'LICENSE', 'NOTICE', 'Makefile', 'package.json', 'package-lock.json', '.gitignore',
    '.github/workflows/*.yml', '.github/workflows/*.yaml', 'docs/*.md',
    'provisioning/*.sh', 'provisioning/*.py', 'tests/*.py', 'tests/*.cjs',
    'control/guides/*.md', 'control/guides/docs/*.md', 'control/app/*.py', 'control/validation/*.py', 'control/web/*.html',
    'control/web/*.js', 'control/web/*.css', 'control/requirements.lock',
    'control/runtime-requirements.lock', 'control/bootstrap/*.sh',
    'control/systemd/README.md', 'control/vendor/README.md',
    'control/vendor/*-LICENSE', 'control/vendor/lock.json',
    'terraform/*.tf', 'terraform/schema.yaml', 'terraform/.terraform.lock.hcl',
    'terraform/terraform.tfvars.example', 'terraform/operator/*.tftpl',
    'workloads/dranet/*.yaml', 'workloads/rdma-tools/Dockerfile',
)
ORM_PATTERNS = (
    '*.tf', 'schema.yaml', '.terraform.lock.hcl', 'terraform.tfvars.example',
    'operator/*.tftpl', 'operator/payload/oke-rdma-control.tar.gz',
)
# Conservative budget below Resource Manager's documented 11 MB stack ZIP limit.
MAX_ORM_BYTES = 10_000_000

DENIED_NAMES = {'override.tf', 'override.tf.json'}


def members(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    candidates = {p for pattern in patterns for p in root.glob(pattern)}
    result = []
    for p in sorted(candidates):
        if p.is_symlink():
            raise ValueError(f'Symlinks are not release inputs: {p}')
        if not p.is_file() or p.name in DENIED_NAMES or p.name.endswith(('_override.tf', '_override.tf.json')):
            continue
        result.append(p)
    return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_payload(root: Path) -> Path:
    payload = root / 'terraform/operator/payload/oke-rdma-control.tar.gz'
    allowed_dirs = {'app', 'web', 'validation', 'vendor', 'bootstrap', 'guides'}
    with tarfile.open(payload, 'r:gz') as archive:
        files = {}
        for item in archive.getmembers():
            name = item.name.removeprefix('./')
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or item.issym() or item.islnk():
                raise ValueError(f'Unsafe payload member: {name}')
            if not item.isfile():
                continue
            if path.parts[0] not in allowed_dirs and name not in {'requirements.lock', 'runtime-requirements.lock', 'PAYLOAD-SHA256SUMS'}:
                raise ValueError(f'Unexpected payload member: {name}')
            if '__pycache__' in path.parts or path.suffix in {'.pyc', '.db', '.log'}:
                raise ValueError(f'Runtime state in payload: {name}')
            if name == 'vendor/helm' or path.suffix == '.whl':
                raise ValueError(f'Large runtime dependency must be downloaded at bootstrap: {name}')
            if name in files:
                raise ValueError(f'Duplicate payload member: {name}')
            files[name] = archive.extractfile(item).read()
        for folder in ('app', 'web', 'validation', 'vendor', 'bootstrap'):
            for p in (root / 'control' / folder).rglob('*'):
                if p.name == 'helm' and folder == 'vendor':
                    continue
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                    name = p.relative_to(root / 'control').as_posix()
                    if files.get(name) != p.read_bytes():
                        raise ValueError(f'Payload out of date: {name}')
        for name in ('requirements.lock', 'runtime-requirements.lock'):
            if files.get(name) != (root / 'control' / name).read_bytes():
                raise ValueError(f'Payload requirements out of date: {name}')
        checksums = {}
        for line in files['PAYLOAD-SHA256SUMS'].decode().splitlines():
            expected, name = line.split('  ', 1)
            checksums[name] = expected
            if hashlib.sha256(files[name]).hexdigest() != expected:
                raise ValueError(f'Payload checksum mismatch: {name}')
        if set(checksums) != set(files) - {'PAYLOAD-SHA256SUMS'}:
            raise ValueError('Every payload file must be covered by the checksum manifest')
    return payload


def write_zip(output: Path, root: Path, paths: list[Path], prefix: str = '') -> None:
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in paths:
            archive.write(p, prefix + p.relative_to(root).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError(f'Archive failed integrity check: {output}')


def write_orm_zip(output: Path, root: Path, paths: list[Path]) -> None:
    expanded_size = sum(p.stat().st_size for p in paths)
    if expanded_size > MAX_ORM_BYTES:
        raise ValueError(f'ORM configuration exceeds the {MAX_ORM_BYTES:,}-byte build budget before compression')
    temporary = output.with_suffix('.zip.partial')
    try:
        write_zip(temporary, root, paths)
        if temporary.stat().st_size > MAX_ORM_BYTES:
            raise ValueError(f'ORM ZIP exceeds the {MAX_ORM_BYTES:,}-byte build budget')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def build(root: Path, output: Path, mode: str = 'all') -> list[Path]:
    version = (root / 'VERSION').read_text().strip()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    lock = json.loads((root / 'control/vendor/lock.json').read_text())
    release = {'version': version, 'network_profile': 'Dranet OKE IPvlan',
               'dranet': lock['dranet_version'], 'helm': lock['helm_version'],
               'deployment_guide': 'docs/DEPLOYMENT.md',
               'runtime_dependencies': 'checksum-verified bootstrap downloads',
               'orm_build_budget_bytes': MAX_ORM_BYTES}
    if mode in {'orm', 'all'}:
        payload = verify_payload(root)
        orm = output / f'oci-oke-rdma-quickstart-orm-v{version}.zip'
        write_orm_zip(orm, root / 'terraform', members(root / 'terraform', ORM_PATTERNS))
        release.update(payload_sha256=digest(payload), orm_sha256=digest(orm), orm_bytes=orm.stat().st_size)
        artifacts.append(orm)
    (root / 'RELEASE.json').write_text(json.dumps(release, indent=2) + '\n')
    if mode in {'source', 'all'}:
        source = output / f'oci-oke-rdma-quickstart-v{version}.zip'
        write_zip(source, root, members(root, SOURCE_PATTERNS), f'oci-oke-rdma-quickstart-v{version}/')
        artifacts.append(source)
    checksums = output / 'SHA256SUMS'
    checksums.write_text(''.join(f'{digest(p)}  {p.name}\n' for p in artifacts))
    return artifacts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('source', 'orm', 'all'), default='all')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    for item in build(ROOT, args.output_dir, args.mode):
        print(item)
