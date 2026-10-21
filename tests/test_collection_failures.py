import json
import subprocess
import pytest
from app.collection_failures import finding
from app.config import Settings
from app.provider import LiveProvider


@pytest.mark.parametrize('message,code',[
    ('Failed to pull image: Unauthorized', 'registry_authentication_failed'),
    ('Failed to pull image: Forbidden', 'registry_access_denied'),
    ('Failed to pull image: manifest unknown', 'registry_image_unavailable'),
    ('Failed to pull image: dial tcp: i/o timeout', 'registry_connection_failed'),
    ('Failed to pull image: unknown problem', 'diagnostic_image_pull_failed'),
])
def test_pull_cause_precedes_deadline(message,code):
    pod={'status':{'reason':'DeadlineExceeded','containerStatuses':[{'state':{'terminated':{'reason':'ContainerStatusUnknown','exitCode':137}}}]}}
    f=finding(pod,[{'reason':'Failed','message':message}])
    assert f['code']==code and f['next_action']


def test_unknown_exit137_is_not_oom():
    assert finding({'status':{'reason':'DeadlineExceeded','exitCode':137}},[])['code']=='diagnostic_deadline_exceeded'
    assert finding({'status':{'reason':'OOMKilled'}},[])['code']=='diagnostic_out_of_memory'


def test_wait_stops_at_image_pull_error(monkeypatch):
    p=LiveProvider(Settings())
    pod={'status':{'phase':'Pending','containerStatuses':[{'state':{'waiting':{'reason':'ImagePullBackOff'}}}]}}
    monkeypatch.setattr(p,'kubectl',lambda *a,**k:subprocess.CompletedProcess([],0,json.dumps(pod),''))
    monkeypatch.setattr('app.provider_base.time.sleep',lambda *a:pytest.fail('Should return immediately'))
    assert p._wait_for_diagnostic_pod('ns','pod',10)==pod


def test_secret_retrieval_classification():
    f=finding({},[{'reason':'FailedToRetrieveImagePullSecret','message':'ocir-pull unavailable'}])
    assert f['code']=='registry_secret_unavailable'
