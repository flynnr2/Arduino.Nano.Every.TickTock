#include "ArduinoHttpServer.h"
#include <string.h>
#include <strings.h>
#include <avr/pgmspace.h>
#include <cstdio>
#include <ctype.h>

namespace ArduinoHttpServer {

static ParseResult timedReadLine(WiFiClient& client, char* out, size_t outLen, unsigned long timeoutMs) {
  if (!out || outLen == 0) return ParseResult::Malformed;
  size_t used = 0;
  out[0] = '\0';
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (client.available()) {
      char c = (char)client.read();
      if (c == '\r') continue;
      if (c == '\n') {
        out[used] = '\0';
        return ParseResult::Ok;
      }
      if (used + 1 >= outLen) return ParseResult::UriTooLong;
      out[used++] = c;
      out[used] = '\0';
    }
    delay(1);
  }
  return ParseResult::Timeout;
}

static bool timedReadBody(WiFiClient& client, char* out, size_t outLen, size_t contentLength, unsigned long timeoutMs) {
  if (!out || outLen == 0 || contentLength + 1 > outLen) return false;
  size_t used = 0;
  out[0] = '\0';
  unsigned long start = millis();
  while (used < contentLength && millis() - start < timeoutMs) {
    while (client.available() && used < contentLength) {
      out[used++] = char(client.read());
    }
    if (used >= contentLength) break;
    delay(1);
  }
  out[used] = '\0';
  return used == contentLength;
}

static void trimInPlace(char* s) {
  if (!s) return;
  char* start = s;
  while (*start && isspace((unsigned char)*start)) ++start;
  char* end = start + strlen(start);
  while (end > start && isspace((unsigned char)*(end - 1))) --end;
  const size_t len = static_cast<size_t>(end - start);
  if (start != s) {
    memmove(s, start, len);
  }
  s[len] = '\0';
}

static void copyClamped(char* dest, size_t destSize, const char* src) {
  if (!dest || destSize == 0) return;
  if (!src) {
    dest[0] = '\0';
    return;
  }
  snprintf(dest, destSize, "%s", src);
}

static void copyFlashString(char* dest, size_t destSize, const __FlashStringHelper* src) {
  if (!dest || destSize == 0) return;
  if (!src) {
    dest[0] = '\0';
    return;
  }
  const char* p = reinterpret_cast<const char*>(src);
  size_t i = 0;
  while (i + 1 < destSize) {
    char c = pgm_read_byte(p++);
    if (c == '\0') break;
    dest[i++] = c;
  }
  dest[i] = '\0';
}

ParseResult parseRequest(WiFiClient& client, Request& request) {
  char requestLine[320] = {0};
  ParseResult lineResult = timedReadLine(client, requestLine, sizeof(requestLine), 150);
  if (lineResult != ParseResult::Ok) return lineResult;
  trimInPlace(requestLine);
  if (requestLine[0] == '\0') return ParseResult::Malformed;

  char* firstSpace = strchr(requestLine, ' ');
  if (!firstSpace) return ParseResult::Malformed;
  char* secondSpace = strchr(firstSpace + 1, ' ');
  if (!secondSpace) return ParseResult::Malformed;

  const char* line = requestLine;
  const int methodLen = static_cast<int>(firstSpace - requestLine);
  if (methodLen == 3 && strncmp(line, "GET", 3) == 0) request.method_ = Method::GET;
  else if (methodLen == 4 && strncmp(line, "POST", 4) == 0) request.method_ = Method::POST;
  else request.method_ = Method::UNKNOWN;

  char* urlStart = firstSpace + 1;
  char saved = *secondSpace;
  *secondSpace = '\0';
  char* qPos = strchr(urlStart, '?');
  if (qPos) {
    *qPos = '\0';
    copyClamped(request.path_, sizeof(request.path_), urlStart);
    copyClamped(request.query_, sizeof(request.query_), qPos + 1);
  } else {
    copyClamped(request.path_, sizeof(request.path_), urlStart);
    request.query_[0] = '\0';
  }
  *secondSpace = saved;

  size_t contentLength = 0;
  char headerLine[192] = {0};
  for (uint8_t i = 0; i < 24; ++i) {
    ParseResult headerResult = timedReadLine(client, headerLine, sizeof(headerLine), 120);
    if (headerResult == ParseResult::UriTooLong) return ParseResult::Malformed;
    if (headerResult != ParseResult::Ok) return headerResult;
    trimInPlace(headerLine);
    if (headerLine[0] == '\0') break;
    char* colon = strchr(headerLine, ':');
    if (!colon) continue;
    *colon = '\0';
    char* key = headerLine;
    char* value = colon + 1;
    trimInPlace(key);
    trimInPlace(value);
    for (char* p = key; *p; ++p) *p = (char)tolower((unsigned char)*p);
    if (strcmp(key, "content-length") == 0) {
      contentLength = (size_t)strtoul(value, nullptr, 10);
      if (contentLength > 512) return ParseResult::BodyTooLarge;
    }
  }

  if (contentLength > Request::MAX_BODY_LEN) return ParseResult::BodyTooLarge;
  request.body_[0] = '\0';
  if (contentLength > 0 && !timedReadBody(client, request.body_, sizeof(request.body_), contentLength, 200)) {
    return ParseResult::Timeout;
  }

  return ParseResult::Ok;
}

