// --------------------------------------------------------------
// Nano Every GPS-Disciplined Pendulum Beat Timer with Tick/Tock Analysis
// Optimized for Arduino Nano Every (ATmega4809) with minimal onboard computation
// Performs capture + PPS disciplining + output packaging on-board; deeper analytics can be offloaded downstream
// --------------------------------------------------------------

// Short-term roadmap items are tracked in Docs/TODO.md.

#include "src/EEPROMConfig.h"
#include "src/PendulumProtocol.h"
#include "src/PendulumCore.h"
#include "src/SerialParser.h"

void setup() {
  delay(5000);
  pendulumSetup();
  digitalWrite(ledPin, HIGH);
  delay(5000);
  digitalWrite(ledPin, LOW);
}

void loop() {
  pendulumLoop();
}
