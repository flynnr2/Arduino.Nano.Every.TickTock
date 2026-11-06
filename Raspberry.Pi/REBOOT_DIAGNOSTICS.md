# Reboot diagnostics on the Pi Zero 2 W

Install these optional host diagnostics separately from application updates:

```sh
bash Raspberry.Pi/deploy/install-diagnostics.sh --check
sudo bash Raspberry.Pi/deploy/install-diagnostics.sh
```

The installer enables diagnostics without restarting acquisition or rebooting.
It retains the operating system's hardware watchdog. Root privileges are needed
to install the system service, journal settings and boot overlay.

## Evidence retained

- The system journal is stored on disk, capped at 128 MiB and 14 days, with a
  256 MiB free-space reserve and a five-second sync interval. Older entries
  rotate out when either retention limit is reached. Sudden power loss can
  still lose recent messages.
- `pendulum-health.service` records every ten seconds to
  `/var/log/pendulum-health/health.jsonl`, with seven rotated backups. The files
  occupy at most about 32 MiB, plus a small `last-state.json`. Each observation
  is flushed to disk. The recorder runs at low CPU and I/O priority.
- Samples contain UTC, boot identity, monotonic time, memory and swap usage,
  load, temperature, free disk space, watchdog boot status, application service
  restart counters and selected public dashboard health fields. Dashboard
  failure does not stop host diagnostics. Admin tokens and configuration files
  are not collected.
- A startup record preserves the previous recorder's final event and boot
  identity. On a graceful service stop the recorder writes `recorder_stopped`;
  if systemd reports the system is stopping, it writes `shutdown_requested`.
  This establishes shutdown intent, not successful completion. An absent
  marker alone cannot distinguish power loss, a crash or forced termination.
- The `ramoops` boot overlay reserves 128 KiB: 64 KiB for console messages and
  two 32 KiB crash records. It takes effect at the next planned boot. Linux
  `pstore` and the existing `systemd-pstore` service can preserve crash/console
  records through warm resets and move them to `/var/lib/systemd/pstore/`.
  Retention depends on reset behaviour; this RAM cannot survive power loss.

The Zero range has no low-voltage detection circuitry. `get_throttled` readings
cannot rule out a brief supply failure. Independently powered voltage monitoring
is needed to establish that cause. See the [Raspberry Pi power supply
documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-supply-warnings).

## Verify after installation

```sh
systemctl status pendulum-health --no-pager
sudo tail -n 2 /var/log/pendulum-health/health.jsonl
systemd-analyze cat-config systemd/journald.conf
journalctl --list-boots
```

The effective journal configuration must end with `Storage=persistent`. The
health file should gain a new sample roughly every ten seconds. Before the
first reboot, only the current boot appears in the journal listing.

After a planned reboot, also check:

```sh
sudo journalctl -k -b --grep='ramoops|pstore'
systemctl status systemd-pstore --no-pager
sudo ls -lah /sys/fs/pstore /var/lib/systemd/pstore
```

An empty crash archive is normal when there has been no captured crash. Avoid
triggering a deliberate kernel panic on the measurement system as a test.

## Investigate the next unexpected reboot

```sh
journalctl --list-boots
sudo journalctl -b -1 -n 250 --no-pager -o short-monotonic
sudo journalctl -k -b -1 --no-pager
sudo ls -lah /var/lib/systemd/pstore
sudo tail -n 30 /var/log/pendulum-health/health.jsonl
```

Preserve all `health.jsonl*` files, `last-state.json`, crash archive files and
the previous boot's journal before they rotate out. Use monotonic ordering and
boot identity: wall-clock dates can initially be wrong until chrony synchronises.
Compare shutdown requests, memory exhaustion, service failures, disk errors,
kernel crash messages and the last health samples. A quiet log ending is not
proof of a power failure; a hang can prevent further logging too.

## Remove or adjust

Journal settings are in
`/etc/systemd/journald.conf.d/90-pendulum-diagnostics.conf`. Restart
`systemd-journald` and run `sudo journalctl --flush` after changing them.
Disable the recorder with `sudo systemctl disable --now pendulum-health`.

The installer saves the original boot configuration as
`/boot/firmware/config.txt.before-pendulum-diagnostics` before appending a marked
`[all]` section and `dtoverlay=ramoops,...` line. To remove crash capture, remove
that appended overlay line and reboot at a convenient time. Do not restore the
whole backup over later unrelated boot changes. Re-running the installer is
safe with its existing overlay line; it refuses conflicting ramoops settings.