void Response::setStatusCode(int statusCode) {
  switch (statusCode) {
    case 200: snprintf(statusCode_, sizeof(statusCode_), "200 OK"); break;
    case 404: snprintf(statusCode_, sizeof(statusCode_), "404 Not Found"); break;
    default: snprintf(statusCode_, sizeof(statusCode_), "%d", statusCode); break;
  }
}

void Response::setStatusCode(const __FlashStringHelper* status) {
  if (!status) return;
  copyFlashString(statusCode_, sizeof(statusCode_), status);
}

void Response::setStatusCode(const char* status) {
  copyClamped(statusCode_, sizeof(statusCode_), status);
}

void Response::setHeader(const __FlashStringHelper* key, const __FlashStringHelper* value) {
  if (headerCount_ >= MAX_HEADERS) return;
  HeaderSlot& header = headers_[headerCount_];
  copyFlashString(header.key, sizeof(header.key), key);
  copyFlashString(header.value, sizeof(header.value), value);
  if (strcasecmp(header.key, "Content-Length") == 0) {
    contentLengthSet_ = true;
  } else if (strcasecmp(header.key, "Connection") == 0) {
    connectionSet_ = true;
  }
  headerCount_++;
}

void Response::setHeader(const __FlashStringHelper* key, const char* value) {
  if (headerCount_ >= MAX_HEADERS) return;
  HeaderSlot& header = headers_[headerCount_];
  copyFlashString(header.key, sizeof(header.key), key);
  copyClamped(header.value, sizeof(header.value), value);
  if (strcasecmp(header.key, "Content-Length") == 0) {
    contentLengthSet_ = true;
  } else if (strcasecmp(header.key, "Connection") == 0) {
    connectionSet_ = true;
  }
  headerCount_++;
}

void Response::setHeader(const char* key, const __FlashStringHelper* value) {
  if (headerCount_ >= MAX_HEADERS) return;
  HeaderSlot& header = headers_[headerCount_];
  copyClamped(header.key, sizeof(header.key), key);
  copyFlashString(header.value, sizeof(header.value), value);
  if (strcasecmp(header.key, "Content-Length") == 0) {
    contentLengthSet_ = true;
  } else if (strcasecmp(header.key, "Connection") == 0) {
    connectionSet_ = true;
  }
  headerCount_++;
}

void Response::setHeader(const char* key, const char* value) {
  if (headerCount_ >= MAX_HEADERS) return;
  HeaderSlot& header = headers_[headerCount_];
  copyClamped(header.key, sizeof(header.key), key);
  copyClamped(header.value, sizeof(header.value), value);
  if (strcasecmp(header.key, "Content-Length") == 0) {
    contentLengthSet_ = true;
  } else if (strcasecmp(header.key, "Connection") == 0) {
    connectionSet_ = true;
  }
  headerCount_++;
}

