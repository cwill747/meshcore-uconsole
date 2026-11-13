# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## v1.13.0 (2026-11-13)

### Feat

- **map**: add a switch to stop downloading map tiles
- discover nearby repeaters and ask them for their names
- support private channel keys when adding channels (#101) (#103)

### Fix

- **messages**: key DM threads by the peer's public key
- **peers**: give a name collision a name nobody else holds
- **admin**: bind a saved repeater password to the repeater's key
- **channels**: keep group channels out of the DM thread ids
- **messages**: keep a message's details within the screen
- **messages**: scroll to a new message after the layout, not during it
- **messages**: keep the newest message in view as messages arrive
- **ui**: draw a path from this device outwards, not backwards
- **map**: open where the map was left, or at this device, not on the USA
- **map**: show this device's fixed position on the map even when not shared
- **ui**: stop network paths drawing over the public key
- **admin**: replace the "telemetry" quick command, which repeaters don't have
- **requests**: don't answer replayed requests or transit copies
- **requests**: skip a contact with an unusable key instead of dropping the request
- **cli**: end `listen --duration` on time even when nothing is received
- **client**: record app-generated events once in the event history
- **cli**: let `send` reach the peers the app knows
- **routing**: don't restore routes from stored advert paths at startup
- **analyzer**: store each received packet once
- **client**: close the database connection of a replaced session
- **cli**: let import-channel take the key without the command line
- **gps**: don't power the GPS rail off when the serial fallback stops
- **gps**: report a gpsd position only while there is a current fix
- **map**: update markers when peers move or are renamed
- **admin**: open the console after an admin login
- **admin**: let Up/Down leave the command line when there is no history
- **admin**: say "still waiting" instead of ignoring requests while busy
- **ui**: make node badges usable from the keyboard
- **ui**: stop leaking a popover per channel right-click
- **ui**: reach channel actions from the keyboard
- **peers**: keep details, focus and selection when the peer list refreshes
- **channels**: remove a private channel's key with the channel, after warning
- **messages**: keep message history per channel
- **messages**: show late CLI replies in Admin and credit room-post authors
- **ui**: URL-encode the node name in the contact QR code
- **settings**: fall back to the default for an unreadable settings row
- **presets**: power the AIOv2 LoRa module from GPIO 16, not the GPS rail
- **radio**: pass MeshCore's sync word to the radio explicitly
- **radio**: add the EU/UK Narrow preset and use MeshCore 1.16 preamble lengths
- **security**: bound the peer list
- **messages**: show the real hop count instead of "255 hops"
- **messages**: pair each decoded message with its own packet
- **messages**: stop dropping a repeated DM sent minutes later
- **messages**: send without freezing the UI, retry DMs, and report delivery
- give DM threads their own channel ids
- **routing**: flood DMs until a route is known, and stop taking routes from adverts
- **security**: never let another key take over a contact's name
- **security**: build peers only from adverts openhop_core has verified
- **admin**: ask before rebooting a repeater
- **admin**: enable the command line only for admin logins
- **admin**: show the logged-out state after a disconnect
- **cli**: use the saved settings for listen, send and advert
- **client**: keep processing a batch of events when one of them fails
- **db**: run schema upgrades in one transaction
- **client**: serialise connect/disconnect and clean up a failed connect
- **client**: never leave a radio call waiting forever
- deliver bursts of events to the UI without dropping the oldest
- **analyzer**: keep collecting packets while paused, and stop inventing signal values
- **settings**: allow a fixed position to be cleared or set to 0
- **ui**: keep the open channel and unread counts updating
- **ui**: keep the main window within a 1024x576 screen
- **ui**: close Add Channel and Region Scope dialogs when Enter confirms them
- **ui**: make Export Logs work on GTK 4.8
- **ui**: show toast text literally on libadwaita 1.2
- **privacy**: rate-limit discovery answers and honour the node-type filter
- **privacy**: leave anonymous requests to openhop_core's handler
- **privacy**: send the position in telemetry replies only when it is shared
- **logging**: keep message text, positions and control characters out of logs
- **security**: keep the identity seed, database and logs owner-only
- **logging**: keep the log in the state directory that XDG_STATE_HOME names
- **ui**: rebuild the admin repeater list whenever its repeaters change
- use the Ed25519 identity key as the node's own public key
- **repeater**: make logout local instead of sending a CLI "logout"
- **repeater**: use the openhop_core 1.1 handler API for login, CLI and telemetry
- **ui**: wrap channel list in a ScrolledWindow so the window fits the screen (#102)
- normalise routing_path from bytes to list[int] for PacketBuilder compatibility (#99)

## v1.12.0 (2026-09-11)

### Feat

- Add region scoping support (#90) (#94)
- Add setting to disable radio error toasts (#80) (#86)
- Add repeater admin view with login, CLI, and password storage

### Fix

- **ui**: support alert dialog response activation (#96)
- Treat CRC and header errors as RF noise, not radio faults (#91) (#93)
- Make the GPIO chip, IRQ backend, and enable pins configurable (#85) (#89)
- Derive and store channel secrets for hashtag channels (#81) (#87)
- Make the GPS serial port configurable (#82) (#88)
- **ui**: render on libadwaita 1.2 (Debian bookworm) (#84)
- Replace telemetry button with valid firmware CLI command (#79)
- Replace hallucinated repeater admin commands with real ones (#78)
- Set contact routing path so repeater admin login works (#77)
- Address review comments on repeater admin PR

## v1.11.0 (2026-05-17)

### Feat

- Add dedupe toggle to analyzer view
- Add mock data with 2-byte and 3-byte path hops
- Use FlowBox for path visualization to support wrapping long paths
- Add multi-byte path hash mode setting (#71)

### Fix

- Auto-create venv with system-site-packages in run-dev.sh
- Address codex review comments on long path visualization
- Show self public key prefix in path badges instead of hardcoded "Me"

## v1.10.0 (2026-05-15)

### Feat

- Add "telemetry favorites only" setting

### Fix

- Address codex review comments on telemetry favorites
- codex issues
- Resolve ANON_REQ sender identity via ContactBook key lookup
- Handle inbound telemetry requests with hash-prefix collisions
- Scope telemetry results to the requesting peer and add ContactBook key lookup
- Use settings fixed position in adverts, telemetry, and map (#66)
- Propagate routing fields (path_hops, snr, rssi) to message events (#64)
- Correct telemetry callback signature and group text error handling (#63)
- Issues when favoriting node (#62)
- Use PacketBuilder for send operations, supporting pymc-core 1.0.10 (#61)

## v1.9.1 (2026-05-08)

### Fix

- Fix builds after uv change
- Don't re-apply presets on settings save, preserving user edits (#59)
- Use dtoverlay=spi1-1cs instead of raspi-config for SPI1 enablement (#57)
- Build .deb packages for both Bookworm and Trixie

## v1.9.0 (2026-03-08)

### Feat

- Add loading/shutdown screen widget
- Add pre-flight conflict detection for radio hardware (#48)

## v1.8.0 (2026-02-22)

### Feat

- Add telemetry request and response

### Fix

- Don't show self in contacts
- Use gpsd if available

## v1.7.0 (2026-02-19)

### Feat

- Analyzer shows decrypted content and route indicators
- Store channel kind explicitly in Channel model
- Surface radio hardware errors in StatusPill and toast

### Fix

- Fix private channel messaging and message dedup
- Need to fall back out of decrypted not existing
- Add hardware presets
- Saving settings shouldn't freeze UI

## v1.6.1 (2026-02-18)

### Fix

- Fix unread message handling

## v1.6.0 (2026-02-17)

### Feat

- Add favorite capabilities for peers

### Fix

- Migrate all polling behavior to use glib async
- Accessibility

## v1.5.0 (2026-02-17)

### Feat

- Dynamic scaling
- Add mentions
- Add more packet handling to analyzer
- Add hashtag-channel adding from UI
- Add CONTROL packet handling

### Fix

- Fix main width again and wraparound message text
- Update sizing method with larger fonts
- Slight width overflow
- Message wordwrap
- Sort peers reverse-chronilocallcally
- Rely on font with emojis on Pi
- Multibyte unicode not displaying correctly
- Mentions are always bracket-wrapped
- Potentially fix emoji / UTF8 node names
- Add channel to channel list after import

## v1.4.0 (2026-02-16)

### Feat

- Add path view on messages

### Fix

- Autoscroll channel to bottom when loading channel pages
- Speculative grp_text fixes
- Group texts not appearing in channels

## v1.3.1 (2026-02-15)

### Fix

- Day separator was not showing
- Details box shows details for wrong packet

## v1.3.0 (2026-02-15)

### Feat

- Add monkey testing script for UI stress testing

### Fix

- Resolve GTK widget assertions found by monkey testing
- Fixup screenshot generation (#23)

## v1.2.0 (2026-02-14)

### Feat

- Add ability to import private channel

### Fix

- Consistent timestamps across GUI
- Sending and receiving messages different channels
- Let pymc handle out_path
- Peer data not refreshing on advert
- Historical packets showed wrong timestamp
- Fix dms again

## v1.1.0 (2026-02-13)

### Feat

- Add emoji support
- Add more node info badges
- Add node prefix to channel view, fix viewport
- Add log level setting to settings and log it somewhere (#17)

### Fix

- Node disposal issues
- Make analyzer columns a bit bigger
- Make content text more robust
- Case-sensitive DMs
- Remove double CLIs

## v1.0.0 (2026-02-13)

### Feat

- Move from json-based cache to sqlite
- Add day change analyzer line
- Add map follow mode
- Add autoconnect setting

### Fix

- Resolve sender nodes of packets from known peers
- Add pycore to deps
- Don't crash gtk on gpio issues
- Not getting messages in channels
- Poll on GPS pin
- Repeaters show as nodes
- Fix settings savings crashing the session
- Hook up DM<->Channel correctly
- Refresh channel list when channel created
- Create DM channels when DMs received
- Stable key
- Add contacts db
- Also fix received callbacks
- Wrong dispatcher callback
- Public key not showing on settings page

### Perf

- Reduce CPU/IO load on Raspberry Pi hot paths

## v0.2.1 (2026-02-13)

### Fix

- Hallucinated APIs used in pymc_core

## v0.2.0 (2026-02-13)

### Feat

- add conventional commits and automated releases

### Fix

- Don't land under 'Internet'
- **deb**: Update to libgpiod3
