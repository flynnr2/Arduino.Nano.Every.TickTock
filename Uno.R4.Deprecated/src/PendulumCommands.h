#pragma once

// Ownership: shared Nano/Uno command/control contract header.
// Sync rule: this file must remain byte-for-byte identical between Nano and Uno.
// Scope: shared runtime command tokens, STS acknowledgement tokens, and tunable
// parameter names only (no Uno-only runtime/state details).

// Runtime CLI commands.
static constexpr char CMD_HELP[] = "help";
static constexpr char CMD_GET[] = "get";
static constexpr char CMD_SET[] = "set";
static constexpr char CMD_RESET[] = "reset";
static constexpr char CMD_RESET_DEFAULTS[] = "defaults";
static constexpr char CMD_EMIT[] = "emit";
static constexpr char CMD_EMIT_META[] = "meta";
static constexpr char CMD_EMIT_STARTUP[] = "startup";

// Structured tunables command acknowledgements carried in STS,OK,... replies.
static constexpr char STS_TUNABLES_GET[] = "get";
static constexpr char STS_TUNABLES_SET[] = "set";
static constexpr char STS_TUNABLES_RESET[] = "reset";
static constexpr char STS_EMIT_META[] = "emit,meta";
static constexpr char STS_EMIT_STARTUP[] = "emit,startup";

// Runtime tunable names. These must match namespace Tunables.
static constexpr char PARAM_PPS_FAST_SHIFT[] = "ppsFastShift";
static constexpr char PARAM_PPS_SLOW_SHIFT[] = "ppsSlowShift";
static constexpr char PARAM_PPS_BLEND_LO_PPM[] = "ppsBlendLoPpm";
static constexpr char PARAM_PPS_BLEND_HI_PPM[] = "ppsBlendHiPpm";
static constexpr char PARAM_PPS_LOCK_R_PPM[] = "ppsLockRppm";
static constexpr char PARAM_PPS_LOCK_MAD_TICKS[] = "ppsLockMadTicks";
static constexpr char PARAM_PPS_UNLOCK_R_PPM[] = "ppsUnlockRppm";
static constexpr char PARAM_PPS_UNLOCK_MAD_TICKS[] = "ppsUnlockMadTicks";
static constexpr char PARAM_PPS_LOCK_COUNT[] = "ppsLockCount";
static constexpr char PARAM_PPS_UNLOCK_COUNT[] = "ppsUnlockCount";
static constexpr char PARAM_PPS_HOLDOVER_MS[] = "ppsHoldoverMs";
static constexpr char PARAM_PPS_STALE_MS[] = "ppsStaleMs";
static constexpr char PARAM_PPS_ISR_STALE_MS[] = "ppsIsrStaleMs";
static constexpr char PARAM_PPS_CFG_REEMIT_DELAY_MS[] = "ppsCfgReemitDelayMs";
static constexpr char PARAM_PPS_ACQUIRE_MIN_MS[] = "ppsAcquireMinMs";
static constexpr char PARAM_PPS_METROLOGY_GRACE_MS[] = "ppsMetrologyGraceMs";
