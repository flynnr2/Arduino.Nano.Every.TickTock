# Protocol Wire Contract (Normative)

## 1) Normative source declaration

This document is the single normative specification for emitted serial records consumed by host integrations.

**Source-of-truth rule:** `Nano.Every/src/PendulumProtocol.h` is the authoritative wire-contract source. If this document and code ever differ, `PendulumProtocol.h` is correct and this document must be updated to match it. This includes tags, schema IDs, field order, version constants, and mode semantics.

## 2) Record-family contract tables

### 2.1 `CFG`

| Contract item | Value |
|---|---|
| Exact tag name | `CFG` |
| Line shape / grammar | `CFG,<key>=<value>(,<key>=<value>)*` |
| Cardinality | once/boot + on-demand (`emit meta`, `emit startup`) |
| Mode applicability | CANONICAL and DERIVED |

Notes:
- Key names are compact (`pv`, `nhz`, `st`, `ss`, `asv`, `hm`, `em`, `cst`, `css`, `cpt`, `cps`) per `CFG_KEY_*` constants.

### 2.2 `STS`

| Contract item | Value |
|---|---|
| Exact tag name | `STS` |
| Line shape / grammar | `STS,<status_code>,<family>,<payload...>` |
| Cardinality | once/boot + periodic + on-demand + event-driven |
| Mode applicability | CANONICAL and DERIVED |

Notes:
- Status codes are from `StatusCode` (`OK`, `UNKNOWN_COMMAND`, `INVALID_PARAM`, `INVALID_VALUE`, `INTERNAL_ERROR`, `PROGRESS_UPDATE`).
- `STS_FAMILY_SCHEMA` and `STS_FAMILY_CFG` are explicit wire families.

### 2.3 `SCH`

| Contract item | Value |
|---|---|
| Exact tag name | `SCH` |
| Line shape / grammar | `SCH,<tag>,<schema_id>,<csv_fields>` |
| Cardinality | once/boot + on-demand |
| Mode applicability | CANONICAL |

Notes:
- Used for canonical declarations of `CSW` and `CPS` schemas.

### 2.4 `CSW`

| Contract item | Value |
|---|---|
| Exact tag name | `CSW` |
| Line shape / grammar | `CSW,<value_1>,<value_2>,...,<value_n>` |
| Cardinality | per-sample |
| Mode applicability | CANONICAL |

Notes:
- Column order is declared via `SCH,CSW,canonical_swing_v1,...`.

### 2.5 `CPS`

| Contract item | Value |
|---|---|
| Exact tag name | `CPS` |
| Line shape / grammar | `CPS,<value_1>,<value_2>,...,<value_n>` |
| Cardinality | per-sample (per PPS event row) |
| Mode applicability | CANONICAL |

Notes:
- Column order is declared via `SCH,CPS,canonical_pps_v1,...`.

### 2.6 `HDR_PART`

| Contract item | Value |
|---|---|
| Exact tag name | `HDR_PART` |
| Line shape / grammar | `HDR_PART,<part_index>,<part_count>,<csv_fields>` |
| Cardinality | once/boot sequence + on-demand sequence |
| Mode applicability | DERIVED |

Notes:
- Active mode identifier is `segmented_v1` with `HDR_SEGMENTED_PART_COUNT=4`.

### 2.7 `SMP`

| Contract item | Value |
|---|---|
| Exact tag name | `SMP` |
| Line shape / grammar | `SMP,<value_1>,<value_2>,...,<value_n>` |
| Cardinality | per-sample |
| Mode applicability | DERIVED |

Notes:
- Canonical field order for `SMP` is defined by `SAMPLE_SCHEMA` and `CsvField`.

## 3) Versioning contract

### 3.1 Version constants

- `PROTOCOL_VERSION = 1`
- `STS_SCHEMA_VERSION = 4`
- `SAMPLE_SCHEMA_ID = raw_cycles_hz_v7`
- `CANONICAL_SWING_SCHEMA_ID = canonical_swing_v1`
- `CANONICAL_PPS_SCHEMA_ID = canonical_pps_v1`

### 3.2 Compatibility / breaking-change policy

When wire compatibility changes, bump versions/schema IDs with the following minimum policy:

1. **`PROTOCOL_VERSION` must be bumped** for any breaking change affecting wire-level interoperability across record families, including:
   - tag renames/removals,
   - required record grammar changes,
   - changed meaning of previously emitted keys/fields where backward parsing is not safe.

2. **`STS_SCHEMA_VERSION` must be bumped** when `STS` payload schema/order/required semantics change incompatibly for existing parsers.

3. **Per-record schema IDs must be bumped** when field list/order/required interpretation changes for that family:
   - `raw_cycles_hz_v7` for `SMP` (`SAMPLE_SCHEMA`/`CsvField`),
   - `canonical_swing_v1` for `CSW`,
   - `canonical_pps_v1` for `CPS`.

4. **At least one of the above must change** for any incompatible field-order or field-semantics change. Never ship incompatible output while keeping all version/schema identifiers unchanged.

5. Additive, backward-compatible changes should prefer additive key/value extension where parsers can safely ignore unknown fields; if parser safety is uncertain, treat as breaking and bump.

## 4) Authoritative order vs segmented transport

- `SAMPLE_SCHEMA` and `enum CsvField` define the **canonical** `SMP` field ordering contract.
- `SAMPLE_SCHEMA_HDR_PARTS` define **segmented transport/readability groups** for `HDR_PART` emission.
- `SAMPLE_SCHEMA_HDR_PARTS` may differ from canonical serialization ordering semantics and must **not** be used as the authoritative `SMP` order.
- Host implementations should:
  1. validate and reassemble the full `HDR_PART` sequence as transport metadata,
  2. treat `SAMPLE_SCHEMA` as authoritative order for `SMP` parsing/storage logic.

## 5) Host integrator target

This document is the primary integration target for host implementers.

- Repository root reference: `README.md`
- Docs index reference: `Docs/README.md`

Both should link here as the first stop for serial wire-contract integration.

## CFG records and keys

This document is the primary documentation home for emitted serial records and CFG keys.

Current emitted compact keys: `pv`, `nhz`, `st`, `ss`, `asv`, `hm`, `em`, `cst`, `css`, `cpt`, `cps`.

- `pv`: protocol version
- `nhz`: nominal timer frequency (ticks/sec)
- `st` / `ss`: active derived sample tag/schema id
- `asv`: adjustment semantics version
- `hm`: header declaration mode
- `em`: active emit mode (`CANONICAL` or `DERIVED`)
- `cst` / `css`: canonical swing tag/schema id
- `cpt` / `cps`: canonical PPS tag/schema id

Host parser guidance:
1. Parse `CFG` key-value pairs (optionally corroborate with `STS ... cfg`).
2. Branch by `em` for accepted data families.
3. Ignore unknown keys for forward compatibility; do not fail on extra keys.
