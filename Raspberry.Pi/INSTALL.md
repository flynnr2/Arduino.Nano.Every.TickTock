# Install on a Raspberry Pi Zero 2 W

Use Raspberry Pi OS Lite with Python 3.11 or newer, Wi-Fi and SSH enabled. Both the hardware connections and boot settings are in [WIRING.md](WIRING.md). The installer leaves those settings alone.

Complete the [Pi setup checklist](SETUP_CHECKLIST.md) first for headless access,
network connections, interface settings and power-saving choices.

**Before installing for the revised USB/GPS wiring**, read [Check and adjust
configuration](#check-and-adjust-configuration): the current defaults still
assign the Nano to the GPIO UART. Stop acquisition until the Nano USB firmware
selection and USB device configuration are in place.

The application runs as seven independent, always-on application services: serial acquisition/recording, causal analysis, cached views, environmental sensors, OLED, web and storage maintenance. Restarting a peripheral or web service does not restart acquisition. A Pi reboot creates a new recording session and a visible gap; measurements during the reboot are lost. An optional hourly ThingSpeak publisher
runs separately and remains disabled until its write key is configured and
verified; see [ThingSpeak setup and operation](THINGSPEAK.md).

This deployment has been checked in development but **has not yet been tested on the physical Pi, Nano or peripherals**. Complete [HARDWARE_TESTS.md](HARDWARE_TESTS.md) before relying on unattended recording.

## Copy the application

Replace `USER` and `pendulum.local` with the SSH username and hostname selected in Raspberry Pi Imager. From this repository on your Mac:

```sh
ssh USER@pendulum.local 'mkdir -p ~/Arduino.Pendulum.Timer'
rsync -av --exclude=__pycache__ \
  --include='/pendulum_analysis/***' --include='/pyproject.toml' --include='/README.md' \
  --include='/Raspberry.Pi/' --include='/Raspberry.Pi/pendulum_pi/***' \
  --include='/Raspberry.Pi/deploy/***' --include='/Raspberry.Pi/pyproject.toml' \
  --include='/Raspberry.Pi/*.md' --exclude='*' \
  ./ USER@pendulum.local:~/Arduino.Pendulum.Timer/
```

This source-only transfer includes both the receiver and the analysis package
needed by the swing-phase charts. It excludes local configuration files and
tokens, demo output, runtime state, recordings and virtual environments. Keep
those files on their original machine unless you deliberately back them up separately.

Alternatively, clone this repository on the Pi and use its `Raspberry.Pi` directory. The installer copies the package into `/opt/pendulum/venv`; running services do not depend on the checkout or your SSH login.

### Deploy from Git over SSH

The Pi applications are Python services: no firmware flashing is involved.
Once Wi-Fi and SSH work, routine installation, configuration, updates, log
inspection and service restarts can all be done remotely. Start on your Mac:

```sh
ssh USER@pendulum.local
```

Then, on the Pi, install Git and clone once (skip cloning if already present):

```sh
sudo apt update
sudo apt install git
git clone https://github.com/flynnr2/Arduino.Pendulum.Timer.git ~/Arduino.Pendulum.Timer
cd ~/Arduino.Pendulum.Timer
```

The required version must be committed and pushed to the remote repository;
uncommitted files on the Mac are not included. If the repository is private,
configure Git authentication on the Pi. Complete the first-install configuration
requirements below before relying on live acquisition.

For subsequent updates, inside the Pi's checkout:

```sh
git pull --ff-only
bash Raspberry.Pi/deploy/install.sh --check
sudo bash Raspberry.Pi/deploy/install.sh
```

Proceed only if each command succeeds. **A pull alone does not update the
running application:** the installer installs the Python package into its
virtual environment and restarts all seven services, preserving configuration
and recordings. This creates an acquisition gap, but needs no Pi reboot.
Some Python dependencies may build native components automatically during
installation; there is no separate compile-and-flash workflow for the Pi app.

For a service restart without an update, use the relevant name, for example:

```sh
sudo systemctl restart pendulum-web
sudo systemctl status pendulum-web --no-pager
sudo journalctl -u pendulum-web -n 50 --no-pager
```

The other names are `pendulum-acquire`, `pendulum-analyze`, `pendulum-views`, `pendulum-sensors`, `pendulum-oled` and `pendulum-storage`.
Restarting web/sensors/OLED leaves acquisition running; restarting acquisition
creates a recording gap. All installed services continue after you log out.

Boot-interface changes and some OS/kernel updates still require a Pi reboot;
you can issue `sudo reboot` over SSH and reconnect afterwards. Physical access
is normally only needed if networking, boot or power fails. A Pi shut down with
`sudo poweroff` cannot subsequently be reached by SSH to turn it back on.
See Raspberry Pi's [SSH documentation](https://www.raspberrypi.com/documentation/computers/remote-access.html#access-a-remote-terminal-with-ssh).

## Install

On the Pi:

```sh
cd ~/Arduino.Pendulum.Timer
python3 --version
bash Raspberry.Pi/deploy/install.sh --check
sudo bash Raspberry.Pi/deploy/install.sh
```

The installer requires Linux, systemd and root for installation. `--check` and `--help` are read-only and can also run on the Mac. The installer obtains normal OS packages through `apt` and Python dependencies through `pip`; it needs internet access. Building dependencies on a Zero 2 W can take several minutes.

It installs `python3-venv`, `python3-dev`, `build-essential`, `i2c-tools`, `libjpeg-dev`, `zlib1g-dev` and `libopenblas0-pthread`, then the local application with its `hardware` dependencies and the repository's analysis package. OpenBLAS supplies the system library required by NumPy wheels from piwheels. Before starting the services, it verifies that the service account can import swing analysis from the installed environment. It creates:

- `/opt/pendulum/venv`: application and Python dependencies, owned by root.
- `/opt/pendulum/requirements-installed.txt`: resolved dependency versions for diagnosis and reproducing a deployment; the local application still comes from this repository.
- `/opt/pendulum/source-revision.txt`: the clean source Git revision, or an explicit unversioned-source notice for a copy without Git metadata. Git installations reject tracked or non-ignored untracked changes before changing services.
- `/var/lib/pendulum/config.json`: configuration with a fresh administrator token on first installation.
- `/var/lib/pendulum/data`: recordings, never automatically deleted by the installer.
- `/run/pendulum`: shared service state, created at boot through `tmpfiles.d` and cleared on reboot.
- `/run/pendulum-i2c`: root-owned recovery state, socket and shared bus lock.

The `pendulum` service account belongs to `dialout` and `i2c`. Its configuration and recording directories are writable so the dashboard can atomically save settings. The package is read-only to the services. There is no `PrivateDevices` restriction blocking serial/I²C access.

All seven application services and the bounded I²C recovery helper start immediately. The optional
`pendulum-thingspeak.service` and `pendulum-thingspeak.timer` are installed without
creating publisher configuration or credentials. On a new installation the timer
is disabled; no ThingSpeak request is made. Missing Nano/sensors should appear as waiting or degraded health while you complete wiring. The initial dashboard listens on all interfaces at port 8080. Open `http://pendulum.local:8080/` on your local network.

Existing configuration, tokens and recordings are preserved when the installer is rerun. Installation does not pin future dependency resolution to the previous version record; retain a copy of that record and the source revision when reproducibility matters.

## Restore unavailable swing-phase charts

Updating only `Raspberry.Pi` does not install the separate analysis package.
If the dashboard reports that swing analysis is not installed, run these commands
on the Pi from the repository root (the directory containing both
`pendulum_analysis` and `Raspberry.Pi`):

```sh
cd ~/Arduino.Pendulum.Timer
sudo /opt/pendulum/venv/bin/python -m pip install .
sudo -u pendulum /opt/pendulum/venv/bin/python -I -c \
  'from pendulum_analysis.suite.swings import analyze_swings; from pendulum_analysis.pps.timescale import build_timescale; print("Swing analysis imports OK")'
sudo systemctl restart pendulum-web
```

Proceed only if installation and the import check succeed. This installs into
the web service's environment, then restarts only the web service; acquisition
continues. Reload the dashboard and allow the background calculation to finish.
Use a normal installation as above, not an editable installation from a home
directory: the web service's `ProtectHome` setting hides home directories.

If the import traceback names `libopenblas.so.0`, the Python package is installed
but its system OpenBLAS library is missing. Install the runtime on the Pi:

```sh
sudo apt update
sudo apt install libopenblas0-pthread
```

This is a [NumPy dependency documented by piwheels](https://www.piwheels.org/project/numpy/);
reinstalling the Python package alone cannot supply it. Repeat the import check
above, then restart only `pendulum-web` if it succeeds. No reboot is needed.

An import failure can also mean another missing or incompatible scientific dependency.
The import check prints the actual exception. Newer dashboard versions distinguish
missing modules from other import failures and log the traceback:

```sh
sudo journalctl -u pendulum-web -n 80 --no-pager
```

If using the full installer, it installs both packages and checks imports as the
service account before starting services. The full installer restarts acquisition
as well; the targeted repair above does not.

## Check and adjust configuration

The revised [wiring schedule](WIRING.md) uses **Nano USB serial** and reserves
`/dev/serial0` for GPS serial. Nano data and commands default to USB `Serial`.
Set the Pi acquisition `serial_port` to its observed `/dev/serial/by-id/...` device at 115200 baud.
**Do not leave Nano acquisition pointed at the GPS UART.** gpsd will own that
UART at the module's configured baud (9600 by default). The application already
reads gpsd and chrony health; install/configure those OS services separately and
complete physical acceptance. Confirm Nano firmware routing and the Pi USB
device setting before starting live acquisition with the revised wiring.

The OLED and both environmental sensors now default to **I²C bus 1**. Existing
configuration files are preserved by the installer: set both `sensor_bus` and
`oled_bus` to `1` in `/var/lib/pendulum/config.json` when adopting the shared
wiring. With power off, move OLED Data/SDA from GPIO23 (pin 16) to GPIO2
(pin 3), and OLED Clk/SCL from GPIO24 (pin 18) to GPIO3 (pin 5), alongside
the sensors. Remove the old OLED `dtoverlay=i2c-gpio,bus=3,...` line from
`/boot/firmware/config.txt`; retain `dtparam=i2c_arm=on`. Boot and follow the
[peripheral discovery checks](HARDWARE_TESTS.md#2-os-and-peripheral-discovery).
For configuration-only changes, restart `pendulum-sensors` and `pendulum-oled`.
Explicit bus settings remain supported for installations retaining older wiring.

Set `dtparam=i2c_arm_baudrate=400000` once in the boot configuration and reboot
to use fixed 400 kHz for all three peripherals. Use `100000` for the entire bus
if physical testing shows errors; software never switches per-device speeds.

The installer also installs and starts `pendulum-i2c-recovery.service`. This
small root helper exclusively handles bus-1 recovery on the Zero 2 W; sensor
and OLED workers still run as `pendulum`. It requires the packaged Linux
`gpiod` dependency and `/dev/gpiomem`, and coordinates through a root-owned
`/run/pendulum-i2c` directory. Stop both peripheral workers before manually
installing/enabling it; the installer handles this during upgrades. Kernel
sensor/display overlays and other bus users must be absent. Review
[recovery operation and commissioning](I2C_RECOVERY.md). Unsupported hardware
reports recovery unavailable and retains ordinary driver retries.

Read the configuration without printing its token:

```sh
sudo -u pendulum /opt/pendulum/venv/bin/python -m pendulum_pi check-config \
  --config /var/lib/pendulum/config.json
```

Edit it locally on the Pi:

```sh
sudoedit /var/lib/pendulum/config.json
sudo -u pendulum /opt/pendulum/venv/bin/python -m pendulum_pi check-config \
  --config /var/lib/pendulum/config.json
```

Keep `data_dir` under `/var/lib/pendulum` and `runtime_dir` at `/run/pendulum` with the provided service units. Moving either outside those paths requires updating the units’ `ReadWritePaths` and arranging ownership first.

The dashboard can change recording/storage settings, the optional forecast
cycle length, PPS holdover, target full period and display-history limits. The
swing mean window is fixed at 600 seconds; PPS keeps its existing dual EWMA. Acquisition reloads those live settings.
The target defaults to unset; changing it does not reset period estimates.
History defaults to 30 days and 256 MiB, within the overall data budget and
free-space reserve. Hardware, network, token and service settings require a restart:

```sh
sudo systemctl restart pendulum-acquire pendulum-sensors pendulum-oled pendulum-web pendulum-storage
```

Restarting acquisition creates a collection gap. Restart only the affected peripheral or web service when a full restart is unnecessary.
See [CONFIGURATION.md](CONFIGURATION.md) for every setting, default, validation
rule and restart requirement, and [HTTP_API.md](HTTP_API.md) for the web interface.

## Optional ThingSpeak diary

Follow [THINGSPEAK.md](THINGSPEAK.md) when ready to provision the channel and
write key. The separate commissioning command verifies the key with one
status-only write before enabling the hourly timer. Missing credentials or an
unverified/replaced key keep the publisher out of service. Normal installation
and updates never perform a validation write.

An update preserves a previously enabled timer only when the local readiness
check succeeds for its current configuration and key. Otherwise the timer is
left disabled, while the seven core services continue normally.

## Archive setup

Configure `archive_dir` and the storage service filesystem permissions using
[STORAGE.md](STORAGE.md#configure-an-archive). No destination is guessed during
installation. Compression and catalogue generation work without a destination,
but automatic deletion of sole unarchived copies remains disabled.

## GPS and chrony timekeeping

Follow [TIMEKEEPING.md](TIMEKEEPING.md) for the selected GPS/PPS primary source,
network NTP fallback, commissioning checks and current implementation limits.
The [boot configuration](WIRING.md#raspberry-pi-os-configuration) adds PPS on
GPIO17 (physical pin 11), alongside the GPS UART and shared I²C bus.

The application installer currently installs **neither gpsd nor chrony** and
configures neither service. They are separate OS services, not additional
Python workers. Keep acquisition independent of GPS lock: a missing UTC source
must not prevent recording Nano counters. Only one service should discipline
the Pi clock; chrony replaces any other active time synchronisation daemon.

## Administrator token and network access

Monitoring and log downloads are available to anyone who can reach this server. Configuration changes and Nano commands require the administrator token. To display it in your own SSH terminal:

```sh
sudo python3 -c 'import json; print(json.load(open("/var/lib/pendulum/config.json"))["api_token"])'
```

Paste it into the dashboard’s administrator field. The page does not store it in cookies or browser local storage. Setting `api_token` to an empty string disables web changes while leaving monitoring and downloads available.

HTTP does not encrypt the token or recordings. Use the server on a trusted local network; do not expose port 8080 directly to the public internet. For encrypted access through SSH, set `web_host` to `127.0.0.1`, restart `pendulum-web`, and run this on your Mac:

```sh
ssh -N -L 8080:127.0.0.1:8080 USER@pendulum.local
```

Keep that terminal open and browse to `http://127.0.0.1:8080/`. An authenticated HTTPS reverse proxy is another deployment option, but is not installed here.

## Check service health

```sh
systemctl status pendulum-acquire pendulum-sensors pendulum-oled pendulum-web pendulum-storage --no-pager
journalctl -u pendulum-acquire -u pendulum-sensors -u pendulum-oled -u pendulum-web -u pendulum-storage -n 100 --no-pager
```

Use `sudo` for the journal if your SSH account cannot read it. Follow acquisition messages while connecting hardware:

```sh
sudo journalctl -u pendulum-acquire -f
```

A running process does not prove valid capture. In the dashboard, check source **serial**, fresh capture, validated metadata, active recording, sensor/OLED health and advancing counters. Demo and replay are explicitly labelled and do not validate physical hardware.

The acquisition, sensor and OLED units use a 30-second software watchdog; the
analysis worker uses 60 seconds. A blocked process is restarted independently. Each of the seven always-on services also restarts after a failure, with a three-second delay. These are process watchdogs; they do not reset a frozen kernel or recover a power failure. See the [systemd service reference](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) for watchdog semantics.

The web server limits concurrent downloads, streaming a fixed snapshot of complete lines so acquisition can keep writing. Storage maintenance expires only verified archived recordings according to the configured retention and budget. Unarchived recordings are preserved; if no eligible files can be removed, recording stops at the limit. See [archive setup and storage policy](STORAGE.md).

## Update or recover

For a traceable update, select a reviewed commit or release tag, verify the
checkout is clean, and require the installer to match that revision. For example,
replace `REVIEWED_COMMIT_OR_TAG` with the selected revision:

```sh
git fetch origin --tags
git switch --detach REVIEWED_COMMIT_OR_TAG
git status --short
bash Raspberry.Pi/deploy/install.sh --check
sudo bash Raspberry.Pi/deploy/install.sh --revision REVIEWED_COMMIT_OR_TAG
```

`--check` validates installer inputs without changing the Pi; it does not certify
the selected revision or hardware. Installation refuses a dirty Git checkout or
a `--revision` mismatch before package updates or service stops. Ignored local
recordings and configuration do not count as source changes. Do not edit or
switch the checkout while installation is running. A copy-based installation
without Git remains supported by omitting `--revision`, but cannot claim a
verified source commit.

Before updating, copy `/opt/pendulum/source-revision.txt`,
`/opt/pendulum/requirements-installed.txt`, and the private configuration and
publisher credentials to a protected off-device backup. Back up recordings
using the [storage procedure](STORAGE.md); a source Git bundle does not contain
recordings or ignored configuration. Keep credentials out of Git and public
validation artifacts. After installation, retain the new source/dependency
records and verify active services, fresh acquisition, advancing recording
counters, sensor/OLED health and GPS/chrony status. Record results against the
installed commit in the hardware acceptance log.

The installer stops all seven core services and any optional ThingSpeak activity
before changing the virtual environment. This causes a recording gap and may take several minutes. It validates the preserved configuration before enabling and starting the services again.

If an update fails, the installer leaves the stopped services for investigation instead of intentionally restarting a partial installation. Configuration and recordings remain in `/var/lib/pendulum`. Fix the reported error and rerun the installer. To return to an earlier application, restore the known source revision and reinstall; restore its recorded dependency versions as necessary. Keep off-device backups of recordings and configuration, including the administrator token.

The `pi-known-good-2026-09-30` tag records `ad288184a0bb94138d62418479e50c64a2ed9607`,
observed running on `pendulum0` with a clean checkout on 2026-09-30. It predates
the `--revision` installer option. To recover that baseline, switch a clean
checkout to the tag and run its plain `sudo bash Raspberry.Pi/deploy/install.sh`.
Its installer resolves dependency ranges again: retain the saved dependency
record and restore those versions if necessary. This is source rollback, not
an automatic database downgrade; preserve data and inspect compatibility first.

To stop recording cleanly before removing power:

```sh
sudo systemctl stop pendulum-acquire
sudo poweroff
```

Wait for shutdown before disconnecting power. For physical acceptance, use the simultaneous recording/download, disconnect, reboot and fault checks in [HARDWARE_TESTS.md](HARDWARE_TESTS.md).
