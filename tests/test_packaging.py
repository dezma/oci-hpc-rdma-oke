from pathlib import Path
import re
import yaml
ROOT=Path(__file__).parents[1]


def test_ttl_resource_manager_contract():
    schema=yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())
    for key in ['bastion_ssh_session_ttl_minutes','bastion_port_forwarding_session_ttl_minutes']:
        field=schema['variables'][key]
        assert (field['default'],field['minimum'],field['maximum'])==(120,30,180)
    out=(ROOT/'terraform/outputs.tf').read_text()
    assert '--session-ttl 3600' not in out
    assert out.count('--session-ttl ${60 * var.bastion_port_forwarding_session_ttl_minutes}')==2
    assert out.count('--session-ttl ${60 * var.bastion_ssh_session_ttl_minutes}')==1
    assert 'max(var.bastion_ssh_session_ttl_minutes, var.bastion_port_forwarding_session_ttl_minutes)' in (ROOT/'terraform/operator.tf').read_text()


def test_frontend_no_tab_switch_on_job():
    script=(ROOT/'control/web/app.js').read_text()
    assert "setPage('jobs')" not in script
    assert 'data-result' in script and 'rdma_validation' in script


def test_cloud_init_interpolation_names():
    text=(ROOT/'terraform/operator/cloud-init.sh.tftpl').read_text()
    variables=set(re.findall(r'(?<!\$)\$\{([^}]+)\}',text))
    mapping=(ROOT/'terraform/operator.tf').read_text()
    assert all(re.search(r'\b'+re.escape(v)+r'\s*=',mapping) for v in variables)


def test_bootstrap_shared_mode_and_dranet_scope():
    assert 'options ib_core netns_mode=1' in (ROOT/'terraform/operator/managed-ubuntu-hpc.yaml.tftpl').read_text()
    assert re.search(r'key\s*=\s*"oke-rdma-mode"\s+value\s*=\s*"dranet"', (ROOT/'terraform/oke.tf').read_text())


def _packager():
    import importlib.util
    spec = importlib.util.spec_from_file_location('release_packager', ROOT/'provisioning/package-release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_archive_excludes_local_state_and_generated_inputs(tmp_path):
    import zipfile
    packager = _packager()
    safe = ['README.md', 'docs/oci-hpc-arc.png', 'VERSION', 'control/app/main.py', 'control/vendor/lock.json',
            'terraform/main.tf', 'terraform/.terraform.lock.hcl', '.github/workflows/ci.yml']
    private = ['pytest-of-root/pytest-1/control.db', '.test-state/rdma.json', '.demo/control.db',
               'terraform/terraform.tfvars', 'terraform/prod.auto.tfvars', 'terraform/override.tf',
               'terraform/private_override.tf', 'terraform/terraform.tfstate', '.env',
               'control/vendor/helm', 'control/vendor/dranet-v1.5.0.tgz',
               'terraform/operator/payload/oke-rdma-control.tar.gz',
               'control/app/__pycache__/main.pyc', 'admin.token', 'node_modules/index.js']
    for name in safe + private:
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('synthetic fixture')
    selected = packager.members(tmp_path, packager.SOURCE_PATTERNS)
    output = tmp_path/'source.zip'
    packager.write_zip(output, tmp_path, selected)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == set(safe)


def test_orm_archive_excludes_terraform_overrides_and_state(tmp_path):
    packager = _packager()
    for name in ['main.tf', 'schema.yaml', '.terraform.lock.hcl', 'operator/cloud-init.sh.tftpl',
                 'operator/payload/oke-rdma-control.tar.gz', 'terraform.tfstate', 'override.tf',
                 'secrets_override.tf', 'terraform.tfvars', '.terraform/providers/plugin']:
        p = tmp_path/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('synthetic fixture')
    selected = {p.relative_to(tmp_path).as_posix() for p in packager.members(tmp_path, packager.ORM_PATTERNS)}
    assert selected == {'main.tf', 'schema.yaml', '.terraform.lock.hcl', 'operator/cloud-init.sh.tftpl',
                        'operator/payload/oke-rdma-control.tar.gz'}


def test_source_packager_rejects_symlinks(tmp_path):
    import pytest
    packager = _packager()
    (tmp_path/'README.md').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='Symlinks'):
        packager.members(tmp_path, packager.SOURCE_PATTERNS)


def test_schema_variables_and_groups_match_terraform():
    declared = set(re.findall(r'variable "([^"]+)"', (ROOT/'terraform/variables.tf').read_text()))
    schema = yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())
    assert set(schema['variables']) <= declared
    grouped = {key for group in schema['variableGroups'] for key in group['variables']}
    assert grouped <= declared
    assert 'oci_containerengine_addon' not in (ROOT/'terraform/oke.tf').read_text()


