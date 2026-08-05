"""Pinned configuration and budget for the v12 Function Calling experiment."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .agent_workflow_v11 import load_role_configuration as load_v11_configuration
from .agent_workflow_v9 import RoleConfigurationV9

ROOT = Path(__file__).parent
CONFIG = ROOT / "agent_roles.v12.json"
TOOLS = ROOT / "tools"


@dataclass(frozen=True)
class RoleConfigurationV12:
    base: RoleConfigurationV9
    tool_name: str
    tool_schema: Mapping[str, Any]
    tool_schema_hash: str

    @property
    def roles(self):
        return self.base.roles

    @property
    def executor_max_output_tokens(self):
        return self.base.executor_max_output_tokens


def load_role_configuration(path: Path = CONFIG) -> RoleConfigurationV12:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("configuration_version") != "agent_roles_v12_function_experiment":
        raise ValueError("invalid v12 configuration")
    # Reuse the reviewed v11 loader by validating an in-memory-equivalent v11
    # role block here, while independently pinning v12 prompt bytes below.
    aliases, artifacts, hashes = raw["model_aliases"], raw["prompt_artifacts"], raw["expected_prompt_hashes"]
    from .agent_workflow_v5 import MAX_REVISION_ROUNDS, PROFILES, PROMPTS, ROLES, RoleConfiguration
    if set(aliases) != ROLES or set(artifacts) != ROLES or set(hashes) != ROLES or len(set(aliases.values())) != 3:
        raise ValueError("invalid role mappings")
    versions, actual_hashes = {}, {}
    for role, filename in artifacts.items():
        prompt = (PROMPTS / filename).resolve()
        if prompt.parent != PROMPTS.resolve() or not prompt.is_file():
            raise ValueError("prompt must remain local")
        actual = hashlib.sha256(prompt.read_bytes()).hexdigest()
        if actual != hashes[role]:
            raise ValueError("prompt hash mismatch")
        versions[role], actual_hashes[role] = prompt.stem, actual
    profiles = raw["revision_profiles"]
    if set(profiles) != PROFILES or any(type(v) is not int or not 0 <= v <= MAX_REVISION_ROUNDS for v in profiles.values()):
        raise ValueError("invalid revision profiles")
    limit = raw["generation_limits"]["executor_max_output_tokens"]
    if type(limit) is not int or not 1024 <= limit <= 8192:
        raise ValueError("invalid executor limit")
    role_config = RoleConfiguration(dict(aliases), versions, actual_hashes, raw["output_contract_prompt_version"], dict(profiles))
    base = RoleConfigurationV9(role_config, limit, "qwen3.8-max-preview")
    tool = raw["reviewer_tool"]
    if tool["name"] != "submit_reviewer_decision_v1":
        raise ValueError("unexpected tool name")
    schema_path = (TOOLS / tool["schema_artifact"]).resolve()
    if schema_path.parent != TOOLS.resolve() or not schema_path.is_file():
        raise ValueError("tool schema must remain local")
    schema_bytes = schema_path.read_bytes()
    actual_tool_hash = hashlib.sha256(schema_bytes).hexdigest()
    if actual_tool_hash != tool["schema_sha256"]:
        raise ValueError("tool schema hash mismatch")
    schema = json.loads(schema_bytes)
    return RoleConfigurationV12(base, tool["name"], schema, actual_tool_hash)


def max_provider_calls_v12(max_revision_rounds: int, triage_used: bool = False) -> int:
    if type(max_revision_rounds) is not int or not 0 <= max_revision_rounds <= 5:
        raise ValueError("revision rounds outside cap")
    return 4 * (max_revision_rounds + 1) + int(triage_used)


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedgerV12:
    def __init__(self) -> None:
        self.total_limit = 25
        self.used = 0
        self.candidate = -1
        self.by_candidate: dict[int, dict[str, int]] = {}
        self.triage_calls = 0

    def set_limit(self, limit: int) -> None:
        if self.used > limit:
            raise BudgetExceeded("budget already exceeded")
        self.total_limit = limit

    def begin_candidate(self, candidate: int) -> None:
        self.candidate = candidate
        self.by_candidate.setdefault(candidate, {"executor": 0, "reviewer": 0})

    def consume(self, role: str) -> None:
        if self.used >= self.total_limit:
            raise BudgetExceeded("total provider budget exceeded")
        if role == "triage":
            if self.triage_calls >= 1:
                raise BudgetExceeded("triage budget exceeded")
            self.triage_calls += 1
        elif role in {"executor", "reviewer"}:
            if self.candidate < 0:
                raise BudgetExceeded("candidate budget not initialized")
            counts = self.by_candidate[self.candidate]
            if counts[role] >= 2:
                raise BudgetExceeded("per-candidate role budget exceeded")
            counts[role] += 1
        self.used += 1
