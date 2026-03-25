# ⏱ Time Solution

> **Stable. Sync. Secure.**  
> High-precision multi-protocol timecode synchronisation for live events, broadcast and installation.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform: Windows | macOS](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey.svg)]()
[![Licence: Proprietary](https://img.shields.io/badge/licence-Proprietary-red.svg)]()

---

## Overview

**Time Solution** is a professional timecode synchronisation engine and web dashboard developed by **Audio Solutions Network**.  
It unifies MIDI/MTC, OSC, Dante (AES67), Art-Net and SMPTE/LTC into a single Kalman-filter-compensated master clock, giving technical directors a reliable, monitorable timing hub for any production environment.

---

## Features

| Feature | Details |
|---|---|
| 🎯 High-Precision Engine | Sub-millisecond accuracy · Kalman-filter latency compensation |
| 📡 Multi-Protocol | MIDI/MTC · OSC · Dante (AES67) · Art-Net 4 · SMPTE 12M / LTC |
| 📊 Real-Time Waveforms | Rolling 30-second latency waveform per protocol |
| 🔄 A/B Hot-Swap | Switch Legacy ↔ AI-Enhanced engine with zero downtime |
| 🗂️ Show Logging | Per-show `.log` files for post-event analysis and AI training |
| 🌐 Remote Dashboard | Browser-based monitoring on any device, zero install |
| 🖥️ Cross-Platform | Windows 10/11 (ASIO/WDM) · macOS (CoreAudio/CoreMIDI) |

---

## Project Structure

```
timesolution-site/
├── index.html                  # Landing page (open in browser or serve statically)
├── requirements.txt            # Python dependencies
├── .gitignore
├── README.md
└── engine/
    ├── __init__.py
    ├── timecode_engine.py      # Core sync engine (multi-protocol + Kalman filter)
    └── latency_monitor.py      # Real-time latency monitoring & alerting
```

---

## Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the engine (minimal example)

```python
from engine.timecode_engine import TimecodeEngine, EngineConfig
from engine.latency_monitor import LatencyMonitor

def on_sync(state):
    print(f"Master time: {state.master_time:.3f}s  locked={state.is_locked}")

cfg = EngineConfig(
    protocols=["MIDI", "OSC", "ARTNET"],
    log_file="show_data_myevent.log",
    engine_mode="AI-Enhanced",
    sync_callback=on_sync,
)

monitor = LatencyMonitor(window_seconds=30)
monitor.start()

engine = TimecodeEngine(cfg)
engine.start()

# … your show runs here …

engine.stop()
monitor.stop()
```

### 3. Open the landing page

Open `index.html` in any modern browser, or serve it with any static file server:

```bash
python -m http.server 8080
```

Then navigate to `http://localhost:8080`.

---

## Engine Modes

| Mode | Description |
|---|---|
| **Legacy** | Proven stable fixed-gain latency compensation |
| **AI-Enhanced** | Adaptive Kalman filter tuned from show log data |

Switch modes in `EngineConfig(engine_mode="Legacy")` or from the dashboard settings panel.

---

## Supported Protocols

| Protocol | Transport | Notes |
|---|---|---|
| MIDI / MTC | Serial / USB | Quarter-frame assembly, auto-detect ports |
| OSC | UDP | `/timecode` address, configurable port |
| Art-Net 4 | UDP 6454 | ArtTimecode + ArtSync opcodes |
| SMPTE / LTC | Audio / GPIO | Software LTC decoder (25/30 fps) |
| Dante (AES67) | Ethernet | PTP network timestamping |

---

## Platform Notes

### macOS
- CoreMIDI is used automatically via `python-rtmidi`.
- Bonjour is used for device discovery on the LAN.

### Windows
- WDM / ASIO audio drivers supported.
- Run as Administrator for low-latency network socket access.

---

## Update Policy

Time Solution uses a **weights-file** update model for the AI engine:

1. A show produces a `show_data_<event>.log` file automatically.
2. The log is used offline to retrain the Kalman parameters.
3. New parameters are distributed as `brain_vX.Y.dat`.
4. Drop the new `.dat` file next to the engine; restart → instant upgrade.
5. USB offline update and cloud (4G/5G) push modes are supported.

---

## Changelog

### v2.0.0 (2026-03)
- Initial public release of Time Solution engine and landing page.
- Kalman-filter latency compensation (Optimisation for fibre-optic networks).
- Multi-protocol hub: MIDI, OSC, Art-Net, SMPTE.
- Real-time latency waveform dashboard (30-second rolling window).
- A/B engine hot-swap (Legacy / AI-Enhanced).
- Cross-platform: Windows 10/11 · macOS (Apple Silicon & Intel).

---

## Licence

Proprietary — © 2026 Time Solution. Developed by **Audio Solutions Network**. All rights reserved.  
Contact [contact@timesolution.io](mailto:contact@timesolution.io) for licensing enquiries.
