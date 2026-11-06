#pragma once

// Uno-only validated session metadata and latest environment readings.
#include <stddef.h>
#include "PendulumProtocol.h"

constexpr size_t CANONICAL_SCHEMA_ID_MAX_LEN =
    sizeof(CANONICAL_SWING_SCHEMA_ID) > sizeof(CANONICAL_PPS_SCHEMA_ID)
      ? sizeof(CANONICAL_SWING_SCHEMA_ID) : sizeof(CANONICAL_PPS_SCHEMA_ID);
constexpr size_t CANONICAL_SCHEMA_PAYLOAD_MAX_LEN =
    sizeof(CANONICAL_SWING_SCHEMA) > sizeof(CANONICAL_PPS_SCHEMA)
      ? sizeof(CANONICAL_SWING_SCHEMA) : sizeof(CANONICAL_PPS_SCHEMA);
constexpr uint32_t SCHEMA_PAYLOAD_HASH_UNSET = 0;

struct NanoSessionConfig {
  uint32_t nominal_hz = 0;
  uint16_t protocol_version = 0;
  char firmware[64] = {0};
  char canonical_swing_schema_id[CANONICAL_SCHEMA_ID_MAX_LEN] = {0};
  char canonical_pps_schema_id[CANONICAL_SCHEMA_ID_MAX_LEN] = {0};
  uint32_t canonical_swing_schema_payload_hash = SCHEMA_PAYLOAD_HASH_UNSET;
  uint32_t canonical_pps_schema_payload_hash = SCHEMA_PAYLOAD_HASH_UNSET;
  char canonical_swing_tag[sizeof(TAG_CSW)] = {0};
  char canonical_pps_tag[sizeof(TAG_CPS)] = {0};
  bool config_received = false;
  bool canonical_swing_received = false;
  bool canonical_pps_received = false;
  bool canonical_swing_schema_received = false;
  bool canonical_pps_schema_received = false;
};

struct PendulumSampleState {
  NanoSessionConfig session = {};
  float temperature_C = 0.0f;
  float humidity_pct = 0.0f;
  float pressure_hPa = 0.0f;
};
