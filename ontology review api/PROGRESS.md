# Ontology Review API — Progress Checkpoint

Last saved: 2026-08-04 (Asia/Shanghai)

## Current position

- Phase 1 deployable foundation was completed and previously verified with 38 passing tests.
- A second adversarial review found 11 patch items; the user approved applying all of them.
- The Review API foundation spec is now marked `done` after implementation and final review:
  `_bmad-output/implementation-artifacts/spec-review-api-deployable-foundation.md`.
- The canonical Ontology directory remains read-only and has not been modified by this patch round.

## Confirmed product decision

For R3 percentage-only budget increases, if G2 does not have both the platform minimum budget and the resulting concrete daily budget, keep the Ontology rule match but downgrade the disposition to `REVIEW`. Never auto-execute without the G2 evidence.

The only authoritative Ontology source is `docs/ontology/ontology 概念卡/`. The divergent copy under `docs/campaign-optimizer-llm-integration-main/campaign_optimizer/ontology/` must not be loaded by the Review API or treated as a second source of truth. Integration may align API exchange contracts with the campaign optimizer (`final_plan` / `ontology_review`), but must not silently replace the canonical concepts, R1-R7, G1-G2, clients, schemas, or version semantics.

## Patch work already written to disk

The interrupted implementation wrote changes in these areas:

- R3/G2 safety downgrade and multi-rule conflict disposition;
- finite numeric guardrail checks;
- closed entity-grain values and non-empty expected Ontology version;
- bounded nested JSON strings;
- client tolerance validation;
- configured-role validation;
- Alembic revision/required-table readiness checks;
- safer temporary-file restore flow.

Files modified during the interrupted patch run include:

- `app/services/review_engine.py`
- `app/domain/models.py`
- `app/ontology/loader.py`
- `app/config.py`
- `app/db/database.py`
- `scripts/restore.sh`

## Final review and verification completed

The interrupted patch run was resumed on 2026-08-04. All 11 approved patch items were completed and checked off.

Verification completed:

- Pytest: 53 passed.
- Canonical Ontology assertions: 35 scenarios passed.
- Field mapping check: passed.
- Demo data adapter check: passed.
- API-level POST/GET persistence coverage now includes every applicable canonical positive, negative, boundary, conflict, client gate, G1, G2, and R7 fixture.
- Nested request JSON has both per-container and aggregate size limits.
- Concept ranges are rejected when malformed, non-finite, or reversed.

The deployable Python/API foundation is complete. Actual Docker/Compose runtime verification remains an environment handoff because Docker is unavailable on this computer.

## Known environment limit

Docker is not installed on this computer, so Docker/Compose image verification still needs a teammate machine or ECS. Python/API tests can run locally through the existing project `.venv`.

## Next product phase after patch completion

The plan-review contract adapter is now implemented. It accepts the campaign optimizer's validated `final_plan` / `review_evidence`, evaluates only the canonical Ontology package, persists the exchange, and returns an `ontology_review` object compatible with their JSON schema.

The acceptance and edge review repair round is complete. Verification now includes 67 passing Review API tests, 35 canonical assertion scenarios plus both mapping checks, and 377 passing downstream tests with 1 pre-existing/configured skip. Runtime Draft-07 input/output validation, canonical checksum locking, raw/normalized audit storage, PostgreSQL-safe field definitions and migration, strict evidence semantics, immutable replay, and downstream canonical compatibility are implemented.

The next handoff is deployment verification: run the migrations and smoke test against the team's real PostgreSQL-compatible PolarDB instance, then build/run the Docker image on the teammate machine or ECS. These cloud/runtime checks are not claimed as complete because this computer has no Docker or live PolarDB connection. After that, connect the other team's HTTP client and continue with user feedback, Review state transitions, and Ontology governance. Do not create a replacement project.

PolarDB edge-review hardening was applied on 2026-08-04: percent-encoded Alembic URLs, PostgreSQL advisory migration locking, readiness-triggered recovery after transient startup failure, failed-engine disposal, required finite connection timeout, disposable `_test` database acknowledgement, exact cleanup, and conflicting idempotency replay coverage. The local Review API suite now collects 78 tests: 77 pass and the single live-PostgreSQL test is intentionally skipped without operator credentials. Canonical and downstream suites remain green.
