from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from app.errors import AppError


class ExternalContractSchemas:
    def __init__(self, final_plan_path: Path, ontology_review_path: Path):
        self._final_plan = self._load(final_plan_path)
        self._ontology_review = self._load(ontology_review_path)

    @staticmethod
    def _load(path: Path) -> Draft7Validator:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(schema)
        return Draft7Validator(schema, format_checker=FormatChecker())

    def validate_final_plan(self, value: Any) -> None:
        self._validate(self._final_plan, value, "FINAL_PLAN_SCHEMA_INVALID")

    def validate_ontology_review(self, value: Any) -> None:
        self._validate(self._ontology_review, value, "ONTOLOGY_REVIEW_SCHEMA_INVALID")

    @staticmethod
    def _validate(validator: Draft7Validator, value: Any, code: str) -> None:
        errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
        if errors:
            error = errors[0]
            path = ".".join(str(part) for part in error.absolute_path)
            raise AppError(422 if code.startswith("FINAL") else 500, code, "external contract validation failed", details={"path": path, "message": error.message})
