#include <iostream>
#include "FreqDiscipliner.h"
int main() {
  FreqDiscipliner f;
  f.reset(16000000);
  unsigned reset, cls, valid, ticks, ms, anomaly, fast, slow;
  while (std::cin >> reset >> cls >> valid >> ticks >> ms >> anomaly >> fast >> slow) {
    Tunables::ppsFastShift = fast;
    Tunables::ppsSlowShift = slow;
    if (reset) f.reset(ticks);
    else f.observe((PpsValidator::SampleClass)cls, valid, ticks, ms, anomaly);
    std::cout << (unsigned)f.state() << ' ' << f.fast() << ' ' << f.slow() << ' '
      << f.applied() << ' ' << f.rPpm() << ' ' << (unsigned)f.lockStreak() << ' '
      << (unsigned)f.unlockStreak() << ' ' << (unsigned)f.transitionStreak() << ' '
      << f.holdoverAgeMs() << ' ' << f.lastGoodSlow() << ' '
      << f.fastErrTicks() << ' ' << f.slowErrTicks() << ' ' << f.appliedErrTicks() << ' '
      << f.fastErrPpm() << ' ' << f.slowErrPpm() << ' ' << f.appliedErrPpm() << ' '
      << f.slowMadTicks() << ' ' << f.appliedMadTicks() << ' ' << f.madTicks() << ' '
      << (unsigned)f.lockPassMask() << ' ' << (unsigned)f.unlockBreachMask() << '\n';
  }
}
