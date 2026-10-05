# GPS/PPS UTC timekeeping with chrony

## Selected design and implementation status

Use **GPS NMEA plus a directly wired PPS input as the Pi's preferred UTC
reference**, with internet NTP retained for fallback and independent comparison.
A local PPS avoids Wi-Fi/network delivery variation and can maintain UTC without
internet access when the GPS supplies valid time. See the
[GPSD timing guide](https://gpsd.gitlab.io/gpsd/gpsd-time-service-howto.html).

This document specifies the agreed deployment. The application installer does
not yet install or configure gpsd/chrony. The application provides read-only
chrony health and gpsd receiver diagnostics; independent GPS validity and
event-to-UTC mapping remain separate work. The instructions below are
commissioning work for the physical Pi, not evidence of an already tested
deployment.

## Connections and responsibilities

- GPS TX/RX connect only to the Pi UART, GPIO15/RX and GPIO14/TX respectively.
  gpsd owns this UART; the application must consume GPS information from gpsd
  rather than open a competing serial reader.
- The original 3.3 V GPS PPS branches directly to Pi **GPIO17 / physical pin 11**
  and to AHCT125 gate 1. The gate's 5 V output connects only to Nano A3/D17.
  Grounds are common. Follow the exact [wiring schedule](WIRING.md#shared-pps-for-pi-utc-and-nano-capture).
- Linux timestamps the Pi's PPS rising edge. NMEA supplies the UTC date and
  second; chrony associates these and disciplines the Pi system clock.
- Nano Every continues hardware capture of PPS and optical edges. It performs
  no NMEA parsing or Pi clock-setting work. Its USB message arrival is not a
  precision reference for the Pi clock.

The kernel supports PPS timestamping independently of application scheduling;
USB delivery introduces additional latency and jitter. See
[Linux PPS documentation](https://docs.kernel.org/driver-api/pps.html).

## Operating policy

Use GPS/PPS when valid and selectable; retain several network NTP sources for
fallback and comparison. Preference must not force an invalid GPS source to be
trusted. If GPS timing is unavailable but network time is usable, chrony can
select network time. If neither is usable, the Pi continues with its estimated
clock rate in holdover; UTC accuracy then degrades with time. Report that state
as having no current external reference, rather than claiming GPS lock merely
because a daemon is running or a pulse is present.

Recording must continue regardless of UTC availability. Once external time
returns, chrony reacquires it without resetting the Nano. A Pi reboot still
loses measurements during downtime; do not buffer, invent or backfill them.
The Nano's capture readiness and the Pi's UTC quality are separate states.

## Pi commissioning

1. Apply the [boot settings](WIRING.md#raspberry-pi-os-configuration), including
   `dtoverlay=pps-gpio,gpiopin=17`. Reboot and identify the GPIO PPS device,
   expected to be `/dev/pps0` when it is the only PPS device. Confirm its source
   before configuring chrony; another PPS device could change numbering.
2. Install the OS packages separately on the Pi:

   ```sh
   sudo apt update
   sudo apt install gpsd gpsd-clients chrony pps-tools
   ```

3. Configure the distribution's gpsd service for the GPS UART `/dev/serial0`,
   with continuous polling (`-n`) so timing does not depend on a dashboard
   client being connected. Disable GPS auto-discovery of the Nano USB port.
   Inspect the installed service/defaults to confirm the effective device list
   contains only the intended GPS, and confirm the module's baud rate (default
   9600). Nano acquisition uses its USB by-id device at 115200 baud instead.
4. Make chrony the sole system-clock synchronisation service. Preserve suitable
   distribution network pools for fallback. Configure a gpsd NMEA reference
   named `NMEA` with `noselect`, and a kernel PPS reference locked to it. This
   associates the PPS with GPS seconds while keeping delayed serial sentences
   out of direct clock selection. The PPS reference has this form:

   ```conf
   refclock PPS /dev/pps0 refid GPS lock NMEA prefer
   ```

   The NMEA reference must also be configured: this line is **not a complete
   chrony configuration**. Use a verified gpsd interface supported by the
   installed versions: SHM unit 0 for the sole receiver's NMEA, or the NMEA SOCK
   interface available since gpsd 3.25. With SOCK, chrony creates the socket and
   must start before gpsd. Verify socket naming or SHM allocation on the target.
   Measure NMEA latency and configure its offset so the pulse is paired with
   the correct second; never copy an example offset as a calibration. Record
   the resulting configuration and GPS/OS/package versions with acceptance
   evidence. The [chrony reference](https://chrony-project.org/doc/4.6/chrony.conf.html)
   describes `PPS`, `SHM`, `SOCK`, `lock`, `noselect` and `prefer`.
5. Allow initial clock correction during commissioning/startup, then use gradual
   corrections during normal recording. Review the installed `makestep` policy;
   avoid recurring forced clock steps during a live run. Do not enable chrony's
   `local` reference mode to disguise absence of external synchronisation.
6. Complete the [hardware acceptance checks](HARDWARE_TESTS.md), including
   correct date/second association, GPS loss, network loss, recovery and reboot.
   The installer does not yet automate this commissioning or service ordering.

## Verification and monitoring

On the Pi, useful diagnostics are:

```sh
sudo ppstest /dev/pps0
cgps -s
chronyc sources -v
chronyc sourcestats -v
chronyc tracking
```

Stop the continuous tools with Ctrl+C. Check that PPS assert sequence advances
once per second, GPS reports valid UTC, and chrony actually selects the intended
source with sensible offset/uncertainty. PPS presence alone does not establish
valid UTC. Compare against independent network time to detect an incorrect date
or whole-second association before relying on GPS offline.

The dashboard's Pi UTC health panel reads `chronyc -n -c tracking` and `sources`
in a background worker every ten seconds, with a one-second timeout per command.
It requires agreement between the selected source and tracking report, rather
than inferring synchronisation from a running daemon. It reports selected
GPS/PPS or NTP source, reference age, last offset and chrony's error estimate at
collection time. This estimate is conditional on the upstream clock being
correct; it is not an independent accuracy guarantee.

Another background worker polls gpsd on `127.0.0.1:2947` for fix mode,
optional fix status, satellites used and in view, HDOP and receiver report ages.
It waits up to seven seconds for a complete streamed SKY report because a
one-shot POLL can contain only the latest partial satellite update, then pauses
briefly before sampling again. It requires one active receiver. TPV report age
above 30 seconds clears fix claims. A timestamped SKY report must be at most
30 seconds old to expose satellite/HDOP values. When gpsd omits the optional
SKY timestamp, counts from a newly received SKY report can appear alongside
a fresh timestamped TPV report; their individual age remains unknown. An empty
satellite array without nSat is not interpreted as zero satellites in view.
Missing age is not an independent freshness check. gpsd failures do not mask
chrony status.
The web page reads these cached results without opening the GPS UART. Both
collectors become unavailable when their cached checks exceed 30 seconds.
They run within acquisition in serial, replay and demo modes; these diagnostics
describe the host OS, not simulated GPS hardware. They do not
install services, change the clock or independently certify GPS UTC. A configured
GPS/PPS reference name and a receiver fix are not independent accuracy checks.
Direct GPS validity and Pi PPS freshness remain commissioning checks using the
OS diagnostics above.
Nano PPS state remains a separate display. Retain hardware commissioning evidence.

## Meaning of recorded time

Chrony corrects the Pi wall clock, not Nano capture counters. Even with accurate
Pi UTC, USB receipt time is later than the event. Exact UTC labelling of a Nano
event needs a verified association between Nano PPS captures and UTC seconds;
that mapping is separate work. Preserve the original measurement CSV format.
See [DATA_FORMAT.md](DATA_FORMAT.md#utc-and-capture-time) for host timestamps,
monotonic timing, segment age rotation and unsynchronised startup limitations.
