from __future__ import annotations

from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
import yaml
import time
from . import dranet
from fastapi.staticfiles import StaticFiles

from .auth import require_token, require_metrics_token
from .config import load_settings, save_rdma_config
from .db import enqueue, get_job, init_db, list_jobs
from .jobs import validate_request
from .models import DiagnosticRunCreate, JobCreate, LoginCheck, RdmaConfigUpdate, ToolsImageUpdate, CapacityConnection
from .provider import get_provider, ProviderError
from .provider_base import error_detail
from .measurements import summarize
from .performance import dashboard
from .diagnostics import report_path
from .replacement import Replacement
from .recovery import active as active_recoveries, guard as recovery_guard, Recovery

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
init_db()

app = FastAPI(title="OKE RDMA Control Center", version="2.10.0", docs_url=None, redoc_url=None)


@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": error_detail(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    fields = []
    for error in exc.errors():
        field = str(error['loc'][-1])
        message = ('Enter a Resource Manager stack OCID starting with ocid1.ormstack.' if field == 'stack_id'
                   else f"{field.replace('_', ' ').capitalize()}: {error['msg']}")
        fields.append({'field': field, 'message': message})
    return JSONResponse(status_code=422, content={'detail': {'code': 'invalid_input', 'message': 'Check the highlighted fields.', 'fields': fields}})


@app.exception_handler(ValueError)
async def input_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        load_settings()
        init_db()
        return {"status": "ready", "mode": load_settings().mode}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not-ready", "error": str(exc)})


@app.post("/api/v1/session", response_model=LoginCheck, dependencies=[Depends(require_token)])
def session_check():
    return LoginCheck()


@app.get("/api/v1/environment", dependencies=[Depends(require_token)])
def environment():
    settings = load_settings()
    try:
        data = get_provider(settings).environment()
        data['recoveries'] = active_recoveries()
        data['replacement'] = Replacement(get_provider(settings)).operation()
        data['measurement_status'] = summarize(list_jobs(100), data, settings)
        data['performance'] = dashboard(list_jobs(100), data, settings)
        return data
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/preflight", dependencies=[Depends(require_token)])
def preflight():
    settings = load_settings()
    try:
        return get_provider(settings).preflight()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/rdma/discovery", dependencies=[Depends(require_token)])
def rdma_discovery():
    settings = load_settings()
    try:
        return get_provider(settings).rdma_discovery()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/nodes", dependencies=[Depends(require_token)])
def node_diagnostics():
    settings = load_settings()
    try:
        return get_provider(settings).node_diagnostics()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/diagnostics/profiles", dependencies=[Depends(require_token)])
def diagnostic_profiles():
    settings = load_settings()
    try:
        return get_provider(settings).diagnostic_catalog()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/v1/diagnostics/image", dependencies=[Depends(require_token)])
def update_diagnostic_image(req: ToolsImageUpdate):
    if any(j["state"] in {"QUEUED", "RUNNING"} for j in list_jobs()):
        raise HTTPException(status_code=409, detail="Wait for the active operation before editing settings.")
    settings = load_settings()
    get_provider(settings).capacity_service().guard('settings')
    values = settings.rdma_config()
    values["rdma_tools_image"] = req.image
    try:
        saved = save_rdma_config(values)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to persist operations tools image: {exc}") from exc
    return {"saved": True, "image": saved.rdma_tools_image}


