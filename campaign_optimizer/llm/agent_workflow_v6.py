"""v6 role configuration loader; runtime semantics remain v5-compatible."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .agent_workflow_v5 import MAX_REVISION_ROUNDS, PROFILES, PROMPTS, ROLES, RoleConfiguration

CONFIG = Path(__file__).with_name("agent_roles.v6.json")


def load_role_configuration(path: Path = CONFIG) -> RoleConfiguration:
    raw: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0" or raw.get("configuration_version") != "agent_roles_v6":
        raise ValueError("invalid agent role configuration")
    aliases = raw.get("model_aliases")
    artifacts = raw.get("prompt_artifacts")
    expected_hashes = raw.get("expected_prompt_hashes")
    if not all(isinstance(value, Mapping) for value in (aliases, artifacts, expected_hashes)):
        raise ValueError("role configuration mappings are required")
    if set(aliases) != ROLES or set(artifacts) != ROLES or set(expected_hashes) != ROLES:
        raise ValueError("all three roles require aliases, prompts, and pinned hashes")
    if any(not isinstance(alias, str) or not alias.strip() for alias in aliases.values()) or len(set(aliases.values())) != len(ROLES):
        raise ValueError("model aliases must be nonempty and heterogeneous")
    versions: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for role, filename in artifacts.items():
        if not isinstance(filename, str):
            raise ValueError("prompt artifact name must be a string")
        prompt_path = (PROMPTS / filename).resolve()
        if prompt_path.parent != PROMPTS.resolve() or not prompt_path.is_file():
            raise ValueError("prompt artifact must remain in the local prompts directory")
        actual_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        expected_hash = expected_hashes[role]
        if not isinstance(expected_hash, str) or actual_hash != expected_hash.lower():
            raise ValueError("prompt artifact hash does not match the approved configuration")
        versions[role] = prompt_path.stem
        hashes[role] = actual_hash
    profiles = raw.get("revision_profiles")
    if not isinstance(profiles, Mapping) or set(profiles) != PROFILES:
        raise ValueError("all approved revision profiles are required")
    if any(type(value) is not int or not 0 <= value <= MAX_REVISION_ROUNDS for value in profiles.values()):
        raise ValueError("revision profiles must stay inside the five-round hard cap")
    return RoleConfiguration(dict(aliases), versions, hashes, raw["output_contract_prompt_version"], dict(profiles))
