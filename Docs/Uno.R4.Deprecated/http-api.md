# HTTP API

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Registered endpoints

The route registrations in `Uno.R4.Deprecated/src/HttpServer.cpp` define the available interface.

| Path                 | Method    | Purpose                                                |
| -------------------- | --------- | ------------------------------------------------------ |
| `/`                  | GET       | Home page with configuration and log-file links        |
| `/wifi`              | GET, POST | Wi-Fi credential portal                                |
| `/uno`               | GET, POST | View/save UNO logging and OLED half-lives              |
| `/nano`              | GET, POST | Asynchronous Nano tunables proxy/status                |
| `/reset`             | GET, POST | Confirmation page and UNO settings/Wi-Fi factory reset |
| `/nano-reset`        | GET, POST | Confirmation page and queued Nano defaults reset       |
| `/logfiles`          | GET       | SD file listing                                        |
| `/download?file=...` | GET       | Download a selected SD file                            |
| `/force_reconnect`   | GET       | Schedule reconnection; returns `202 Accepted`          |

`/json`, `/stats`, **/stats.json** and `/log` are not registered and return `404 Not Found`. Configure logging through `/uno`. The home page does not show a latest measurement.

## OLED half-life configuration

`POST /uno` accepts `oledShortMinutes` (default `5`, range 1–30) and `oledLongMinutes` (default `60`, range 15–360). Values must be unsigned whole decimal minutes, with long at least twice short. A missing field keeps its current value; validation uses the resulting pair. Invalid or truncated numbers, out-of-range values, and invalid pairs return `400 Bad Request` without saving or applying any settings. A successful save redirects to `/uno?saved=1`.

The page provides both inputs and persists them in EEPROM. Changing either horizon reinitializes both display EWMAs; saving unchanged horizons retains their state. OLED-only changes leave capture and the running logger unchanged, including when the form also submits unchanged logging fields. These parameters do not change raw PCPS/PCSW formats. Factory reset restores 5 minutes / 60 minutes.

## Retained export implementation

JSON emission and logging-page helper code remain in the source, but have no registered routes. Their presence does not make them accessible over HTTP. In particular, sensor/SD health counters in the JSON helper are not currently a public health API.

The retained JSON helper now describes the v3 capture contract, latest CSW and CPS values, and sensor/SD health. It has no registered route.
