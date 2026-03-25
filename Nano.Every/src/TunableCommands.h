#pragma once

void handleGetCommand(char* name);
void handleSetCommand(char* name, char* val, bool& headerPending);
void handleResetCommand(char* action, bool& headerPending);
