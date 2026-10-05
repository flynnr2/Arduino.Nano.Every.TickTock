#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

#include "csv_formatter_under_test.inc"

template <typename T, typename Formatter>
void checkAppend(T value, Formatter format) {
  const std::string digits = std::to_string(value);
  for (const std::string prefix : {std::string(), std::string("CSW,")}) {
    const std::string expected = prefix + digits;
    // Include exact fit, insufficient space, and spare space; guard the buffer.
    for (size_t capacity = prefix.size() + 1; capacity <= expected.size() + 2;
         ++capacity) {
      char storage[32];
      std::memset(storage, '#', sizeof(storage));
      char* out = storage + 1;
      std::memcpy(out, prefix.c_str(), prefix.size() + 1);
      size_t pos = prefix.size();
      const bool ok = format(out, capacity, pos, value);
      assert(ok == (capacity > expected.size()));
      assert(pos < capacity);
      assert(storage[0] == '#');
      for (size_t i = capacity + 1; i < sizeof(storage); ++i)
        assert(storage[i] == '#');
      if (ok) {
        assert(pos == expected.size());
        assert(std::string(out) == expected);
      }
    }
  }
}

int main() {
  for (uint32_t v = 0; v <= UINT16_MAX; ++v)
    checkAppend(static_cast<uint16_t>(v), appendU16);
  for (uint32_t power = 10; power <= 1000000000U; power *= 10) {
    for (uint32_t v : {power - 1, power, power + 1})
      checkAppend(v, appendU32);
    if (power == 1000000000U) break;
  }
  uint32_t value = 1;
  for (unsigned i = 0; i < 10000; ++i) {
    value = value * 1664525U + 1013904223U;
    checkAppend(value, appendU32);
  }
  checkAppend(UINT32_MAX, appendU32);
  size_t pos = 0;
  assert(!appendU32(nullptr, 0, pos, UINT32_MAX));
  char out = '#';
  assert(!appendU32(&out, 0, pos, 0));
  assert(out == '#' && pos == 0);
}