void Response::sendHeaders() {
  if (headersSent_) return;
  headersSent_ = true;

  if (!connectionSet_) {
    setHeader(F("Connection"), F("close"));
  }

  client_.print(F("HTTP/1.1 "));
  client_.println(statusCode_);
  for (uint8_t i = 0; i < headerCount_; i++) {
    client_.print(headers_[i].key);
    client_.print(F(": "));
    client_.println(headers_[i].value);
  }
  client_.println();
}

void Response::beginBody(size_t length) {
  if (length > 0 && !contentLengthSet_) {
    char contentLength[16] = {0};
    snprintf(contentLength, sizeof(contentLength), "%lu", static_cast<unsigned long>(length));
    setHeader(F("Content-Length"), contentLength);
  }
  sendHeaders();
}

size_t Response::write(const uint8_t* data, size_t len) {
  sendHeaders();
  return client_.write(data, len);
}

size_t Response::write_P(PGM_P data, size_t len) {
  sendHeaders();

  static constexpr size_t CHUNK_SIZE = 96;
  uint8_t buffer[CHUNK_SIZE];

  size_t totalWritten = 0;
  size_t offset = 0;

  while (offset < len) {
    const size_t remaining = len - offset;
    const size_t n = remaining < CHUNK_SIZE ? remaining : CHUNK_SIZE;

    for (size_t i = 0; i < n; ++i) {
      buffer[i] = pgm_read_byte(data + offset + i);
    }

    const size_t written = client_.write(buffer, n);
    totalWritten += written;
    offset += written;

    if (written == 0 || written < n) {
      break;
    }
  }

  return totalWritten;
}

void Server::begin() {
  server_.begin();
}

void Server::stop() {
  server_.end();
}

void Server::on(Method method, const char* path, Handler handler) {
  if (routeCount_ >= MAX_ROUTES) return;
  routes_[routeCount_++] = {method, path, handler};
}

void Server::poll() {
  WiFiClient client = server_.available();
  if (!client) return;

  const unsigned long waitStart = millis();
  while (!client.available() && (millis() - waitStart) < HTTP_ACCEPT_GRACE_MS) {
    delay(1);
  }
  if (!client.available()) {
    client.stop();
    return;
  }

  Request request;
  ParseResult parseResult = parseRequest(client, request);
  if (parseResult != ParseResult::Ok) {
    Response response(client);
    if (parseResult == ParseResult::UriTooLong) {
      response.setStatusCode(F("414 Request-URI Too Long"));
      response.setHeader(F("Content-Type"), F("text/plain"));
      response.setHeader(F("Connection"), F("close"));
      response.beginBody();
      response.print(F("Request-URI Too Long"));
    } else if (parseResult == ParseResult::BodyTooLarge) {
      response.setStatusCode(F("413 Payload Too Large"));
      response.setHeader(F("Content-Type"), F("text/plain"));
      response.setHeader(F("Connection"), F("close"));
      response.beginBody();
      response.print(F("Payload Too Large"));
    } else if (parseResult == ParseResult::Malformed) {
      response.setStatusCode(F("400 Bad Request"));
      response.setHeader(F("Content-Type"), F("text/plain"));
      response.setHeader(F("Connection"), F("close"));
      response.beginBody();
      response.print(F("Bad Request"));
    }
    client.stop();
    return;
  }

  Response response(client);

  Handler handler = nullptr;
  for (uint8_t i = 0; i < routeCount_; i++) {
    if (routes_[i].method == request.method() && strcmp(request.path(), routes_[i].path) == 0) {
      handler = routes_[i].handler;
      break;
    }
  }

  if (handler) {
    handler(request, response);
  } else if (notFoundHandler_) {
    notFoundHandler_(request, response);
  }

  client.stop();
}

} // namespace ArduinoHttpServer
