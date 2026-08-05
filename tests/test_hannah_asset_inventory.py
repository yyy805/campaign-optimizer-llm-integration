from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
INVENTORY = (
    ROOT / 'campaign_optimizer' / 'ontology' / 'assertions'
    / 'hannah_asset_migration_inventory.json'
)
HISTORY_PREFIX = 'campaign_optimizer/ontology/history/hannah_v1_1_demo/'


def test_every_inventoried_hannah_asset_is_preserved_exactly():
    inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
    entries = inventory['entries']
    assert inventory['source_commit'] == '24fc72c4e5837c02c9192b1d78f2acd363eeb527'
    assert inventory['expected_asset_count'] == len(entries) == 54
    assert len({entry['source_path'] for entry in entries}) == len(entries)
    assert len({entry['target_path'] for entry in entries}) == len(entries)

    for entry in entries:
        assert entry['disposition'] == 'COPIED_EXACT_HISTORICAL_NON_RUNTIME'
        assert entry['target_path'].startswith(HISTORY_PREFIX)
        raw = (ROOT / entry['target_path']).read_bytes()
        assert len(raw) == entry['size']
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']


def test_inventory_covers_assertions_mappings_adapters_and_rule_history():
    inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
    targets = {entry['target_path'] for entry in inventory['entries']}
    required_suffixes = {
        'VERSION',
        'assertions/story_assertions.json',
        'assertions/assertion.schema.json',
        'assertions/field_mapping.json',
        'assertions/field_mapping.schema.json',
        'assertions/demo_data_adapter.json',
        'assertions/demo_data_adapter.schema.json',
        *(f'rules/R{index}.json' for index in range(1, 8)),
    }
    for suffix in required_suffixes:
        assert any(target.endswith(suffix) for target in targets), suffix
    assert {entry['category'] for entry in inventory['entries']}.issuperset(
        {'assertion', 'mapping', 'adapter', 'version_history'}
    )
    assert inventory['excluded'] == [{
        'source_path': inventory['excluded'][0]['source_path'],
        'reason': 'EMPTY_TOOL_HISTORY_NOT_A_BUSINESS_ASSET',
        'size': 0,
    }]
