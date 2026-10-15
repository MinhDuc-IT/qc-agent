"""An executable proposal is never a human-approved binding."""
from typing import Literal
from pydantic import Field
from .contracts import Contract,ExecutableCase
from .workspace import AssertionMapping


class AutomationDraft(Contract):
    case_id: str
    disposition: Literal['needs_review','needs_setup','manual']
    reason: str
    prerequisites: list[str] = Field(default_factory=list,max_length=20)
    executable: ExecutableCase | None = None
    expected_result_mapping: list[AssertionMapping] = Field(default_factory=list,max_length=100)
