# Command Interface Contract

This document defines the runtime CLI contract implemented by `processSerialCommands()` in `Nano.Every/src/SerialParser.cpp`.

## Scope and transport

- Command transport is `CMD_SERIAL` (see `SerialParser.h` compile-time routing).
- Commands are accumulated until newline (`\n`) and processed one line at a time.
- Carriage return (`\r`) is ignored.
- Command lines longer than the parser buffer are rejected with:
  - human text: `ERROR: command too long`
  - machine status: `STS,INVALID_VALUE,command too long`

## Grammar

Tokens are ASCII, **space-delimited**, and command words are matched **case-insensitively**.

- Case-insensitive command tokens include: `help`, `get`, `set`, `reset`, `repair`, `emit`, plus `meta`, `startup`, `defaults`, and `eeprom` subcommand tokens.
- `?` is accepted as an alias for `help`.

EBNF-like notation:

- `<sp>` = one ASCII space (`0x20`) as token delimiter
- `<eol>` = newline terminator (`\n`)
- `<word>` = non-space token

```text
command_line ::= command <eol>

command ::= help_cmd
          | get_cmd
          | set_cmd
          | reset_cmd
          | emit_cmd
          | repair_cmd

help_cmd  ::= ("help" | "?") [<sp> help_target]
help_target ::= <word>             ; expected: command name or "tunables"

get_cmd   ::= "get" <sp> param
set_cmd   ::= "set" <sp> param <sp> value
reset_cmd ::= "reset" <sp> "defaults"
repair_cmd ::= "repair" <sp> "eeprom"
emit_cmd  ::= "emit" <sp> ("meta" | "startup")

param ::= <word>
value ::= <word>
```

Notes:
- Parser delimiter is literal space; there is no quoted-string support.
- Multiple adjacent spaces are tolerated by tokenization.
- A blank line (newline with no token) is ignored.
- Tunable names are also case-insensitive; replies use their registered spelling.

## Cardinality and explicit rejection rules

The parser enforces argument counts exactly as follows.

### `help`, `help <command|tunables>`

- Accepted:
  - `help`
  - `?`
  - `help tunables`
  - `help get` (or another command token)
- Rejected when more than one argument is present:
  - Human: `ERROR: help takes at most one argument`
  - STS: `STS,INVALID_PARAM,help takes at most one argument`

### `get <param>`

- Missing arg:
  - Human: `ERROR: get requires <param>`
  - STS: `STS,INVALID_PARAM,get requires <param>`
- Extra args (anything after first param):
  - Human: `ERROR: get requires exactly one parameter`
  - STS: `STS,INVALID_PARAM,get requires exactly one parameter`
- Unknown parameter:
  - Human: `ERROR: unknown parameter`
  - STS: `STS,INVALID_PARAM,unknown parameter`

### `set <param> <value>`

When `CLI_ALLOW_MUTATIONS=1`:
- Missing/partial args:
  - Human: `ERROR: set requires <param> and <value>`
  - STS: `STS,INVALID_PARAM,set requires <param> and <value>`
- Extra args:
  - Human: `ERROR: set requires exactly <param> <value>`
  - STS: `STS,INVALID_PARAM,set requires exactly <param> <value>`
- Unknown parameter:
  - Human: `ERROR: unknown parameter`
  - STS: `STS,INVALID_PARAM,unknown parameter`

When `CLI_ALLOW_MUTATIONS=0`:
- Any `set ...` form is rejected before arg parsing:
  - Human: `ERROR: set is disabled by build policy`
  - STS: `STS,INVALID_PARAM,set disabled by build policy`

### `reset defaults`

When `CLI_ALLOW_MUTATIONS=1`:
- Missing action:
  - Human: `ERROR: reset requires <action>`
  - STS: `STS,INVALID_PARAM,reset requires <action>`
- Unsupported action (anything except `defaults`):
  - Human: `ERROR: reset supports only 'defaults'`
  - STS: `STS,INVALID_PARAM,reset supports only defaults`
