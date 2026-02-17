# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
