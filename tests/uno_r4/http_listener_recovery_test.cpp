#include "../../Uno.R4.Deprecated/src/HttpServer.h"

#include <cstdio>

using HttpServer::detail::ListenerRecovery;
using HttpServer::detail::LifecycleAction;
using HttpServer::detail::ListenerState;

static int failures = 0;

static void check(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    ++failures;
  }
}

int main() {
  constexpr uint32_t stableMs = 350;
  constexpr uint32_t backoffMs = 500;
  ListenerRecovery recovery;

  // This drives the exact recovery branch used by serviceLifecycle. The old
  // implementation stored zero after the first inactive transition, so every
  // pass below falsely observed a generation change and restarted the timer.
  check(recovery.service(0, false, 0, stableMs, backoffMs) == LifecycleAction::NetworkNotReady,
        "initial outage latches listener inactive");
  check(recovery.service(10, true, 7, stableMs, backoffMs) == LifecycleAction::NetworkReadyAfterOutage,
        "network recovery begins a stable wait");
  for (uint32_t now = 100; now < 350; now += 25) {
    check(recovery.service(now, true, 7, stableMs, backoffMs) == LifecycleAction::None,
          "same generation remains quiet during stable wait");
  }
  check(!recovery.generationChanged(7), "inactive listener compares recovery target, not zero active generation");
  check(recovery.stableSinceMs == 10, "repeat preserves stability timer");
  check(recovery.service(359, true, 7, stableMs, backoffMs) == LifecycleAction::None,
        "stability period has not elapsed");
  check(recovery.service(360, true, 7, stableMs, backoffMs) == LifecycleAction::None,
        "stability period enters restart backoff once");
  check(recovery.service(400, true, 7, stableMs, backoffMs) == LifecycleAction::None,
        "same generation remains quiet during restart backoff");
  check(recovery.stateSinceMs == 360, "repeat preserves restart backoff");
  // An outage observed during start backoff stays quiet until readiness returns,
  // then starts a fresh stable period even if the network generation is unchanged.
  check(recovery.service(401, false, 7, stableMs, backoffMs) == LifecycleAction::None,
        "outage entry does not churn an inactive backoff");
  check(recovery.service(440, true, 7, stableMs, backoffMs) == LifecycleAction::NetworkReadyAfterOutage,
        "outage recovery resets stability period");
  check(recovery.state == ListenerState::WaitingStable, "outage recovery returns to stable wait");
  check(recovery.stableSinceMs == 440, "outage recovery records readiness time");

  check(recovery.service(500, true, 8, stableMs, backoffMs) == LifecycleAction::GenerationChanged,
        "new network generation is detected while recovering");
  check(recovery.recoveryGeneration == 8, "new recovery target is recorded");
  check(recovery.stableSinceMs == 500, "changed generation resets stability timer");

  recovery.markStarted(8, 1300);
  check(recovery.beginRecovery(8, 1400, true), "outage or force reconnect resets recovery");
  check(recovery.stableSinceMs == 1400, "forced recovery records a fresh stable period");
  check(recovery.state == ListenerState::WaitingStable, "forced recovery waits for stability");

  ListenerRecovery backoffRecovery;
  backoffRecovery.service(0, false, 7, stableMs, backoffMs);
  backoffRecovery.service(0, true, 7, stableMs, backoffMs);
  backoffRecovery.service(350, true, 7, stableMs, backoffMs);
  check(backoffRecovery.service(849, true, 7, stableMs, backoffMs) == LifecycleAction::None,
        "restart backoff has not elapsed");
  check(backoffRecovery.service(850, true, 7, stableMs, backoffMs) == LifecycleAction::RestartDue,
        "restart backoff elapses once");

  ListenerRecovery wrappingRecovery;
  const uint32_t beforeWrap = 0xFFFFFFF0u;
  wrappingRecovery.service(beforeWrap, false, 9, stableMs, backoffMs);
  wrappingRecovery.service(beforeWrap, true, 9, stableMs, backoffMs);
  check(wrappingRecovery.service(beforeWrap + 349u, true, 9, stableMs, backoffMs) == LifecycleAction::None,
        "stability wait remains bounded before millis wrap");
  check(wrappingRecovery.service(beforeWrap + 350u, true, 9, stableMs, backoffMs) == LifecycleAction::None &&
        wrappingRecovery.state == ListenerState::Starting,
        "stability wait expires correctly across millis wrap");

  ListenerRecovery initialReady;
  check(initialReady.service(0, true, 0, stableMs, backoffMs) == LifecycleAction::None &&
        initialReady.state == ListenerState::WaitingStable,
        "ready generation zero begins recovery from the down state");

  if (failures == 0) {
    std::puts("http listener recovery tests passed");
  }
  return failures == 0 ? 0 : 1;
}