@app.post("/api/v1/diagnostics/runs", dependencies=[Depends(require_token)])
def create_diagnostic_run(req: DiagnosticRunCreate):
    settings = load_settings()
    try:
        recovery_guard("node_diagnostic")
        validate_request(settings, "node_diagnostic", req.confirmation)
        get_provider(settings).validate_node_diagnostic(req.node_name, req.profile)
    except (ValueError, ProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return enqueue(
        "node_diagnostic",
        req.confirmation,
        {"node_name": req.node_name, "profile": req.profile},
    )


@app.get("/api/v1/diagnostics/reports/{job_id}/{report_format}", dependencies=[Depends(require_token)])
def diagnostic_report(job_id: str, report_format: str):
    extension = "json" if report_format == "json" else "txt" if report_format == "text" else ""
    if not extension:
        raise HTTPException(status_code=404, detail="Unsupported diagnostic report format")
    try:
        path = report_path(job_id, extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Diagnostic report is not available")
    media_type = "application/json" if extension == "json" else "text/plain; charset=utf-8"
    node, profile = "host", "diagnostic"
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"{node}-{profile}-{job_id}.{extension}",
    )


@app.get("/api/v1/rdma/config", dependencies=[Depends(require_token)])
def rdma_config():
    settings = load_settings()
    try:
        locks = get_provider(settings).configuration_status()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "config": settings.rdma_config(),
        "configured": settings.rdma_configured,
        "locks": locks,
        "documentation": {
            "ubuntu_node_packages": "https://docs.oracle.com/en-us/iaas/Content/ContEng/Concepts/contengCompatibilitymatrix_topic.htm",
            "ubuntu_custom_workers": "https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcreatingubuntubasedworkernodes.htm",
        },
    }


@app.put("/api/v1/rdma/config", dependencies=[Depends(require_token)])
def update_rdma_config(req: RdmaConfigUpdate):
    if any(j["state"] in {"QUEUED", "RUNNING"} for j in list_jobs()):
        raise HTTPException(status_code=409, detail="Wait for the active operation before editing settings.")
    settings = load_settings()
    provider = get_provider(settings)
    try:
        locks = provider.configuration_status()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    old = settings.rdma_config()
    try:
        provider.capacity_service().guard('settings')
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    new = req.model_dump()
    try:
        provider.validate_rdma_selection(new)
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    immutable = {"rdma_pf_name", "rdma_mtu", "rdma_child_cidr"}
    if locks.get("installed") and any(old.get(k) != new.get(k) for k in immutable):
        raise HTTPException(status_code=409, detail="PF and child range are locked while Dranet is installed. Plan driver maintenance with workloads stopped before changing them. Image and test settings remain editable.")

    try:
        saved = save_rdma_config(new)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to persist RDMA configuration: {exc}") from exc
    return {
        "saved": True,
        "configured": saved.rdma_configured,
        "config": saved.rdma_config(),
        "locks": locks,
    }


@app.get("/api/v1/jobs", dependencies=[Depends(require_token)])
def jobs():
    return {"items": list_jobs()}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
def job(job_id: str):
    try:
        return get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.get("/api/v1/capacity", dependencies=[Depends(require_token)])
def capacity_status():
    return get_provider(load_settings()).capacity_service().status()


@app.get('/api/v1/capacity/readiness', dependencies=[Depends(require_token)])
def capacity_readiness(target: int = Query(ge=0, le=64)):
    return get_provider(load_settings()).capacity_service().readiness(target)


@app.put("/api/v1/capacity/connection", dependencies=[Depends(require_token)])
def capacity_connection(req: CapacityConnection):
    settings = load_settings()
    if req.confirmation.strip() != settings.environment_name:
        raise HTTPException(status_code=400, detail={'code': 'confirmation_mismatch', 'field': 'confirmation', 'message': f'Type {settings.environment_name} to confirm this environment.', 'next_action': 'This field confirms the existing environment name; it does not rename it.'})
    try:
        return get_provider(settings).capacity_service().connect(req.stack_id)
    except (ProviderError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=error_detail(exc)) from exc


@app.get("/api/v1/reset-plan", dependencies=[Depends(require_token)])
def reset_plan():
    try:
        return get_provider(load_settings()).reset_plan()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/v1/jobs", dependencies=[Depends(require_token)])