def test_documentation_links_resolve():
    for path in [ROOT/'README.md', ROOT/'CONTRIBUTING.md', *list((ROOT/'docs').glob('*.md'))]:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            assert (path.parent/target.split('#')[0]).exists(), (path, target)


def test_release_versions_align():
    import json
    version = (ROOT/'VERSION').read_text().strip()
    assert json.loads((ROOT/'package.json').read_text())['version'] == version
    assert json.loads((ROOT/'package-lock.json').read_text())['version'] == version
    schema = yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())
    assert schema['version'] == schema['packageVersion'] == version
    assert 'v'+version in (ROOT/'control/web/index.html').read_text()


def test_orm_rejects_oversized_configuration(tmp_path):
    import pytest
    packager = _packager()
    payload = tmp_path/'payload.tar.gz'
    with payload.open('wb') as stream:
        stream.truncate(packager.MAX_ORM_BYTES + 1)
    output = tmp_path/'orm.zip'
    with pytest.raises(ValueError, match='before compression'):
        packager.write_orm_zip(output, tmp_path, [payload])
    assert not output.exists()


def test_orm_rejects_oversized_zip_without_leaving_release(tmp_path, monkeypatch):
    import pytest
    packager = _packager()
    monkeypatch.setattr(packager, 'MAX_ORM_BYTES', 32)
    source = tmp_path/'main.tf'
    source.write_text('x')
    output = tmp_path/'orm.zip'
    with pytest.raises(ValueError, match='ORM ZIP exceeds'):
        packager.write_orm_zip(output, tmp_path, [source])
    assert not output.exists() and not output.with_suffix('.zip.partial').exists()


def test_payload_rejects_embedded_helm(tmp_path):
    import io
    import tarfile
    import pytest
    packager = _packager()
    payload = tmp_path/'terraform/operator/payload/oke-rdma-control.tar.gz'
    payload.parent.mkdir(parents=True)
    with tarfile.open(payload, 'w:gz') as archive:
        item = tarfile.TarInfo('vendor/helm')
        item.size = 1
        archive.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='downloaded at bootstrap'):
        packager.verify_payload(tmp_path)


def test_runtime_dependencies_are_pinned_and_hashed():
    runtime = (ROOT/'control/runtime-requirements.lock').read_text()
    entries = [line for line in runtime.splitlines() if line and not line.startswith('#')]
    assert all(re.fullmatch(r'[A-Za-z0-9_.-]+==[^ ]+ --hash=sha256:[a-f0-9]{64}', line) for line in entries)
    pins = {line.split(' --hash=')[0].lower().replace('_', '-') for line in entries}
    declared = (ROOT/'control/requirements.lock').read_text().splitlines()
    assert all(line.lower().replace('_', '-') in pins for line in declared if line and not line.startswith('#'))


def test_rendered_cloud_init_shell_syntax(tmp_path):
    import subprocess
    source = (ROOT/'terraform/operator/cloud-init.sh.tftpl').read_text()
    rendered = re.sub(r'(?<!\$)\$\{[^}]+\}', 'example', source).replace('$${', '${')
    script = tmp_path/'bootstrap.sh'
    script.write_text(rendered)
    subprocess.run(['bash', '-n', str(script)], check=True)
    assert rendered.count('--require-hashes') == 2
    assert 'runtime-requirements.lock' in rendered


def test_capacity_terraform_ownership_and_safe_lifecycle():
    import hcl2
    from hcl2.utils import SerializationOptions
    pool=hcl2.loads((ROOT/'terraform/oke.tf').read_text(),serialization_options=SerializationOptions(strip_string_quotes=True))['resource']
    config=next(item['oci_containerengine_node_pool']['rdma'] for item in pool if 'oci_containerengine_node_pool' in item)
    assert config['node_config_details'][0]['size']=='${var.node_count}'
    assert 'lifecycle' not in config
    assert config['node_pool_cycling_details'][0]['is_node_cycling_enabled'] is False
    eviction=config['node_eviction_node_pool_settings'][0]
    assert eviction['is_force_delete_after_grace_duration'] is False and eviction['eviction_grace_duration']=='PT60M'
    schema=yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())
    assert schema['variables']['capacity_stack_ocid']['default']==''
    iam=(ROOT/'terraform/iam.tf').read_text()
    import re
    for scope in ['compartment','tenancy']:
        variable=f'create_capacity_{scope}_policy'
        assert schema['variables'][variable]['default'] is True
        assert schema['variables'][variable]['type']=='boolean'
        assert re.search(r'count\s*= var.capacity_stack_ocid != "" && var.'+variable+r' \? 1 : 0',iam)
        definitions=hcl2.loads((ROOT/'terraform/variables.tf').read_text(),serialization_options=SerializationOptions(strip_string_quotes=True))['variable']
        assert next(x[variable] for x in definitions if variable in x)['default'] is True
    assert 'target.nodepool.id' in iam and "target.job.operation = 'PLAN'" in iam and "target.job.operation = 'APPLY'" in iam


