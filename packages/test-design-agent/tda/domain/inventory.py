"""Static inventory and explicit traceability, never runtime code coverage."""
from typing import Literal
from pydantic import Field
from .contracts import Contract


class InventoryItem(Contract):
    id: str
    kind: Literal['api','ui_route','logic','existing_test','branch']
    source_path: str
    source_sha256: str
    symbol: str
    line: int
    end_line: int
    excerpt: str
    method: str = ''
    route: str = ''
    parent_id: str = ''
    outcome: str = ''
    extractor: str = 'ast'
    conditional: bool = False


class ProjectInventory(Contract):
    schema_version: Literal['1.0'] = '1.0'
    completeness: Literal['repository_scan','selected_sources','not_scanned'] = 'not_scanned'
    files_scanned: int = 0
    items: list[InventoryItem] = Field(default_factory=list)
    unknown: list[dict] = Field(default_factory=list)
    exclusions: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
