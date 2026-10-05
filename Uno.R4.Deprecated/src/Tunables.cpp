#include "Config.h"

namespace UnoTunables {
  uint16_t statsWindowSize      = DEFAULT_STATS_WINDOW;
  uint32_t rollingWindowMs      = DEFAULT_ROLLING_MS;
  int32_t  blockJumpUs          = DEFAULT_BLOCK_JUMP_US;

  uint16_t oledShortMinutes = OledRatingConfig::DEFAULT_SHORT_MINUTES;
  uint16_t oledLongMinutes = OledRatingConfig::DEFAULT_LONG_MINUTES;

  bool     logDaily             = LOG_DAILY_DEFAULT;
  bool     logEnabled           = LOG_ENABLED_DEFAULT;
  bool     logAppend            = LOG_APPEND_DEFAULT;
  uint8_t  logStartupPolicy     = LOG_STARTUP_POLICY_DEFAULT;
  char     logBaseName[LOG_FILENAME_LEN] = LOG_FILENAME;
}
