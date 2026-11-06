#pragma once
#include "WiFiCompat.h"
#include <Arduino.h>

namespace ArduinoHttpServer {

enum class Method { GET, POST, UNKNOWN };
enum class ParseResult { Ok, Timeout, Malformed, UriTooLong, BodyTooLarge };

class Request {
public:
  Method method() const { return method_; }
  const char* path() const { return path_; }
  const char* query() const { return query_; }
  const char* body() const { return body_; }

private:
  static constexpr size_t MAX_PATH_LEN = 96;
  static constexpr size_t MAX_QUERY_LEN = 256;
  static constexpr size_t MAX_BODY_LEN = 512;
  Method method_ = Method::UNKNOWN;
  char path_[MAX_PATH_LEN + 1] = {0};
  char query_[MAX_QUERY_LEN + 1] = {0};
  char body_[MAX_BODY_LEN + 1] = {0};

  friend ParseResult parseRequest(WiFiClient& client, Request& request);
};

class Response {
public:
  explicit Response(WiFiClient& client) : client_(client) {}

  void setStatusCode(int statusCode);
  void setStatusCode(const __FlashStringHelper* status);
  void setStatusCode(const char* status);

  void setHeader(const __FlashStringHelper* key, const __FlashStringHelper* value);
  void setHeader(const __FlashStringHelper* key, const char* value);
  void setHeader(const char* key, const __FlashStringHelper* value);
  void setHeader(const char* key, const char* value);

  void beginBody(size_t length = 0);
  size_t write(const uint8_t* data, size_t len);
  size_t write_P(PGM_P data, size_t len);

  void print(const __FlashStringHelper* val) { sendHeaders(); client_.print(val); }
  void print(const char* val) { sendHeaders(); client_.print(val); }
  void print(int val) { sendHeaders(); client_.print(val); }
  void print(unsigned int val) { sendHeaders(); client_.print(val); }
  void print(long val) { sendHeaders(); client_.print(val); }
  void print(unsigned long val) { sendHeaders(); client_.print(val); }
  void print(float val) { sendHeaders(); client_.print(val); }
  void print(float val, int digits) { sendHeaders(); client_.print(val, digits); }
  void print(double val) { sendHeaders(); client_.print(val); }
  void print(double val, int digits) { sendHeaders(); client_.print(val, digits); }

  void println(const __FlashStringHelper* val) { sendHeaders(); client_.println(val); }
  void println(const char* val) { sendHeaders(); client_.println(val); }
  void println(int val) { sendHeaders(); client_.println(val); }
  void println(unsigned int val) { sendHeaders(); client_.println(val); }
  void println(long val) { sendHeaders(); client_.println(val); }
  void println(unsigned long val) { sendHeaders(); client_.println(val); }
  void println(float val) { sendHeaders(); client_.println(val); }
  void println(float val, int digits) { sendHeaders(); client_.println(val, digits); }
  void println(double val) { sendHeaders(); client_.println(val); }
  void println(double val, int digits) { sendHeaders(); client_.println(val, digits); }

private:
  static constexpr uint8_t MAX_HEADERS = 10;
  static constexpr uint8_t MAX_HEADER_KEY_LEN = 24;
  static constexpr uint8_t MAX_HEADER_VALUE_LEN = 64;

  void sendHeaders();

  struct HeaderSlot {
    char key[MAX_HEADER_KEY_LEN];
    char value[MAX_HEADER_VALUE_LEN];
  };

  WiFiClient& client_;
  char statusCode_[32] = "200 OK";
  HeaderSlot headers_[MAX_HEADERS] = {};
  uint8_t headerCount_ = 0;
  bool headersSent_ = false;
  bool contentLengthSet_ = false;
  bool connectionSet_ = false;
};

using Handler = void (*)(Request&, Response&);

class Server {
public:
  explicit Server(WiFiServer& server) : server_(server) {}
  void begin();
  void stop();
  void poll();
  void on(Method method, const char* path, Handler handler);
  void onNotFound(Handler handler) { notFoundHandler_ = handler; }

private:
  static constexpr uint8_t MAX_ROUTES = 16;
  static constexpr uint32_t HTTP_ACCEPT_GRACE_MS = 40;

  struct Route { Method method; const char* path; Handler handler; };
  WiFiServer& server_;
  Route routes_[MAX_ROUTES];
  uint8_t routeCount_ = 0;
  Handler notFoundHandler_ = nullptr;
};

} // namespace ArduinoHttpServer
