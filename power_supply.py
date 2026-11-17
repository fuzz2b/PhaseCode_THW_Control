"""Keithley 2280S supply over VISA."""

import time

import pyvisa

import devices
import settings

from typing import cast

import pyvisa
from pyvisa.resources import MessageBasedResource


class PowerSupply:
    """Use as a context manager so the output is always switched off on exit."""

    def __init__(self, address=None):
        self._limits = settings.get("limits")
        address = address or devices.find_power_supply_address() 
        self._rm = pyvisa.ResourceManager()
        self._inst = cast(MessageBasedResource, self._rm.open_resource(address))  #Just error prevention

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self):
        try:
            self._inst.write(":OUTP OFF")
        finally:
            try:
                self._inst.close()
            finally:
                self._rm.close()

    def query(self, command):
        return self._inst.query(command).strip()

    def errors(self):
        return self.query("SYST:ERR?")

    def reset(self):
        self._inst.write("*RST")
        time.sleep(1)

    def set_protection(self):
        """Apply the protection levels from config.json; return the readbacks."""
        self._inst.write(f":VOLT:PROT:LEV {self._limits['protection_voltage_V']}")
        self._inst.write(f":CURR:PROT:LEV {self._limits['protection_current_A']}")
        return self.query(":VOLT:PROT:LEV?"), self.query(":CURR:PROT:LEV?")

    def set_current_range(self, current_A):
        """Fix the range above the operating current (after *RST the active
        function is CONC, not CURR, so both are set)."""
        for func in ("CONC", "CURR"):
            self._inst.write(f":SENS:{func}:RANG:AUTO OFF")
            self._inst.write(f":SENS:{func}:RANG {current_A}")
        return self.query(":SENS:CONC:RANG?")

    def set_setpoint(self, voltage=None, current=None):
        """Set voltage and/or current, refusing anything above the configured limits."""
        if voltage is not None:
            if not 0 <= voltage <= self._limits["max_voltage_V"]:
                raise ValueError(f"Voltage must be 0 to {self._limits['max_voltage_V']} V.")
            self._inst.write(f":VOLT {voltage}")
        if current is not None:
            if not 0 <= current <= self._limits["max_current_A"]:
                raise ValueError(f"Current must be 0 to {self._limits['max_current_A']} A.")
            self._inst.write(f":CURR {current}")

    def output(self, on):
        self._inst.write(":OUTP ON" if on else ":OUTP OFF")

    def trigger(self):
        """Apply a pending source change immediately. The old set_power.py sent
        both :INIT and *TRG so this works whichever the instrument waits on."""
        self._inst.write(":INIT")
        self._inst.write("*TRG")