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

- Case-insensitive command tokens include: `help`, `get`, `set`, `reset`, `emit`, plus `meta`, `startup`, and `defaults` subcommand tokens.
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

help_cmd  ::= ("help" | "?") [<sp> help_target]
help_target ::= <word>             ; expected: command name or "tunables"

get_cmd   ::= "get" <sp> param
set_cmd   ::= "set" <sp> param <sp> value
reset_cmd ::= "reset" <sp> "defaults"
emit_cmd  ::= "emit" <sp> ("meta" | "startup")

param ::= <word>
value ::= <word>
```

Notes:
- Parser delimiter is literal space; there is no quoted-string support.
- Multiple adjacent spaces are tolerated by tokenization.
- A blank line (newline with no token) is ignored.

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

| Enum | Wire token | Meaning |
|---|---|---|
| `StatusCode::Ok` | `OK` | Command accepted/success acknowledgment |
| `StatusCode::UnknownCommand` | `UNKNOWN_COMMAND` | Command or subcommand token not recognized |
| `StatusCode::InvalidParam` | `INVALID_PARAM` | Wrong/missing/extra argument or policy-rejected command |
| `StatusCode::InvalidValue` | `INVALID_VALUE` | Value-level issue (for example oversized command line) |
| `StatusCode::InternalError` | `INTERNAL_ERROR` | Internal formatting/buffer failure while generating response |
| `StatusCode::ProgressUpdate` | `PROGRESS_UPDATE` | Informational progress/telemetry updates |

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

`emitTunableCommandAck()` always sends `StatusCode::Ok` and structures payload by action:

- **Get ack**
  - Format: `STS,OK,get,<param>,<value>`
  - Example: `STS,OK,get,ppsLockCount,5`

- **Set ack**
  - Format: `STS,OK,set,<param>,<value>`
  - Example: `STS,OK,set,ppsLockCount,6`

- **Reset ack**
  - Format: `STS,OK,reset,defaults`

These payload action tokens map to `STS_TUNABLES_GET`, `STS_TUNABLES_SET`, and `STS_TUNABLES_RESET`.

## Build policy: `CLI_ALLOW_MUTATIONS`

`CLI_ALLOW_MUTATIONS` is a compile-time policy gate in `Config.h`:

- `CLI_ALLOW_MUTATIONS=1` (default): `set` and `reset defaults` are enabled.
- `CLI_ALLOW_MUTATIONS=0`: `set` and `reset` are disabled irrespective of supplied arguments.

Disabled behavior contract:

- `set ...` ->
  - `ERROR: set is disabled by build policy`
  - `STS,INVALID_PARAM,set disabled by build policy`
- `reset ...` ->
  - `ERROR: reset is disabled by build policy`
  - `STS,INVALID_PARAM,reset disabled by build policy`

`help`, `get`, and `emit` remain available.