def test_capacity_policy_renderer_is_explicit_and_scoped(tmp_path):
    import subprocess,sys,json
    args=[sys.executable,str(ROOT/'provisioning/capacity-policy.py'),
          '--stack-id','ocid1.ormstack.oc1.region.example','--node-pool-id','ocid1.nodepool.oc1.region.example',
          '--dynamic-group-id','ocid1.dynamicgroup.oc1.example','--compartment-id','ocid1.compartment.oc1.example',
          '--tenancy-id','ocid1.tenancy.oc1.example','--output-dir',str(tmp_path/'policy')]
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    request=json.loads((tmp_path/'policy/compartment.json').read_text())
    assert request['compartmentId']=='ocid1.compartment.oc1.example'
    joined='\n'.join(request['statements'])
    assert "target.stack.id = 'ocid1.ormstack.oc1.region.example'" in joined
    assert "target.nodepool.id = 'ocid1.nodepool.oc1.region.example'" in joined
    assert 'ORM_STACK_DELETE' not in joined and 'manage all-resources' not in joined
    assert subprocess.run(args,capture_output=True).returncode!=0


def test_all_iam_resources_are_optional_and_existing_addresses_are_mapped():
    import hcl2
    from hcl2.utils import SerializationOptions
    options=SerializationOptions(strip_string_quotes=True)
    parsed=[hcl2.loads(p.read_text(),serialization_options=options) for p in (ROOT/'terraform').glob('*.tf')]
    resources={f'{kind}.{name}':value for doc in parsed for block in doc.get('resource',[]) for kind,items in block.items() for name,value in items.items()}
    expected={'oci_identity_policy.operator':'create_operator_policy',
              'oci_identity_policy.nodepool_compute_cluster':'create_nodepool_compute_cluster_policy',
              'oci_identity_policy.capacity_compartment':'create_capacity_compartment_policy',
              'oci_identity_policy.capacity_tenancy':'create_capacity_tenancy_policy',
              'oci_identity_dynamic_group.operator':'create_operator_dynamic_group'}
    actual={k for k in resources if k.startswith(('oci_identity_policy.','oci_identity_dynamic_group.'))}
    assert actual==set(expected)
    variables={name:spec for doc in parsed for block in doc.get('variable',[]) for name,spec in block.items()}
    schema=yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())
    for address,flag in expected.items():
        assert f'var.{flag}' in resources[address]['count']
        assert variables[flag]['default'] is True
        assert schema['variables'][flag]['default'] is True
        assert sum(flag in g['variables'] for g in schema['variableGroups'])==1
    moves={m['from']:m['to'] for doc in parsed for m in doc.get('moved',[])}
    for address in ['oci_identity_dynamic_group.operator','oci_identity_policy.operator','oci_identity_policy.nodepool_compute_cluster']:
        assert moves['${'+address+'}']=='${'+address+'[0]}'
    iam=(ROOT/'terraform/iam.tf').read_text()
    assert '"dynamic-group id ${var.existing_operator_dynamic_group_ocid}"' in iam
    assert 'oci_identity_dynamic_group.operator.id' not in (ROOT/'terraform/outputs.tf').read_text()
    assert not any('oci_identity_dynamic_group' in block for doc in parsed for block in doc.get('data',[]))
    assert 'var.create_operator_dynamic_group ? var.existing_operator_dynamic_group_ocid == "" : var.existing_operator_dynamic_group_ocid != ""' in (ROOT/'terraform/locals.tf').read_text()


def test_worker_count_resource_manager_contract():
    field=yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())['variables']['node_count']
    assert (field['default'],field['minimum'],field['maximum'],field['multipleOf'])==(2,0,64,1)
    text=(ROOT/'terraform/variables.tf').read_text().split('variable "node_count" {')[1].split('\nvariable ',1)[0]
    assert 'default     = 2' in text
    assert 'var.node_count >= 0' in text and 'floor(var.node_count) == var.node_count' in text


def test_orm_operator_image_requires_explicit_named_selection():
    field=yaml.safe_load((ROOT/'terraform/schema.yaml').read_text())['variables']['operator_image_ocid']
    assert field['type']=='oci:core:image:id'
    assert field['required'] is True
    assert field['title']=='Approved operator image override'
