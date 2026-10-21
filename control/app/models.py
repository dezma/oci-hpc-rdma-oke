from __future__ import annotations
import ipaddress
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Action = Literal['monitoring_sweep', 'monitoring_baseline', 'replacement_preview', 'replacement_run', 'replacement_resume', 'replacement_close', 'recovery_restart_assess', 'recovery_assess', 'recovery_run', 'recovery_finish', 'preflight', 'check_hosts', 'install_dranet', 'rdma_validation', 'cleanup_validation', 'reset_rdma', 'capacity_plan', 'capacity_apply', 'capacity_discard', 'capacity_resume', 'capacity_close']

class JobCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Action
    confirmation: str = Field(default='', max_length=128)
    target_node_count: int | None = Field(default=None, ge=0, le=64, strict=True)
    capacity_operation_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    node_name: str = Field(default='', max_length=253, pattern=r'^[A-Za-z0-9._-]*$')
    replacement_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')
    recovery_preview_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')
    recovery_attempt_id: str = Field(default='', pattern=r'^(?:[a-f0-9-]{36})?$')
    reset_plan_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{64})?$')
    performance_profile: Literal['quick', 'sweep', 'loaded', 'numa', 'mpi', 'matrix'] = 'quick'
    server_node: str = Field(default='', max_length=253, pattern=r'^[A-Za-z0-9._-]*$')
    client_node: str = Field(default='', max_length=253, pattern=r'^[A-Za-z0-9._-]*$')

class DiagnosticRunCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_name: str = Field(min_length=1, max_length=253, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
    profile: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9-]+$')
    confirmation: str = Field(default='', max_length=128)

class ToolsImageUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: str = Field(min_length=1, max_length=512)
    @field_validator('image')
    @classmethod
    def validate_image(cls, value):
        value = value.strip()
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-fA-F]{64}', value):
            raise ValueError('Use repository@sha256:<64-hex-digest>')
        return value

class LoginCheck(BaseModel):
    ok: bool = True

class RdmaConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rdma_pf_name: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')
    rdma_mtu: Literal[4220] = 4220
    rdma_child_cidr: str = '10.208.0.0/12'
    rdma_tools_image: str = Field(default='', max_length=512)
    rdma_image_pull_secret: str = Field(default='', max_length=253, pattern=r'^[a-z0-9.-]*$')
    rdma_tos: int = Field(default=41, ge=0, le=255)
    @field_validator('rdma_child_cidr')
    @classmethod
    def validate_cidr(cls, value):
        net = ipaddress.ip_network(value, strict=True)
        if net.version != 4 or not 8 <= net.prefixlen <= 30:
            raise ValueError('Use an IPv4 CIDR with a prefix from /8 to /30')
        for reserved in ['0.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/4', '240.0.0.0/4', '10.224.0.0/12']:
            if net.overlaps(ipaddress.ip_network(reserved)):
                raise ValueError(f'Child CIDR overlaps reserved or parent RDMA range {reserved}')
        return str(net)
    @field_validator('rdma_tools_image')
    @classmethod
    def validate_image(cls, value):
        return ToolsImageUpdate.validate_image(value) if value.strip() else ''


class CapacityConnection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stack_id: str = Field(max_length=512, pattern=r'^ocid1[.]ormstack[.][A-Za-z0-9._-]+$')
    confirmation: str = Field(max_length=128)
