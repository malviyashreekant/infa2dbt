"""Tool-neutral intermediate representation of an Informatica mapping.

The parser fills these models; generators only ever read them. Nothing in
here knows about XML or about dbt.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    CONVERTED = "CONVERTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNSUPPORTED = "UNSUPPORTED"


class Port(BaseModel):
    name: str
    datatype: str = "string"
    precision: int = 0
    scale: int = 0
    porttype: str = "INPUT/OUTPUT"  # INPUT, OUTPUT, INPUT/OUTPUT, VARIABLE, LOOKUP, RETURN
    expression: Optional[str] = None
    group: Optional[str] = None  # ROUTER group, UPDATE strategy etc.
    key: bool = False
    default: Optional[str] = None
    expr_type: str = "GENERAL"
    sort_dir: Optional[str] = None  # Sorter: ASCENDING / DESCENDING
    sort_key: bool = False
    ref_field: Optional[str] = None  # Router/Union output port -> input port it mirrors
    not_null: bool = False

    @property
    def is_variable(self) -> bool:
        return "VARIABLE" in self.porttype.upper()

    @property
    def is_output(self) -> bool:
        pt = self.porttype.upper()
        return "OUTPUT" in pt or "RETURN" in pt or "VARIABLE" in pt

    @property
    def is_input(self) -> bool:
        return "INPUT" in self.porttype.upper() or "LOOKUP" in self.porttype.upper()


class Table(BaseModel):
    """A SOURCE or TARGET definition."""

    name: str
    kind: str  # SOURCE | TARGET
    dbdname: str = ""
    owner: str = ""
    fields: list[Port] = Field(default_factory=list)


class Transformation(BaseModel):
    name: str
    type: str
    reusable: bool = False
    ports: list[Port] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    groups: list[tuple[str, str]] = Field(default_factory=list)  # (group name, filter condition)

    def port(self, name: str) -> Optional[Port]:
        for p in self.ports:
            if p.name == name:
                return p
        return None


class Instance(BaseModel):
    name: str
    type: str  # SOURCE | TARGET | TRANSFORMATION | MAPPLET
    transformation_name: str
    transformation_type: str = ""


class Connector(BaseModel):
    from_instance: str
    from_field: str
    to_instance: str
    to_field: str


class Mapping(BaseModel):
    name: str
    folder: str = ""
    sources: dict[str, Table] = Field(default_factory=dict)
    targets: dict[str, Table] = Field(default_factory=dict)
    transformations: dict[str, Transformation] = Field(default_factory=dict)
    instances: dict[str, Instance] = Field(default_factory=dict)
    connectors: list[Connector] = Field(default_factory=list)
    parameters: dict[str, str] = Field(default_factory=dict)  # $$NAME -> default
    unparsed_nodes: list[str] = Field(default_factory=list)

    def upstream(self, instance: str) -> list[str]:
        seen: list[str] = []
        for c in self.connectors:
            if c.to_instance == instance and c.from_instance not in seen:
                seen.append(c.from_instance)
        return seen

    def downstream(self, instance: str) -> list[str]:
        seen: list[str] = []
        for c in self.connectors:
            if c.from_instance == instance and c.to_instance not in seen:
                seen.append(c.to_instance)
        return seen


class CoverageRow(BaseModel):
    informatica_name: str
    informatica_type: str
    dbt_artifact: str
    layer: str
    status: Status
    notes: str = ""


class ReviewItem(BaseModel):
    where: str
    reason: str
    original: str = ""
