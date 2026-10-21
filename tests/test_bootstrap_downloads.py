"""Exercise checksum-gated tool downloads with local fixtures and no network."""
from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('valid', [True, False])
def test_helm_download_is_verified_before_install(tmp_path, valid):
    control = tmp_path/'control'
    (control/'bootstrap').mkdir(parents=True)
    vendor = control/'vendor'
    vendor.mkdir()
    script = control/'bootstrap/fetch-vendor.sh'
    shutil.copyfile(ROOT/'control/bootstrap/fetch-vendor.sh', script)
    helm = b'#!/bin/sh\necho fixture\n'
    chart = b'fixture chart'
    (vendor/'dranet-v1.5.0.tgz').write_bytes(chart)
    (vendor/'lock.json').write_text(json.dumps({'files': {
        'helm': hashlib.sha256(helm if valid else b'different').hexdigest(),
        'dranet-v1.5.0.tgz': hashlib.sha256(chart).hexdigest(),
    }}))
    download = tmp_path/'helm.tar.gz'
    with tarfile.open(download, 'w:gz') as archive:
        item = tarfile.TarInfo('linux-amd64/helm')
        item.mode = 0o755
        item.size = len(helm)
        archive.addfile(item, io.BytesIO(helm))
    fakebin = tmp_path/'bin'
    fakebin.mkdir()
    curl = fakebin/'curl'
    curl.write_text('#!/usr/bin/env python3\nimport os, shutil, sys\nshutil.copyfile(os.environ["FIXTURE_ARCHIVE"], sys.argv[sys.argv.index("-o")+1])\n')
    curl.chmod(0o755)
    environment = dict(os.environ, PATH=str(fakebin)+os.pathsep+os.environ['PATH'],
                       FIXTURE_ARCHIVE=str(download), TMPDIR=str(tmp_path))
    result = subprocess.run(['bash', str(script)], env=environment, capture_output=True, text=True)
    if valid:
        assert result.returncode == 0, result.stderr
        assert (vendor/'helm').read_bytes() == helm
    else:
        assert result.returncode != 0
        assert 'Helm checksum mismatch' in result.stderr
        assert not (vendor/'helm').exists()
