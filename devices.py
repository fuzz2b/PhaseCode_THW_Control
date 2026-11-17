"""
Finds the instruments: Keithley supply (VISA) and relay board (COM port).

    python devices.py                      show what is connected
    python devices.py --use-supply 4400194 remember that supply in config.json
"""

import argparse

import pyvisa
from serial.tools import list_ports

import settings

KEITHLEY_VID = "0x05e6"
KEITHLEY_PID = "0x2280"
FTDI_VID = 0x0403  # assumption: the relay board's USB-serial chip is FTDI

# import pyvisa
# rm = pyvisa.ResourceManager()
# print(rm.list_resources())

def list_power_supplies():
    """Every Keithley 2280 on USB: [{'address', 'serial', 'idn'}, ...]."""
    rm = pyvisa.ResourceManager()
    print(rm.list_resources())
    supplies = []
    try:
        for address in rm.list_resources():
            parts = address.split("::")  # USB0::0x05E6::0x2280::4400194::INSTR
            if len(parts) < 4 or parts[1].lower() != KEITHLEY_VID or parts[2].lower() != KEITHLEY_PID:
                continue
            idn = ""
            try:
                inst = rm.open_resource(address)
                try:
                    idn = inst.query("*IDN?").strip() #type: ignore
                finally:
                    inst.close()
            except Exception as exc:  # e.g. already open in another program
                idn = f"(could not query: {exc})"
            supplies.append({"address": address, "serial": parts[3], "idn": idn})
    finally:
        rm.close()
    return supplies


def list_serial_ports():
    return [
        {"port": p.device, "description": p.description, "ftdi": p.vid == FTDI_VID}
        for p in list_ports.comports()
    ]


def find_power_supply_address():
    cfg = settings.get("power_supply")
    if cfg["visa_address"] and cfg["visa_address"] != "auto":
        return cfg["visa_address"]

    found = list_power_supplies()
    if cfg["serial"]:
        found = [s for s in found if s["serial"] == str(cfg["serial"])]

    if len(found) == 1:
        return found[0]["address"]
    names = [f"{s['serial']} ({s['address']})" for s in found] or "none"
    raise RuntimeError(
        f"Expected one Keithley 2280, found {len(found)}: {names}. "
        f'Run "python devices.py", then set "power_supply" -> "serial" in config.json.'
    )


def find_relay_port():
    configured = settings.get("relay", "com_port")
    if configured and configured != "auto":
        return configured

    ports = list_serial_ports()
    ftdi = [p for p in ports if p["ftdi"]]
    if len(ftdi) == 1:
        return ftdi[0]["port"]

    seen = ", ".join(f"{p['port']} ({p['description']})" for p in ports) or "none"
    raise RuntimeError(
        f"Could not pick the relay board's COM port ({len(ftdi)} FTDI ports). "
        f'Ports found: {seen}. Set "relay" -> "com_port" in config.json.'
    )


def describe():
    """Text summary of what is connected (used by the GUI's Devices panel)."""
    lines = []
    supplies = list_power_supplies()
    lines.append(f"Keithley 2280 supplies: {len(supplies)}")
    for s in supplies:
        lines.append(f"  serial {s['serial']}  {s['idn']}")
    ports = list_serial_ports()
    lines.append(f"Serial ports: {len(ports)}")
    for p in ports:
        tag = "  <- FTDI, likely the relay board" if p["ftdi"] else ""
        lines.append(f"  {p['port']}  {p['description']}{tag}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="List connected instruments.")
    parser.add_argument("--use-supply", metavar="SERIAL",
                        help="remember this Keithley serial number in config.json")
    args = parser.parse_args()

    print(describe())

    if args.use_supply:
        if args.use_supply not in [s["serial"] for s in list_power_supplies()]:
            print(f"\nNo connected supply has serial {args.use_supply}; config not changed.")
            return
        settings.update("power_supply", "serial", args.use_supply)
        print(f"\nSaved supply serial {args.use_supply} to config.json.")


if __name__ == "__main__":
    main()