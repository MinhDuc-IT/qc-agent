from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..domain.contracts import SourceBundle, WorkflowConfig

T = TypeVar("T", bound=BaseModel)


class AgentFailure(RuntimeError):
    """A structured model response could not be obtained within the call budget."""
    def __init__(self,message,stage=None,error_kind='provider',validation_errors=None):
        super().__init__(message)
        self.stage=stage
        self.error_kind=error_kind
        self.validation_errors=validation_errors or []


class StructuredAgent(Protocol):
    def propose(self, stage: str, instructions: str, context: dict[str, Any], output: type[T]) -> T: ...


class SourceReader(Protocol):
    def collect(self, config: WorkflowConfig, config_directory: Path) -> SourceBundle: ...


class TestExecutor(Protocol):
    def execute(self, cases: list[dict], base_url: str, allow_mutations: bool) -> dict: ...


class ArtifactStore(Protocol):
    def write(self, name: str, data: Any) -> None: ...
    def event(self, stage: str, status: str, details: dict[str, Any]) -> None: ...


class BaselinePublisher(Protocol):
    def publish_draft(self, config: WorkflowConfig, payload: dict[str, Any]) -> Path: ...
