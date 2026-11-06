#pragma once

#include "Config.h"
#include "FreqDiscipliner.h"
#include <stdint.h>

class DisciplinedTime {
public:
  enum class ExportMode : uint8_t {
    NOMINAL = 0,
    BLEND_TRACK = 1,
    SLOW_TRACK = 2,
    SLOW_GRACE = 3,
    SLOW_HOLDOVER = 4,
  };

  void begin(uint32_t f_cpu_nominal);
  void sync(const FreqDiscipliner& discipliner, bool pps_valid, uint32_t now_ms);

  uint32_t ticksPerSecond() const;
  ExportMode exportMode() const { return export_mode_; }
  static const char* exportModeName(ExportMode mode);

private:
#if defined(MAIN_CLOCK_HZ)
  static constexpr uint32_t kDefaultFcpu = static_cast<uint32_t>(MAIN_CLOCK_HZ);
#else
  static constexpr uint32_t kDefaultFcpu = 16UL * 1000000UL;
#endif

  uint32_t f_cpu_nominal_ = kDefaultFcpu;
  uint32_t f_hat_ = kDefaultFcpu;
  uint32_t grace_anchor_hz_ = kDefaultFcpu;
  uint32_t grace_start_ms_ = 0;
  FreqDiscipliner::DiscState state_ = FreqDiscipliner::DiscState::FREE_RUN;
  ExportMode export_mode_ = ExportMode::NOMINAL;
  bool has_disciplined_once_ = false;
};
