#pragma once
#include <stdint.h>
#include <float.h>

namespace HttpServer {
  namespace detail {
    inline bool jsonFinite(float value) {
      return value == value && value <= FLT_MAX && value >= -FLT_MAX;
    }

    template <typename Response>
    void appendJsonEnvironmentalFields(Response& response,
                                       float temperature,
                                       float humidity,
                                       float pressure) {
      response.print(",\"temperature_C\":");
      if (jsonFinite(temperature)) response.print(temperature, 2);
      else response.print("null");
      response.print(",\"humidity_pct\":");
      if (jsonFinite(humidity)) response.print(humidity, 2);
      else response.print("null");
      response.print(",\"pressure_hPa\":");
      if (jsonFinite(pressure)) response.print(pressure, 2);
      else response.print("null");
    }

    enum class ListenerState : uint8_t { Down = 0, WaitingStable, Starting, Up };
    enum class LifecycleAction : uint8_t {
      None = 0,
      NetworkNotReady,
      NetworkReadyAfterOutage,
      GenerationChanged,
      RestartDue
    };

    // Kept separate from the active listener generation so repeated lifecycle
    // checks do not restart a recovery already targeting the current network.
    struct ListenerRecovery {
      uint32_t activeGeneration = 0;
      uint32_t recoveryGeneration = 0;
      bool active = false;
      ListenerState state = ListenerState::Down;
      bool outageLatched = false;
      uint32_t stateSinceMs = 0;
      uint32_t stableSinceMs = 0;

      bool beginRecovery(uint32_t generation, uint32_t nowMs, bool forceReset) {
        const bool targetChanged = recoveryGeneration != generation;
        const bool transition = forceReset || active || state == ListenerState::Down ||
                                state == ListenerState::Up || targetChanged;
        active = false;
        activeGeneration = 0;
        recoveryGeneration = generation;
        if (transition) {
          state = ListenerState::WaitingStable;
          stateSinceMs = nowMs;
          stableSinceMs = nowMs;
        }
        return transition;
      }

      bool generationChanged(uint32_t generation) const {
        return active ? activeGeneration != generation : recoveryGeneration != generation;
      }

      bool enterStartingIfStable(uint32_t nowMs, uint32_t stableMs) {
        if (state != ListenerState::WaitingStable ||
            (uint32_t)(nowMs - stableSinceMs) < stableMs) {
          return false;
        }
        state = ListenerState::Starting;
        stateSinceMs = nowMs;
        return true;
      }

      bool restartDue(uint32_t nowMs, uint32_t backoffMs) const {
        return state == ListenerState::Starting &&
               (uint32_t)(nowMs - stateSinceMs) >= backoffMs;
      }

      LifecycleAction service(uint32_t nowMs,
                              bool networkReady,
                              uint32_t generation,
                              uint32_t stableMs,
                              uint32_t backoffMs) {
        if (!networkReady) {
          const bool transitioned = beginRecovery(generation, nowMs, false);
          outageLatched = true;
          return transitioned ? LifecycleAction::NetworkNotReady : LifecycleAction::None;
        }

        if (outageLatched) {
          outageLatched = false;
          beginRecovery(generation, nowMs, true);
          return LifecycleAction::NetworkReadyAfterOutage;
        }

        if (generationChanged(generation)) {
          beginRecovery(generation, nowMs, false);
          return LifecycleAction::GenerationChanged;
        }

        if (!active && state == ListenerState::Down) {
          beginRecovery(generation, nowMs, false);
        }
        if (!active) {
          enterStartingIfStable(nowMs, stableMs);
          if (restartDue(nowMs, backoffMs)) {
            return LifecycleAction::RestartDue;
          }
        }
        return LifecycleAction::None;
      }

      void markStarted(uint32_t generation, uint32_t nowMs) {
        activeGeneration = generation;
        recoveryGeneration = generation;
        active = true;
        state = ListenerState::Up;
        stateSinceMs = nowMs;
      }
    };
  }

  void begin();
  void beginRoutesOnce();
  void restartListener();
  void markListenerInactive(const char* reason);
  bool isActive();
  void serviceLifecycle();
  void serviceClients();
}
