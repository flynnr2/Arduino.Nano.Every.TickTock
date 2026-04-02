#include "SerialHelp.h"

#include <Arduino.h>
#include <avr/pgmspace.h>

#include "SerialParser.h"
#include "StringCase.h"
#include "TunableRegistry.h"

#ifndef FPSTR
  #define FPSTR(p) (reinterpret_cast<const __FlashStringHelper *>(p))
#endif

namespace {

struct CmdHelp {
  const char* name_P;     // PROGMEM
  const char* synopsis_P; // PROGMEM
  const char* usage_P;    // PROGMEM
  const char* category_P; // PROGMEM
};

const char CAT_core[]     PROGMEM = "core";
const char CAT_tunables[] PROGMEM = "tunables";

const char H_name[]     PROGMEM = "help";
const char H_syn[]      PROGMEM = "Show help for commands or tunables";
const char H_use[]      PROGMEM = "help [<command>|tunables]";

const char G_name[]     PROGMEM = "get";
const char G_syn[]      PROGMEM = "Read a tunable";
const char G_use[]      PROGMEM = "get <param>";

const char SET_name[]   PROGMEM = "set";
const char SET_syn[]    PROGMEM = "Set a tunable";
const char SET_use[]    PROGMEM = "set <param> <value>";

const char RESET_name[] PROGMEM = "reset";
const char RESET_syn[]  PROGMEM = "Reset firmware defaults into live config and EEPROM";
const char RESET_use[]  PROGMEM = "reset defaults";

const char EMIT_name[]  PROGMEM = "emit";
const char EMIT_syn[]   PROGMEM = "Emit runtime telemetry events";
const char EMIT_use[]   PROGMEM = "emit meta|startup";

const CmdHelp HELP_REGISTRY[] PROGMEM = {
  { H_name, H_syn, H_use, CAT_core },
  { G_name, G_syn, G_use, CAT_tunables },
  { SET_name, SET_syn, SET_use, CAT_tunables },
  { RESET_name, RESET_syn, RESET_use, CAT_tunables },
  { EMIT_name, EMIT_syn, EMIT_use, CAT_core },
};
constexpr uint8_t HELP_N = sizeof(HELP_REGISTRY) / sizeof(HELP_REGISTRY[0]);
constexpr uint8_t MAX_HELP_SUGGESTIONS = 5;

inline void print_P(const char* p) { CMD_SERIAL.print(FPSTR(p)); }
inline void println_P(const char* p) { CMD_SERIAL.println(FPSTR(p)); }

bool equals_ci_P(const char* ram, const char* pgm) {
  if (!ram) return false;
  while (true) {
    char a = *ram++;
    char b = pgm_read_byte(pgm++);
    if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
    if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
    if (a != b) return false;
    if (a == '\0') return true;
  }
}

bool starts_with_ci_P(const char* ram, const char* pgm) {
  if (!ram) return false;
  while (*ram) {
    char a = *ram++;
    char b = pgm_read_byte(pgm++);
    if (b == '\0') return false;
    if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
    if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
    if (a != b) return false;
  }
  return true;
}

void read_entry(uint8_t i, CmdHelp& out) { memcpy_P(&out, &HELP_REGISTRY[i], sizeof(out)); }

void list_commands() {
  CMD_SERIAL.println(F("Commands: name – synopsis"));
  for (uint8_t i = 0; i < HELP_N; ++i) {
    CmdHelp e;
    read_entry(i, e);
    CMD_SERIAL.print(F("  "));
    print_P(e.name_P);
    CMD_SERIAL.print(F(" – "));
    println_P(e.synopsis_P);
  }
  CMD_SERIAL.println(F("Tip: 'help <command>' or 'help tunables'"));
}

bool detail_for(const char* name) {
  for (uint8_t i = 0; i < HELP_N; ++i) {
    CmdHelp e;
    read_entry(i, e);
    if (equals_ci_P(name, e.name_P)) {
      CMD_SERIAL.print(F("name : "));
      println_P(e.name_P);
      CMD_SERIAL.print(F("usage: "));
      println_P(e.usage_P);
      CMD_SERIAL.print(F("desc : "));
      println_P(e.synopsis_P);
      CMD_SERIAL.print(F("cat  : "));
      println_P(e.category_P);
      return true;
    }
  }
  return false;
}

void suggest_similar(const char* name) {
  CMD_SERIAL.println(F("Did you mean:"));
  uint8_t shown = 0;
  for (uint8_t i = 0; i < HELP_N; ++i) {
    CmdHelp e;
    read_entry(i, e);
    if (starts_with_ci_P(name, e.name_P)) {
      CMD_SERIAL.print(F("  "));
      println_P(e.name_P);
      if (++shown >= MAX_HELP_SUGGESTIONS) break;
    }
  }
  if (!shown) CMD_SERIAL.println(F("  (no close matches)"));
}

void show_tunables() {
  CMD_SERIAL.println(F("Tunables (current / example usage)"));
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    CMD_SERIAL.print(F("  "));
    CMD_SERIAL.print(it->cliName);
    CMD_SERIAL.print(F(" ["));
    CMD_SERIAL.print(tunableTypeName(it->type));
    CMD_SERIAL.print(F("]: "));
    it->printCurrent(CMD_SERIAL);
    CMD_SERIAL.print(F("    e.g. `"));
    CMD_SERIAL.print(it->exampleText);
    CMD_SERIAL.print(F("`"));
    if (it->helpText && *it->helpText) {
      CMD_SERIAL.print(F(" ("));
      CMD_SERIAL.print(it->helpText);
      if (it->validationText && *it->validationText) {
        CMD_SERIAL.print(F("; "));
        CMD_SERIAL.print(it->validationText);
      }
      CMD_SERIAL.print(F(")"));
    } else if (it->validationText && *it->validationText) {
      CMD_SERIAL.print(F(" ("));
      CMD_SERIAL.print(it->validationText);
      CMD_SERIAL.print(F(")"));
    }
    CMD_SERIAL.println();
  }
}

} // namespace

void handleHelp(const char* arg1) {
  if (!arg1 || *arg1 == '\0') {
    list_commands();
    return;
  }
  if (equalsIgnoreCaseAscii(arg1, "tunables")) {
    show_tunables();
    return;
  }
  if (!detail_for(arg1)) {
    CMD_SERIAL.print(F("No such command: "));
    CMD_SERIAL.println(arg1);
    suggest_similar(arg1);
  }
}