- Extra args:
  - Human: `ERROR: reset requires exactly one action`
  - STS: `STS,INVALID_PARAM,reset requires exactly one action`

When `CLI_ALLOW_MUTATIONS=0`:
- Any `reset ...` form is rejected before arg parsing:
  - Human: `ERROR: reset is disabled by build policy`
  - STS: `STS,INVALID_PARAM,reset disabled by build policy`

### `repair eeprom`

When `CLI_ALLOW_MUTATIONS=1`:
- Missing or unsupported action:
  - STS: `STS,INVALID_PARAM,repair requires eeprom`
- Extra args:
  - Human: `ERROR: repair requires exactly eeprom`
  - STS: `STS,INVALID_PARAM,repair requires exactly eeprom`
- No valid saved source or failed write verification:
  - STS: `STS,INTERNAL_ERROR,EEPROM repair failed: no valid source or write verification failed`
  - Current EEPROM slot-health status follows.
- Success (including an already healthy pair of slots):
  - STS: `STS,OK,repair,eeprom`
  - Current EEPROM slot-health status follows.

When `CLI_ALLOW_MUTATIONS=0`, any `repair ...` form is rejected before argument
parsing with `STS,INVALID_PARAM,repair disabled by build policy`. This branch
emits no separate human-readable error.

### `emit meta|startup`

- Missing subcommand:
  - Human: `ERROR: emit requires subcommand`
  - STS: `STS,INVALID_PARAM,emit requires subcommand`
- Unknown subcommand:
  - Human: `ERROR: unknown emit subcommand: <token>`
  - STS: `STS,UNKNOWN_COMMAND,<token>`
- Extra args after `meta` or `startup`:
  - Human: `ERROR: emit <meta|startup> takes no arguments`
  - STS: `STS,INVALID_PARAM,emit <meta|startup> takes no arguments`

### Unknown top-level command

- Human: `ERROR: unknown command`
- STS: `STS,UNKNOWN_COMMAND,<token>`

## Dual-channel response contract

Each command may emit two complementary channels:

1. **Human-readable command channel (`CMD_SERIAL`)**
   - Plain text feedback, usage errors, and value prints.
   - Examples:
     - `get: ppsLockCount = 5`
     - `set: ppsLockCount = 6`
     - `reset: defaults restored from firmware and saved to EEPROM`

2. **Machine-parseable status channel (`STS,...`)** via `sendStatus` / `sendStatusFromOwnedBuffer`
   - CSV structure:

```text
STS,<StatusCode>[,<detail_or_payload>]
```

- `StatusCode` token is the symbolic string from `statusCodeToStr(...)`.
- `detail_or_payload` is context-dependent text/payload.

For robust automation, host tools should key logic off `STS` lines and treat human text as operator UX.

## Status code table (`StatusCode`)

| Enum                         | Wire token        | Meaning                                                                 |
| ---------------------------- | ----------------- | ----------------------------------------------------------------------- |
| `StatusCode::Ok`             | `OK`              | Command accepted/success acknowledgment                                 |
| `StatusCode::UnknownCommand` | `UNKNOWN_COMMAND` | Command or subcommand token not recognized                              |
| `StatusCode::InvalidParam`   | `INVALID_PARAM`   | Wrong/missing/extra argument or policy-rejected command                 |
| `StatusCode::InvalidValue`   | `INVALID_VALUE`   | Value-level issue (for example oversized command line)                  |
| `StatusCode::InternalError`  | `INTERNAL_ERROR`  | Internal operation failure, including formatting or EEPROM verification |
| `StatusCode::ProgressUpdate` | `PROGRESS_UPDATE` | Informational progress/telemetry updates                                |

### Concrete success/error examples

Success examples:

```text
> emit meta
STS,OK,emit,meta
...
```

```text
> get ppsLockCount
get: ppsLockCount = 5
STS,OK,get,ppsLockCount,5
```

Error examples:

```text
> emit foo
ERROR: unknown emit subcommand: foo
STS,UNKNOWN_COMMAND,foo
```

