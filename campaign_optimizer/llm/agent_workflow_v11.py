"""v11 configuration loader with one fail-closed Reviewer retry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .agent_workflow_v5 import MAX_REVISION_ROUNDS, PROFILES, PROMPTS, ROLES, RoleConfiguration
from .agent_workflow_v9 import REVIEWER_REVERT_MODEL, RoleConfigurationV9, TEMPORARY_MODELS

CONFIG = Path(__file__).with_name("agent_roles.v11.json")


def load_role_configuration(path: Path = CONFIG) -> RoleConfigurationV9:
    raw: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0" or raw.get("configuration_version") != "agent_roles_v11":
        raise ValueError("invalid agent role configuration")
    note = raw.get("deployment_note")
    if not isinstance(note, Mapping) or note.get("temporary_general_model_studio_mapping") is not True or note.get("reviewer_revert_model") != REVIEWER_REVERT_MODEL:
        raise ValueError("v11 must remain explicitly temporary and reversible")
    aliases, artifacts, expected_hashes = raw.get("model_aliases"), raw.get("prompt_artifacts"), raw.get("expected_prompt_hashes")
    if aliases != TEMPORARY_MODELS or not all(isinstance(value, Mapping) for value in (aliases, artifacts, expected_hashes)):
        raise ValueError("invalid role mappings")
    if set(aliases) != ROLES or set(artifacts) != ROLES or set(expected_hashes) != ROLES:
        raise ValueError("all three roles require aliases, prompts, and pinned hashes")
    versions, hashes = {}, {}
    for role, filename in artifacts.items():
        if not isinstance(filename, str):
            raise ValueError("prompt artifact name must be a string")
        prompt_path = (PROMPTS / filename).resolve()
        if prompt_path.parent != PROMPTS.resolve() or not prompt_path.is_file():
            raise ValueError("prompt artifact must remain local")
        actual = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        if actual != expected_hashes[role]:
            raise ValueError("prompt artifact hash mismatch")
        versions[role], hashes[role] = prompt_path.stem, actual
    profiles = raw.get("revision_profiles")
    if not isinstance(profiles, Mapping) or set(profiles) != PROFILES or any(type(v) is not int or not 0 <= v <= MAX_REVISION_ROUNDS for v in profiles.values()):
        raise ValueError("invalid revision profiles")
    limits = raw.get("generation_limits")
    if not isinstance(limits, Mapping) or set(limits) != {"executor_max_output_tokens"}:
        raise ValueError("one Executor generation limit is required")
    limit = limits["executor_max_output_tokens"]
    if type(limit) is not int or not 1024 <= limit <= 8192:
        raise ValueError("invalid Executor generation limit")
    roles = RoleConfiguration(dict(aliases), versions, hashes, raw["output_contract_prompt_version"], dict(profiles))
    return RoleConfigurationV9(roles, limit, REVIEWER_REVERT_MODEL)


def max_provider_calls_v11(*, max_revision_rounds: int, triage_used: bool) -> int:
    from .agent_workflow_v5 import max_provider_calls_with_repairs
    return max_provider_calls_with_repairs(max_revision_rounds=max_revision_rounds) + (max_revision_rounds + 1) + int(triage_used)
