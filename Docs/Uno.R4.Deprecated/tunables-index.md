# Tunables Index

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Runtime tunables (UNO)

These are POST field names on `/uno`. Logging and OLED rating settings are saved in EEPROM.

| Name               | Default | Effect                                                                                                                             |
| ------------------ | ------: | ---------------------------------------------------------------------------------------------------------------------------------- |
| `logEnabled`       | `1`     | Start/stop SD logging.                                                                                                             |
| `logDaily`         | `0`     | Daily rollover mode.                                                                                                               |
| `logStartupPolicy` | `1`     | Startup file policy: `0` start a new set if one exists, `1` overwrite base files, `2` preserve existing files and start a new set. |
| `logAppend`        | `0`     | Legacy compatibility setting; `1` maps to append policy, `0` maps to overwrite policy.                                             |
| `oledShortMinutes` | `5`     | Short display EWMA half-life, whole minutes, 1–30.                                                                                 |
| `oledLongMinutes`  | `60`    | Long display EWMA half-life, whole minutes, 15–360; at least twice short.                                                          |

The OLED fields affect only the display estimators. Changing either horizon restarts both display estimates; saving unchanged horizons retains their state. Changing only OLED fields does not restart logging, alter capture, or add fields to raw PCPS/PCSW records. The status screen shows the active half-lives.

HTTP rejects invalid numbers, ranges, or short/long combinations with `400 Bad Request` and saves nothing. Internal/EEPROM sanitization clamps ranges and raises long to twice short if needed. Version 4 EEPROM records preserve logging and legacy statistics settings; valid version 2/3 records migrate with OLED defaults of 5/60. Migration writes the older/invalid slot first so an interrupted write leaves a valid prior configuration. Factory reset restores 5/60.

The former `statsWindowSize`, `rollingWindowMs` and `blockJumpUs` fields are inert and hidden from `/uno`. Their stored layout is retained for EEPROM compatibility.


## Runtime tunables (Nano)

(Selected tunables; the complete 16-name proxy list is in `NanoComm.cpp`, with shared names in `PendulumCommands.h`. Interface documented for integration; Nano firmware sources are external to this repository.)

| Name                | Effect                                  |
| ------------------- | --------------------------------------- |
| `ppsFastShift`      | Fast estimator smoothing                |
| `ppsSlowShift`      | Slow estimator smoothing                |
| `ppsLockRppm`       | Lock threshold (fast/slow disagreement) |
| `ppsLockMadTicks`   | Lock threshold (PPS MAD ticks)          |
| `ppsUnlockMadTicks` | Unlock threshold (PPS MAD ticks)        |

## Compile-time diagnostics profile knobs (UNO)

- `ENABLE_DIAG_INFO`
- `ENABLE_DIAG_PROTOCOL_VERBOSE`
- `ENABLE_DIAG_RECOVERY_TRACE`
- `ENABLE_DIAG_SERVICE_HEARTBEAT`
- `DEBUG_TIMING`

## Compile-time Wi-Fi policy timings (UNO)

- `WIFI_CONNECT_TIMEOUT_MS`
- `WIFI_RECONNECT_INTERVAL_MS`
- `WIFI_AP_RESTART_BACKOFF_MS`
- `WIFI_AP_DROP_GRACE_MS`
- `WIFI_RUNTIME_STA_INITIAL_HOLD_MS`
- `WIFI_RUNTIME_STA_RETRY_SPACING_MS`
- `WIFI_RUNTIME_STA_MAX_RETRIES`
