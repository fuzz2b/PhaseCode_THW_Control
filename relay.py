"""
Denkovi 16-relay board: select a wire by switching on the voltage and sense
relays for its two pins. The pin-to-relay map comes from data/PinsToRelay.csv.
"""

import pandas as pd
import time

import dae_RelayBoard
import devices
import settings

SETTLE_S = 0.5


def _load_pin_map():
    path = settings.ROOT_DIR / settings.get("relay", "pins_csv")
    df = pd.read_csv(path)
    return {
        int(pin): (int(v), int(s))
        for pin, v, s in zip(df["Pin No"], df["Voltage"], df["Sense"])
    }


def pins_to_relays(pin1, pin2):
    """The set of 4 relay numbers (voltage + sense for each pin)."""
    pin_map = _load_pin_map()
    print(pin_map)
    relays = set()
    for pin in (pin1, pin2):
        if pin not in pin_map:
            raise ValueError(f"Pin number {pin} not found in the pin map")
        relays.update(pin_map[pin])
    return relays


def _connect():
    board = dae_RelayBoard.DAE_RelayBoard(dae_RelayBoard.DAE_RELAYBOARD_TYPE_16)
    board.initialise(devices.find_relay_port())
    return board


def set_relays(pin1, pin2):
    """Switch on only the relays for the two pins; all others off."""
    active = pins_to_relays(pin1, pin2)  # validate before touching hardware
    board = _connect()
    try:
        board.setAllStatesOff()
        board.setStates({r: (r in active) for r in range(1, board.getNumRelays() + 1)})
        time.sleep(SETTLE_S)
    finally:
        board.disconnect()


def all_off():
    board = _connect()
    try:
        board.setAllStatesOff()
    finally:
        board.disconnect()