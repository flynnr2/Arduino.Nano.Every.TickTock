#pragma once

#include "Config.h"

UnoConfig makeDefaultUnoConfig();
UnoConfig getCurrentUnoConfig();

void sanitizeUnoConfig(UnoConfig &cfg);
void applyUnoConfig(const UnoConfig &cfg);

bool loadUnoConfig(UnoConfig &unoOut);
void saveUnoConfig(UnoConfig unoCfg);
void saveFactoryDefaults(UnoConfig &unoOut);
