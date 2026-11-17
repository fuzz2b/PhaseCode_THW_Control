# THW Testing Control

GUI for running THW tests: it selects a sensor wire through a Denkovi relay board, powers it from the Keithley supply, and captures the response with a Joulescope . It can also measure the resistance of the wire.

## Hardware

- Keithley 2280S power supply (USB -> check settings on supply)
- Joulescope JS220 (Should work with multiple versions)
- Denkovi 16-relay USB board 

## Setup

Python 3.12 or 3.13 is recommended. Use a virtual environment:

```
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Running

```
python main_gui.py
```

## Tabs

- **THW Test:** pick a wire, the high current step and the duration, then press Execute. It sets the relays, soft-starts the supply while the Joulescope captures, then switches the output off. The capture can be saved as a CSV and plotted. **Shutdown** switches off the supply, Joulescope and relays.
- **Resistance:** sets the relay for a wire, then measures V and I several times and reports the mean ± standard deviation. Each reading is appended to `resistance_log.csv`. **Stop** ends it after the current step, and **Shutdown** stops it and switches everything off.
- **Devices:** shows the settings from `config.json` and the instruments that were detected.

## Configuration

Settings live in `config.json`. Anything left out falls back to a default.

| Setting | Meaning |
|---|---|
| `relay.com_port` | `"auto"` finds the relay board's COM port, or give one such as `"COM3"` |
| `relay.pins_csv` | Pin-to-relay map (`data/PinsToRelay.csv`) |
| `power_supply.visa_address` | `"auto"` or a full VISA address |
| `power_supply.serial` | Pick a supply by serial number if more than one is connected |
| `limits` | Maximum voltage and current, and the supply's protection levels |
| `thw_test` | Test voltage, starting current limit and maximum duration |

To see what is connected, run:

```
python devices.py
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
