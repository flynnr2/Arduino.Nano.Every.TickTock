#pragma once

#include "Config.h"

#include <Arduino.h>
#include <stddef.h>

enum class TunableCliType : uint8_t {
  Unsigned,
  Enum,
};

struct TunableDescriptor {
  const char* cliName;
  const char* deprecatedCliAlias;
  TunableCliType type;
  const char* validationText;
  const char* exampleText;
  const char* helpText;
  void (*printCurrent)(Print& out);
  bool (*parseAndSet)(const char* value, bool& tuningChanged, bool& headerPending);
  void (*writeToConfig)(TunableConfig& cfg);
  void (*applyFromConfig)(const TunableConfig& cfg);
};

const TunableDescriptor* tunableRegistryBegin();
const TunableDescriptor* tunableRegistryEnd();
size_t tunableRegistryCount();
const TunableDescriptor* findTunableDescriptor(const char* cliName);

const char* tunableTypeName(TunableCliType type);
