"""Versioned worker-neutral artifacts; pytest is the first implementation."""
from typing import Literal
from pydantic import Field
from .contracts import Contract


class ExecutionProfile(Contract):
    schema_version: Literal['1.0']='1.0'
    runner: Literal['pytest']='pytest'
    root: str='.'
    test_root: str='tests'
    python_version: str | None=None
    dependency_files: list[str]=Field(default_factory=list)
    fixture_files: list[str]=Field(default_factory=list)
    required_environment: list[str]=Field(default_factory=list)
    services: list[Literal['postgres','smtp']]=Field(default_factory=list)
    test_environment: dict[str,str]=Field(default_factory=dict)
    migrations: bool=False
    dockerfile: str | None=None
    container_root: str='/workspace'
    evidence_paths: list[str]=Field(default_factory=list)
    blockers: list[str]=Field(default_factory=list)


class ArtifactMapping(Contract):
    expected_key: str | None=None
    assertion_sources: list[str]=Field(default_factory=list,max_length=20)
    design_step: int=Field(ge=0)
    expected_result: int=Field(ge=0)
    assertion_lines: list[int]=Field(min_length=1,max_length=20)
    explanation: str=Field(min_length=1,max_length=2000)


class PytestDraft(Contract):
    disposition: Literal['needs_review','needs_setup','manual']
    reason: str
    code: str | None=Field(default=None,max_length=64000)
    prerequisites: list[str]=Field(default_factory=list)
    mapping: list[ArtifactMapping]=Field(default_factory=list)


class ReviewedAssertion(Contract):
    expected_key: str=Field(min_length=1)
    assertion_sources: list[str]=Field(min_length=1,max_length=20)
    explanation: str=Field(min_length=1,max_length=2000)


class ArtifactReview(Contract):
    mapping: list[ReviewedAssertion]
    issues: list[str]


class TestArtifact(Contract):
    path: str
    content: str
    sha256: str


class WorkerArtifactBinding(Contract):
    schema_version: Literal['1.0']='1.0'
    case_id: str
    case_revision: int
    design_sha256: str
    worker: Literal['python-pytest-generated']='python-pytest-generated'
    profile: ExecutionProfile
    artifact: TestArtifact
    test_selector: str
    mapping: list[ArtifactMapping]
    validation_issues: list[str]=Field(default_factory=list)
    preflight: dict=Field(default_factory=dict)
    state: Literal['proposed','validated']='proposed'
    reviewer: str | None=None
