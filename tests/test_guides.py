"""Executable guide checks for the first-use regressions reported by the user."""
import ast
import json
import os
from pathlib import Path
import re
import select
import subprocess
import time
import pytest
ROOT=Path(__file__).resolve().parents[1]


def test_guide_commands_parse_and_local_links_resolve():
    for name in ['README.md', *[str(p.relative_to(ROOT)) for p in (ROOT/'docs').glob('*.md')]]:
        path=ROOT/name;text=path.read_text()
        assert text.count('```')%2==0
        for block in re.findall(r'```bash\n(.*?)```',text,re.S):
            result=subprocess.run(['bash','-n'],input=block,text=True,capture_output=True)
            assert result.returncode==0,(name,result.stderr)
        for code in re.findall(r"<<'PY'\n(.*?)\nPY",text,re.S):ast.parse(code)
        for link in re.findall(r'\]\(([^)]+)\)',text):
            if not link.startswith(('http:','https:','#')):
                assert (path.parent/link.split('#')[0]).exists(),(name,link)


def test_registry_prompt_works_in_a_terminal_without_exposing_password(tmp_path):
    import pty
    text=(ROOT/'docs/IMAGE-PULL-SECRET.md').read_text()
    block=next(b for b in re.findall(r"python3 - <<'PY'\n(.*?)\nPY",text,re.S) if 'getpass' in b)
    # Replace kubectl with a local recorder; no cluster or credentials used.
    stub=tmp_path/'kubectl'
    stub.write_text('#!/usr/bin/env python3\nimport json,sys,pathlib\ns=json.load(sys.stdin)\nassert s["type"]=="kubernetes.io/dockerconfigjson"\npathlib.Path('+repr(str(tmp_path))+' , s["metadata"]["namespace"]+".json").write_text(json.dumps(s))\nprint("secret configured")\n')
    stub.chmod(0o755)
    script=tmp_path/'run.sh';script.write_text("python3 - <<'PY'\n"+block+"\nPY\n")
    pid,fd=pty.fork()
    if pid==0:
        os.environ['PATH']=str(tmp_path)+os.pathsep+os.environ['PATH']
        os.execv('/bin/bash',['bash',str(script)])
    output=b'';sent=0;deadline=time.monotonic()+10
    prompts=[b'Registry host (no https, e.g fra.ocir.io): ',b'Registry username (namespace/[domain/]username): ',b'Registry auth token/password (OCI auth token, not Console password): ']
    answers=[b'example.invalid\n',b'namespace/test-user\n',b'NOT_A_REAL_SECRET_987\n']
    try:
        while time.monotonic()<deadline:
            readable,_,_=select.select([fd],[],[],0.1)
            if readable:
                try:data=os.read(fd,4096)
                except OSError:break
                if not data:break
                output+=data
            if sent<len(prompts) and prompts[sent] in output:
                os.write(fd,answers[sent]);sent+=1
        result,status=os.waitpid(pid,os.WNOHANG)
        if not result:
            os.kill(pid,9);os.waitpid(pid,0);pytest.fail('Interactive guide timed out')
        assert os.waitstatus_to_exitcode(status)==0,output.decode()
        assert b'NOT_A_REAL_SECRET_987' not in output
        assert (tmp_path/'rdma-workloads.json').exists()
        assert (tmp_path/'oke-rdma-diagnostics.json').exists()
    finally:os.close(fd)


def test_documented_service_names_match_bootstrap():
    template=(ROOT/'terraform/operator/cloud-init.sh.tftpl').read_text()
    for name in ['OPERATOR-VM.md','TROUBLESHOOTING.md']:
        text=(ROOT/'docs'/name).read_text()
        for unit in re.findall(r'oke-rdma-control(?:-[a-z]+)?(?:\.service)?',text):
            if unit.endswith('.service'):assert unit in template
        assert 'status oke-rdma-control --' not in text


def test_embedded_image_recipe_matches_dockerfile():
    text=(ROOT/'docs/IMAGE-PULL-SECRET.md').read_text()
    recipe=re.search(r"<<'DOCKERFILE'\n(.*?)\nDOCKERFILE",text,re.S).group(1)
    assert recipe.strip()==(ROOT/'workloads/rdma-tools/Dockerfile').read_text().strip()
