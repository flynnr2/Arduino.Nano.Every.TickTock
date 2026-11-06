# Raspberry Pi OS Lite configuration checklist

OS settings for our **Raspberry Pi Zero 2 W**, after the standard
[Raspberry Pi setup](https://www.raspberrypi.com/documentation/computers/getting-started.html#hardware-prerequisites).
Run commands on the Pi unless stated otherwise. `USER`, `pendulum.local`,
`YOUR_SSID` and `PROFILE` are placeholders for your username, hostname, network
name and saved Wi-Fi profile. Network commands below assume Bookworm or newer
with NetworkManager. For a first boot without a screen, supply Wi-Fi, your user
and SSH settings in Imager before writing the card.

## Network and headless access

- [X] **Set up Wi-Fi:** in `sudo raspi-config` → Localisation Options → WLAN
  Country, select your country (GB in the UK). Connect to your 2.4 GHz network:

  ```sh
  sudo nmcli radio wifi on
  nmcli device wifi list
  sudo nmcli --ask device wifi connect "YOUR_SSID"
  ```

  Enter the password when prompted; the connection is saved. Alternatively,
  use `sudo nmtui` → Activate a connection. Changing networks over SSH may
  disconnect you. **Check:** `nmcli device status` should show `wlan0` connected;
  `hostname -I` should show an IP address. Avoid guest/client-isolated Wi-Fi.
  See [NetworkManager commands](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html).
- [X] **Set the hostname:** `sudo raspi-config` → System Options → Hostname;
  for example `pendulum`. After reboot, try `pendulum.local`. Record the IP
  from `hostname -I` as a fallback; reserve it in the router's DHCP settings
  if you want a stable address.
- [X] **Enable SSH:** `sudo raspi-config` → Interface Options → SSH → Yes,
  or run `sudo systemctl enable --now ssh`. **Check from the Mac:**
  `ssh USER@pendulum.local` (use the IP if name discovery fails).
- [X] **Set unattended boot:** in `sudo raspi-config` → System Options,
  choose console/text boot without automatic login under Boot/Auto Login,
  and disable Network at Boot / “Wait for network at boot”. Menu wording can
  vary by release. A desktop, VNC and HDMI configuration are unnecessary.
- [X] **Updates and locale:** run `sudo apt update`, then
  `sudo apt full-upgrade`. Set timezone and locale in `sudo raspi-config`
  (for example `Europe/London`). Reboot before commissioning.
- [ ] **Install Git for remote deployment:** run `sudo apt install git`.
  Thereafter, use SSH to clone/update the repository and install or restart
  the Python services. See the [Git workflow](INSTALL.md#deploy-from-git-over-ssh).
  Routine application updates need no flashing or Pi reboot.
- [?] **Network access:** if filtering is enabled, allow incoming TCP **22**
  for SSH/SFTP and **8080** for the application, from trusted LAN clients.
  Allow local mDNS (UDP **5353**) for `.local` names, and outgoing DNS,
  HTTP/HTTPS downloads and NTP (UDP **123**). Keep gpsd port **2947** local to
  the Pi. No public port forwarding, SMB or remote GPIO service is required.

## Enable the required OS devices

For manual edits, use `/boot/firmware/config.txt` on Bookworm and newer,
or `/boot/config.txt` on older releases. Put settings in an applicable `[all]`
section, avoiding contradictory entries. Reboot after these changes; the checks
below are for after that reboot. Overlay options are documented in the
[official reference](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README).

- [X] **Enable `/dev/i2c-1` (main I²C bus).** Run `sudo raspi-config` →
  Interface Options → I²C → **Yes**, or set `dtparam=i2c_arm=on` in
  `config.txt`. Add `dtparam=i2c_arm_baudrate=400000` for one fixed
  400 kHz speed shared by all peripherals (100000 is the whole-bus fallback).
  For manual setup, also arrange for the userspace device driver
  to load at boot:

  ```sh
  echo i2c-dev | sudo tee /etc/modules-load.d/pendulum-i2c.conf
  ```

  **Check:** `ls -l /dev/i2c-1`. If missing, try `sudo modprobe i2c-dev`;
  if still missing, check the boot setting. Enabling the bus and loading its
  userspace driver are both necessary.
- [ ] **Share bus 1 between the OLED and environmental sensors.** Connect all
  SDA lines to GPIO2 (pin 3) and all SCL lines to GPIO3 (pin 5). Set both
  `sensor_bus` and `oled_bus` to `1` in existing application configuration.
  Remove the old OLED `dtoverlay=i2c-gpio,bus=3,...` entry from `config.txt`
  if present; GPIO23/24 and `/dev/i2c-3` are no longer needed. Make wiring
  changes with power off, then check all three addresses on bus 1 as described
  in the [hardware checks](HARDWARE_TESTS.md#2-os-and-peripheral-discovery).
- [X] **Enable `/dev/serial0` for GPS, without a login console.** In
  `sudo raspi-config` → Interface Options → Serial Port, answer **No** to a
  login shell and **Yes** to serial hardware. Ensure `config.txt` contains:

  ```ini
  enable_uart=1
  dtoverlay=disable-bt
  ```

  Disable Bluetooth's UART service with
  `sudo systemctl disable --now hciuart.service`; Wi-Fi remains enabled.
  In `cmdline.txt` in the same boot directory, remove serial-console entries
  such as `console=serial0,115200` or `console=ttyAMA0,115200`. Keep that file
  on **one line** and retain unrelated parameters, including `console=tty1`.

  ```sh
  sudo systemctl disable --now serial-getty@serial0.service serial-getty@ttyAMA0.service
  ```

  A missing-service message is harmless if the service is absent.
  **Check:** `readlink -f /dev/serial0` should normally show `/dev/ttyAMA0` on
  this Zero 2 W setup; its serial-getty must be inactive.
- [X] **Enable `/dev/pps0` (PPS input).** Add
  `dtoverlay=pps-gpio,gpiopin=17` to `config.txt`.
  **Check:** `ls -l /dev/pps0`. This enables the OS device; clock
  synchronisation still requires the gpsd/chrony setup below.
- [X] **USB serial:** leave USB in normal host mode; no raspi-config serial
  switch is needed for USB. Do not enable USB gadget/Ethernet mode.
  **Check when connected:** `ls -l /dev/serial/by-id/`. If ModemManager is
  installed, disable competing serial probing with
  `sudo systemctl disable --now ModemManager.service`.
- [ ] **Permissions:** the account accessing these devices needs **dialout**
  and **i2c** group membership. The application installer assigns both to its
  service account. Check with `id pendulum` after installation.
- [X] **Unused interfaces:** leave SPI, 1-Wire and remote GPIO disabled.

## Power saving and storage

- [X] **Disable Wi-Fi power saving** and enable automatic reconnection:

  ```sh
  nmcli -f NAME,TYPE,DEVICE connection show --active
  sudo nmcli connection modify "PROFILE" connection.autoconnect yes 802-11-wireless.powersave 2
  ```

  Replace `PROFILE` with the Wi-Fi connection name shown. Apply at the next
  reboot rather than dropping the current SSH connection. Afterwards,
  `iw dev wlan0 get power_save` should report `off`.
  [NetworkManager defines 2 as disabled](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html).
- [X] **USB autosuspend (optional precaution for this dedicated logger):**
  append `usbcore.autosuspend=-1` to the existing single line in
  `/boot/firmware/cmdline.txt` (`/boot/cmdline.txt` on older releases).
  Reboot, then check `cat /sys/module/usbcore/parameters/autosuspend` reports
  `-1`. This disables autosuspend by default for all USB devices, at the cost
  of potential extra power use. It is usually already disabled for non-hub
  devices. Avoid tools such as `powertop --auto-tune` that can override device
  power settings. See [Linux USB power management](https://docs.kernel.org/driver-api/usb/power-management.html).
- [!] **System sleep:** baseline Lite needs no extra sleep-disabling setting.
  Avoid scheduled suspend, shutdown or automatic reboot during runs. Screen
  blanking can stay enabled; it does not stop background services.
- [!] **GPIO edge timing:** use the kernel `pps-gpio` overlay above. There is
  no separate GPIO “keep awake” switch. The driver timestamps PPS in an
  interrupt handler, so CPU idle exit and interrupt latency can affect the
  timestamp; it is not a hardware-latched edge timestamp. Disabling Wi-Fi or
  USB power saving does not remove this latency. See the
  [Pi PPS driver](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/pps/clients/pps-gpio.c)
  and [Linux CPU idle documentation](https://docs.kernel.org/admin-guide/pm/cpuidle.html).
  Our precision pendulum/PPS capture remains on the Nano; Pi PPS disciplines
  the Pi's wall clock. Keep normal CPU scaling and thermal protection for
  initial commissioning, then measure Pi PPS jitter before choosing CPU idle
  or frequency tuning. `force_turbo=1` is not a guarantee of immediate edge
  timestamps and is not part of the baseline setup.
- [X] **Keep the filesystem writable:** leave raspi-config's overlay/read-only
  filesystem option disabled. Check available storage with `df -h /var/lib`.

## Time synchronisation and final OS checks

- [X] Configure **gpsd + chrony** following [TIMEKEEPING.md](TIMEKEEPING.md).
  gpsd must use only `/dev/serial0`, poll continuously (`-n`), and avoid USB
  auto-discovery. chrony must use GPS/PPS with network NTP fallback and be the
  only active clock-disciplining service. Keep existing network time working
  until chrony is ready. The application installer does not configure these.
- [X] **Reboot**, reconnect by SSH, then check the OS devices and services:

  ```sh
  systemctl is-active ssh NetworkManager
  readlink -f /dev/serial0
  ls -l /dev/i2c-1 /dev/pps0
  iw dev wlan0 get power_save
  ```

  Expect active services, `/dev/serial0` normally resolving to `/dev/ttyAMA0`,
  both I²C devices and the PPS device present, and Wi-Fi power saving off.
  Once timekeeping is configured, also check `chronyc sources -v` and
  `chronyc tracking` for actual synchronisation.

OS references checked on 2026-09-27. Further detail:
[Raspberry Pi OS configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).
Application deployment is covered separately in [INSTALL.md](INSTALL.md).
