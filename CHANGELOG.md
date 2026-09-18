# Changelog

## [4.14] - 2026-09-18

- Add `cookies <bundle>` to copy real HTTP cookies (binarycookies, HTTPStorages
  sqlite + WAL) and Shopee login files from the data container **and App Group**
  into `/var/mobile/controlios-cookies/<bundle>/` for PC download.
- Do not walk huge Documents/Caches trees; login files are copied by known path.

## [4.13] - 2026-09-14

- Add a lightweight `version` control command so PC can show the IPA version
  on the device grid without running full diagnostics.


## [4.12] - 2026-09-14

- Fix EarnApp/app relaunch failing with `sbs=3` (IncompatibleService): terminate
  through FrontBoard before SIGKILL, and launch with SBS UnlockDevice options
  instead of NULL dictionaries. Treat an already-running PID as launch success.

## [4.11] - 2026-09-11

- Skip the capture defer window while a pointer is held, so slider captchas
  and other drags flush frames immediately instead of waiting 8-15ms per
  update.

## [4.10] - 2026-09-10

- Added **Free RAM** (`freeram` / `killallapps`, JS `freeRAM()`): terminate running
  user apps except ControlIOS, TrollStore and essential system processes, then report
  available memory before/after. EarnApp/Golike are closed when the user confirms.
- Diagnostics now include `memory_avail`.
- App UI: **RAM** on the diagnostics screen and **Giải phóng RAM** in Tools.

## [4.9] - 2026-09-01

- Control socket now handles clients concurrently, so a long or stalled file
  transfer no longer blocks diagnostics and later PC commands.
- Added an on-device **Connection Diagnostics** screen showing manager/server,
  VNC, control/file-transfer, Keeper, LAN/config state and daemon health data.
- Added one-tap service restart and automatic recheck from the diagnostics
  screen.

## [4.8] - 2026-08-29

### Fixed
- Initialize the framebuffer using the current device orientation before
  accepting clients, avoiding an immediate disconnect on landscape devices.
- Enable TCP keepalive and `TCP_NODELAY` on accepted VNC sockets so transient
  Wi-Fi/USB interruptions are detected quickly and input remains responsive.
- Make orientation synchronization opt-in by default; enabling it still
  preserves the existing rotation behavior for setups that need it.

All notable changes to TrollVNC are documented here.

## [3.2] – 2026-04-19

### Added
- **Bind Address**: The VNC server can now be bound to a specific local IP address. Leave empty to listen on all interfaces. Validation is performed in the app UI before applying.
- **Orientation Correction** (formerly *Apply Orientation Fix*): Replaced the on/off toggle with a four-option selector — 0° (Default), 90° Clockwise, 180°, and Counterclockwise 90°. Useful for iPad models with non-standard default orientations.
- **Subscription Reconnection**: The client list now automatically reconnects to the VNC server daemon with exponential backoff (1s → 30s cap) and TCP keep-alive, recovering gracefully from server restarts.
- **Simulator Sandbox Support**: `sim-spawn.sh` now forwards the simulator sandbox path as an environment variable so the server reads preferences from the correct location during development. Logs are tee’d to both the terminal and the sandbox tmp directory.

### Changed
- Xcode app target now reads and writes preferences via the daemon’s sandbox path when running in the simulator.

### Fixed
- Version check now falls back gracefully on older iOS versions where the system API is unavailable.
- Fixed a short-read bug in `TVNCReadAll`: data is now read until EOF or timeout instead of stopping early on a partial buffer.
- Fixed subscription handshake validation: the server response is now checked to contain “OK” before the client list is considered live; an invalid response triggers an immediate reconnect.
- Fixed `TVNCSliderCell` reuse bugs: `prepareForReuse` resets the slider to `minimumValue`, and `refreshCellContentsWithSpecifier:` fully re-applies `min`/`max`/`format`/`isContinuous` from the incoming specifier.
