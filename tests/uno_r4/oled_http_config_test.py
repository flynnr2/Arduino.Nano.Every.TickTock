#!/usr/bin/env python3
"""Execute the production /uno handler with lightweight HTTP/config adapters."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'Uno.R4.Deprecated/src/HttpServer.cpp').read_text()
params = source[source.index('static size_t urlDecode('):source.index('static bool parseBool01(')]
# QueryParams is followed by unrelated helpers in some revisions; retain only its class.
params = params[:params.index('\n};', params.index('class QueryParams')) + len('\n};')]
handler = source[source.index('static void handleUnoRequest('):source.index('static void renderResetPage(')]
preamble = r'''
#include <assert.h>
#include <ctype.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <sstream>
#include "Config.h"
#include "Tunables.cpp"
#define F(x) x
using __FlashStringHelper = char;
enum class HttpMethod { GET, POST };
struct HttpRequest {
  HttpMethod verb;
  std::string data;
  HttpMethod method() const { return verb; }
  const char* body() const { return data.c_str(); }
  const char* query() const { return data.c_str(); }
};
struct HttpResponse {
  std::string status;
  std::ostringstream contents;
  void setStatusCode(const char* value) { status = value; }
  void setHeader(const char*, const char*) {}
  template<typename T> void print(T value) { contents << value; }
  template<typename T> void println(T value) { contents << value << '\n'; }
};
unsigned long micros() { return 0; }
void noteHttpRequest() {}
void noteHttpRejected() {}
void sendBusyText(HttpResponse& response, const char* status, const char* body) {
  response.setStatusCode(status); response.print(body);
}
void sendRedirect(HttpResponse& response, const char*) { response.setStatusCode("303 See Other"); }
namespace SDLogger { void logUnoEvent(const char*, const char*) {} }
UnoConfig active;
unsigned saves = 0, applies = 0, loggerApplies = 0;
UnoConfig getCurrentUnoConfig() { return active; }
void sanitizeUnoConfig(UnoConfig& cfg) {
  OledRatingConfig::sanitize(cfg.oledShortMinutes, cfg.oledLongMinutes);
  cfg.logAppend = cfg.logStartupPolicy == LOG_STARTUP_POLICY_APPEND;
}
void applyUnoConfig(const UnoConfig& cfg) { ++applies; active = cfg; }
void saveUnoConfig(const UnoConfig&) { ++saves; }
void applyLoggerConfig(const UnoConfig&) { ++loggerApplies; }
bool parseBool01(const char* text, bool& out) {
  if (strcmp(text, "0") && strcmp(text, "1")) return false;
  out = text[0] == '1'; return true;
}
bool parsePolicyValue(const char* text, uint8_t& out) {
  if (strlen(text) != 1 || *text < '0' || *text > '2') return false;
  out = *text - '0'; return true;
}
'''
test = r'''
int main() {
  active = UnoConfig{};
  active.oledShortMinutes = 5; active.oledLongMinutes = 60;
  active.logEnabled = true; active.logStartupPolicy = LOG_STARTUP_POLICY_OVERWRITE;
  strcpy(active.logBaseName, "pendulum.csv");
  for (const char* bad : {"oledShortMinutes=-1", "oledLongMinutes=361", "oledShortMinutes=65541",
                         "oledShortMinutes=31", "oledLongMinutes=14", "oledShortMinutes=",
                         "oledShortMinutes=5%00junk", "oledShortMinutes=5.5", "oledShortMinutes=%2B5",
                         "oledShortMinutes=00000000000000000000000000000005",
                         "oledShortMinutes=30&oledLongMinutes=59",
                         "oledShortMinutes=5x&logEnabled=0"}) {
    HttpRequest request{HttpMethod::POST, bad}; HttpResponse response;
    handleUnoRequest(request, response);
    assert(response.status == "400 Bad Request");
    assert(saves == 0 && applies == 0 && loggerApplies == 0);
    assert(active.oledShortMinutes == 5 && active.oledLongMinutes == 60 && active.logEnabled);
  }
  HttpRequest request{HttpMethod::POST,
      "oledShortMinutes=10&oledLongMinutes=90&logEnabled=1&logDaily=0&logStartupPolicy=1"};
  HttpResponse response;
  handleUnoRequest(request, response);
  assert(response.status == "303 See Other");
  assert(saves == 1 && applies == 1 && loggerApplies == 0);
  assert(active.oledShortMinutes == 10 && active.oledLongMinutes == 90);
  request.data = "oledLongMinutes=120";
  handleUnoRequest(request, response);
  assert(active.oledShortMinutes == 10 && active.oledLongMinutes == 120);
  assert(loggerApplies == 0);
  request.data = "logEnabled=0";
  handleUnoRequest(request, response);
  assert(!active.logEnabled && loggerApplies == 1);
  request.verb = HttpMethod::GET; request.data = "";
  HttpResponse page;
  handleUnoRequest(request, page);
  assert(page.contents.str().find("name='oledShortMinutes'") != std::string::npos);
  assert(page.contents.str().find("name='oledLongMinutes'") != std::string::npos);
  assert(page.contents.str().find("name='logBaseName'") == std::string::npos);
  assert(page.contents.str().find("value='120'") != std::string::npos);
  puts("OLED /uno HTTP config tests passed");
}
'''
with tempfile.TemporaryDirectory(prefix='oled-http-config-') as temporary:
    code = Path(temporary) / 'handler.cpp'
    executable = Path(temporary) / 'handler'
    code.write_text(preamble + params + handler + test)
    subprocess.run(['c++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(root / 'tests/uno_r4/config_stubs'), '-I', str(root / 'Uno.R4.Deprecated/src'),
                    str(code), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
