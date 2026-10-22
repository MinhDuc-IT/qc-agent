"""Immutable reviewed intent passed to QC independently of automation scripts."""
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract
from .workspace import DesignCase
from .worker_artifacts import WorkerArtifactBinding


class ReviewedCasePacket(Contract):
    schema_version:Literal['reviewed-case-1']='reviewed-case-1'
    case:DesignCase
    design_sha256:str
    source_revision:str
    worker:Literal['python-pytest-agent','hercules']
    worker_capability:Literal['functional.api','functional.e2e']='functional.e2e'
    profile_root:str|None=None
    module_paths:list[str]=Field(default_factory=list)
    requirements:list[str]=Field(default_factory=list)
    cached_artifact:WorkerArtifactBinding|None=None

    @model_validator(mode='after')
    def reviewed_identity(self):
        from ..application.workspace import design_digest
        if self.case.status!='approved' or design_digest(self.case)!=self.design_sha256:
            raise ValueError('Reviewed case identity/hash mismatch')
        if self.worker=='python-pytest-agent' and self.profile_root is None:
            raise ValueError('Python worker requires an execution profile root')
        if self.cached_artifact:
            b=self.cached_artifact
            if b.case_id!=self.case.id or b.design_sha256!=self.design_sha256 or b.case_revision!=self.case.revision:
                raise ValueError('Cached automation differs from reviewed intent')
        return self