```text
> set ppsLockCount
ERROR: set requires <param> and <value>
STS,INVALID_PARAM,set requires <param> and <value>
```

```text
> this_is_not_a_command
ERROR: unknown command
STS,UNKNOWN_COMMAND,this_is_not_a_command
```

## Tunable command ack payload formats (`emitTunableCommandAck()`)

On successful formatting, `emitTunableCommandAck()` sends `StatusCode::Ok`
and structures payload by action. Buffer acquisition or truncation failures
instead attempt an `INTERNAL_ERROR` response.

- **Get ack**
  - Format: `STS,OK,get,<param>,<value>`
  - Example: `STS,OK,get,ppsLockCount,5`

- **Set ack**
  - Format: `STS,OK,set,<param>,<value>`
  - Example: `STS,OK,set,ppsLockCount,6`

- **Reset ack**
  - Format: `STS,OK,reset,defaults`

These payload action tokens map to `STS_TUNABLES_GET`, `STS_TUNABLES_SET`, and `STS_TUNABLES_RESET`.

`set` accepts decimal digits only and checks the same semantic rules as EEPROM
loading, before applying any normalization. Invalid values or incompatible pairs
return `INVALID_VALUE` and restore the previous live settings. For example,
`set ppsUnlockCount 100` and a slow shift below the fast shift are rejected.
Individual limits appear in `help tunables`; see the
[PPS tunable reference](PPS_Discipliner_Guide.md#runtime-tunable-reference).

Success is acknowledged only after EEPROM readback verifies the header, payload
and final commit marker. A failed write returns `INTERNAL_ERROR`, restores the
previous live settings and preserves the other valid EEPROM slot. Change related
thresholds in an order that keeps each intermediate configuration valid: raise
an unlock threshold before raising its lock threshold, for example.

A success acknowledgement reports firmware-verified persistence; a host need
not perform a second EEPROM readback to establish what that acknowledgement
means. If the acknowledgement is lost or times out, execution is uncertain. A
command may have completed even though the host did not receive its reply; inspect
current settings and slot status before deciding whether to retry.

`repair eeprom` restores redundancy by copying the newest valid saved settings
into the invalid slot and verifying the write. It preserves live settings and
PPS acquisition state, does nothing when both slots are valid, and fails without
writing if neither slot is valid. Success is `STS,OK,repair,eeprom`; missing or
unsupported action is `STS,INVALID_PARAM,repair requires eeprom`. Extra arguments
are rejected. The command respects `CLI_ALLOW_MUTATIONS`. It does not erase the
EEPROM, restore defaults, or migrate unknown schemas. `emit startup` refreshes
slot diagnostics from EEPROM, so the repair can be checked without rebooting.

`reset defaults` restores and saves firmware defaults, resets PPS runtime state,
and replays tunable/config/PPS metadata and the active schema declarations.

`emit meta` acknowledges first, then emits mirrored `STS ... cfg`, `CFG`, and
the active schemas. `emit startup` acknowledges first, then replays reset status,
required protocol metadata, enabled boot diagnostics, and active schemas.

## Build policy: `CLI_ALLOW_MUTATIONS`

`CLI_ALLOW_MUTATIONS` is a compile-time policy gate in `Config.h`:

- `CLI_ALLOW_MUTATIONS=1` (default): `set`, `reset defaults`, and `repair eeprom` are enabled.
- `CLI_ALLOW_MUTATIONS=0`: `set`, `reset`, and `repair` are disabled irrespective of supplied arguments.

Disabled behavior contract:

- `set ...` ->
  - `ERROR: set is disabled by build policy`
  - `STS,INVALID_PARAM,set disabled by build policy`
- `reset ...` ->
  - `ERROR: reset is disabled by build policy`
  - `STS,INVALID_PARAM,reset disabled by build policy`
- `repair ...` ->
  - `STS,INVALID_PARAM,repair disabled by build policy`

`help`, `get`, and `emit` remain available.
