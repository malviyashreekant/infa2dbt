"""Tool configuration. Every open design decision is a switch with a safe default."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class Config(BaseModel):
    project_name: str = "infa_migration"
    source_name: str = "raw"  # dbt source name used in sources.yml
    default_source_schema: str = ""  # used when a table name carries no schema
    legacy_schema_var: str = "legacy_schema"  # var holding the schema Informatica loaded into

    # Decisions the user has not made yet: defaults are the conservative option.
    sequence_mode: Literal["row_number", "oracle_sequence"] = "row_number"
    no_update_strategy_load: Literal["table", "incremental_append"] = "table"
    int_materialization: Literal["view", "ephemeral"] = "view"
    allow_llm_assist: bool = False  # reserved; the rule-based translator never calls an LLM

    dim_prefixes: list[str] = Field(default_factory=lambda: ["DIM_", "D_"])
    parameters: dict[str, str] = Field(default_factory=dict)  # $$NAME -> value override
    function_overrides: dict[str, str] = Field(default_factory=dict)  # FUNC -> "SQL with {0} {1}"


def load_config(path: Optional[str | Path]) -> Config:
    if not path:
        return Config()
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Config(**data)