def create_job(req: JobCreate):
    settings = load_settings()
    try:
        validate_request(settings, req.action, req.confirmation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider = get_provider(settings)
    try:
        recovery_guard(req.action)
        provider.capacity_service().guard(req.action)
        if req.action.startswith("replacement_"):
            Replacement(provider).authorize()
        if req.action == "replacement_run":
            Replacement(provider).read_preview(req.replacement_id)
        if req.action.startswith("recovery_"):
            Recovery(provider).authorize()
        if req.action == "recovery_run":
            Recovery(provider).read_preview(req.recovery_preview_id)
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if req.action.startswith('replacement_') and req.action != 'replacement_preview' and not req.replacement_id:
        raise HTTPException(status_code=422, detail='Review the current replacement before continuing.')
    if req.action == 'replacement_preview' and not req.node_name:
        raise HTTPException(status_code=422, detail='Select a worker to preview replacement.')
    if req.action == 'capacity_plan' and req.target_node_count is None:
        raise HTTPException(status_code=422, detail='Choose a target node count between 0 and 64.')
    if req.action.startswith('capacity_') and req.action != 'capacity_plan' and not req.capacity_operation_id:
        raise HTTPException(status_code=422, detail='Refresh the capacity operation before continuing.')
    if req.action == 'reset_rdma':
        try:
            plan = get_provider(settings).reset_plan()
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not plan['can_reset'] or not req.reset_plan_id or req.reset_plan_id != plan['plan_id']:
            raise HTTPException(status_code=409, detail={'message': 'Review a current reset preview before confirming.', 'blockers': plan['blockers']})
    return enqueue(req.action, req.confirmation, {"node_name": req.node_name, "replacement_id": req.replacement_id, "recovery_preview_id": req.recovery_preview_id, "recovery_attempt_id": req.recovery_attempt_id, "target_node_count": req.target_node_count, "capacity_operation_id": req.capacity_operation_id, "reset_plan_id": req.reset_plan_id, "performance_profile": req.performance_profile, "server_node": req.server_node, "client_node": req.client_node, "settings": settings.rdma_config()})


@app.get("/api/v1/operations/events", dependencies=[Depends(require_token)])
def recent_events():
    try:
        return get_provider(load_settings()).events()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/exports/{kind}", dependencies=[Depends(require_token)])
def export_configuration(kind: str):
    settings = load_settings()
    if kind == "workload":
        if not settings.rdma_tools_image:
            raise HTTPException(status_code=409, detail="Save an immutable tools image before exporting a workload.")
        docs = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": settings.workload_namespace}},
                dranet.DEVICE_CLASS, *dranet.workload(settings, "rdma-example")]
        return Response(yaml.safe_dump_all(docs, sort_keys=False), media_type="application/yaml",
                        headers={"Content-Disposition": 'attachment; filename="rdma-workload.yaml"'})
    if kind == "values":
        return Response(yaml.safe_dump(dranet.values(settings), sort_keys=False), media_type="application/yaml",
                        headers={"Content-Disposition": 'attachment; filename="dranet-values.yaml"'})
    if kind == 'support':
        from .support import build_bundle
        provider = get_provider(settings)
        data = provider.environment()
        data['replacement'] = Replacement(provider).operation()
        return Response(build_bundle(data, list_jobs(100), settings), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="oke-hpc-support.zip"'})
    if kind == "snapshot":
        try:
            return {"schema_version": "2.0", "exported_at": time.time(),
                    "environment": get_provider(settings).environment(), "jobs": list_jobs()}
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Unknown export")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


from .monitoring import Monitoring, MonitoringConfig, metrics as monitoring_metrics

@app.get('/api/v1/monitoring', dependencies=[Depends(require_token)])
def monitoring_status():
    return Monitoring(get_provider(load_settings())).status()

@app.put('/api/v1/monitoring', dependencies=[Depends(require_token)])
def monitoring_config(req: MonitoringConfig):
    return Monitoring(get_provider(load_settings())).save_config(req)

@app.get('/metrics', dependencies=[Depends(require_metrics_token)])
def prometheus_metrics():
    return Response(monitoring_metrics(get_provider(load_settings())), media_type='text/plain; version=0.0.4')
