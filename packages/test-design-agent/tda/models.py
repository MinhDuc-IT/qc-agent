from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Oracle(StrictModel):
    status: int = Field(ge=100, le=599)
    json_equals: dict[str, Any] = Field(default_factory=dict)
    source_ref: str = Field(min_length=1)


class Scenario(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    query: dict[str, str | int | float | bool] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    oracle: Oracle
    priority: Literal["P0", "P1", "P2"] = "P1"
    risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def relative_path(self):
        if not self.path.startswith("/") or self.path.startswith("//") or any(
            char in self.path for char in ("{", "?", "#", "\\", "\r", "\n")
        ):
            raise ValueError("path must be a concrete relative API path")
        return self


class Requirement(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    scenarios: list[Scenario] = Field(default_factory=list)


class ProjectInput(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    project: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        requirements = [r.id for r in self.requirements]
        scenarios = [s.id for r in self.requirements for s in r.scenarios]
        if len(requirements) != len(set(requirements)) or len(scenarios) != len(set(scenarios)):
            raise ValueError("requirement and scenario IDs must be unique")
        return self
