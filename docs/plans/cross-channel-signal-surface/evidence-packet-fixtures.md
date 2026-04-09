# Evidence Packet Fixtures

Date: 2026-04-08
Status: pre-freeze fixture index for refined Move 1 packet work

## Purpose

Provide canonical JSON examples for first-wave packet consumers and for future runtime-owner validation.

## Fixture Directory

- `data/shadow/cross-channel-signal-surface/packet-fixtures/`

## Fixtures

### `ct-dns-shadow-packet.json`

Purpose:
- canonical first-wave required family example
- shadow-mode packet

### `ct-dns-active-packet.json`

Purpose:
- promoted first-wave required family example
- active-mode packet projected to the review queue

### `founder-aux-shadow-packet.json`

Purpose:
- conditional family example
- shows a valid founder auxiliary packet while still in shadow mode

### `production-existing-active-packet.json`

Purpose:
- shows how the packet contract applies to current production families once packet transport exists

### `invalid-missing-provenance-packet.json`

Purpose:
- negative fixture
- proves validation fails when required provenance fields are absent

### `fixture-manifest.v1.json`

Purpose:
- machine-readable index of fixture files
- records first-wave family expectations and current Track E readiness state

## Required Fields

Each valid fixture includes:
- `schema_version`
- `canonical_key`
- `company_name`
- `source_family`
- `signal_ids`
- `provenance_summary`
- `score_rationale`
- `family_mode`
- `review_endpoint`
- `created_at`

## Consumer Expectations

- `review_items.evidence_bundle` is the runtime owner after post-freeze implementation
- digest, dashboard, and `Why Now` are derived projections
- fixtures are contract examples, not a claim that the runtime owner already exists in this shape today

## Validation Notes

- `invalid-missing-provenance-packet.json` must fail packet validation
- `founder_aux` does not become first-wave active unless the readiness artifact passes
- `ct_dns` remains the first required new family regardless of Track E readiness

---

*Regenerate these fixtures if `evidence-packet-contract.md` changes.*
