#include "../../Uno.R4.Deprecated/src/HttpServer.h"

#include <cmath>
#include <cstdio>
#include <cstring>

struct ResponseCapture {
  char text[192] = {0};
  size_t length = 0;

  void print(const char* value) {
    const size_t valueLength = std::strlen(value);
    std::memcpy(text + length, value, valueLength);
    length += valueLength;
    text[length] = '\0';
  }

  void print(float value, uint8_t digits) {
    const int written = std::snprintf(text + length, sizeof(text) - length,
                                      "%.*f", (int)digits, value);
    if (written > 0) length += (size_t)written;
  }
};

static int failures = 0;

static void check(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    ++failures;
  }
}

static void emitJsonPath(ResponseCapture& response, int path) {
  char prefix[24] = {0};
  std::snprintf(prefix, sizeof(prefix), "{\"path\":%d", path);
  response.print(prefix);
  HttpServer::detail::appendJsonEnvironmentalFields(
      response, NAN, NAN, NAN);
  response.print(",\"health\":{}}");
}

int main() {
  for (int path = 0; path < 3; ++path) {
    ResponseCapture response;
    emitJsonPath(response, path);
    char expected[96] = {0};
    std::snprintf(expected, sizeof(expected),
                  "{\"path\":%d,\"temperature_C\":null,\"humidity_pct\":null,\"pressure_hPa\":null,\"health\":{}}",
                  path);
    check(std::strcmp(response.text, expected) == 0,
          "JSON path writes null environmental values before health");
  }
  check(!HttpServer::detail::jsonFinite(NAN), "NaN is not a JSON number");
  check(!HttpServer::detail::jsonFinite(INFINITY), "infinity is not a JSON number");
  check(HttpServer::detail::jsonFinite(12.5f), "finite values remain JSON numbers");

  if (failures == 0) std::puts("http JSON emitter tests passed");
  return failures == 0 ? 0 : 1;
}
