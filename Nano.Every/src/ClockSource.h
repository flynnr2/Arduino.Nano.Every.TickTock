#pragma once

// Boot-time-only main clock configuration hook.
// This must run before serial/timer initialization and must not be invoked
// again after startup completes.
void configureMainClockIfConfigured();
