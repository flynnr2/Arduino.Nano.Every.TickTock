#pragma once
#include "Config.h"

namespace MemoryMonitor {
  void poll();
  void serviceBlink();
  int freeRam();
  int minFreeRam();
}
