"""
Measurement sequences. No GUI code: the GUI calls these from worker threads.

  run_thw_test()        soft-start the supply while the Joulescope captures
  measure_resistance()  step the supply and read V and I several times
  shutdown_all()        switch off the supply, Joulescope and relays
"""

import csv
import statistics
import time
from datetime import datetime

import devices
import relay
import traceback
import settings
from joulescope_utils.js320_driver import scan_require_one
from joulescope_utils.plot_cal import export_csv
from power_supply import PowerSupply

ARM_MARGIN_S = 0.02     # lets the power-on edge land inside the capture
SETTLE_DELAY_S = 0.1    # output settles at the low starting limit before the step

RESISTANCE_CSV = settings.ROOT_DIR / "resistance_log.csv"
RESISTANCE_HEADER = ["timestamp", "wire", "measurement_index", "current_A", "voltage_V", "resistance_ohm"]
DEFAULT_RESISTANCE_VOLTAGE_V = 10.0
DEFAULT_RESISTANCE_CURRENT_A = 0.4
DEFAULT_N_MEASURE = 5
DEFAULT_SETTLE_S = 1.0
DEFAULT_COOLDOWN_S = 10.0


def _sleep(seconds, should_stop):
    """Sleep in short steps so Stop is noticed quickly. False if stopped."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop():
            return False
        time.sleep(0.1)
    return True


# -- THW test (replaces set_power.py + long_measure.py) ------------------------
def run_thw_test(current, duration, save_path=None, log=print):
    """Capture `duration` s on the Joulescope while the supply soft-starts and
    steps to `current`. Returns (data, sampling_frequency).

    The Joulescope is armed before the output switches on, as the old READY
    handshake did, and the supply is always switched off afterwards.
    """
    cfg = settings.get("thw_test")
    if duration > cfg["max_duration_s"]:
        raise ValueError(f"Duration must be at most {cfg['max_duration_s']} s.")

    with PowerSupply() as psu:
        log("Configuring safety limits...")
        psu.reset()
        volt_prot, curr_prot = psu.set_protection()
        log(f"Protection readback: VOLT {volt_prot} V, CURR {curr_prot} A")
        log(f"Current range readback: {psu.set_current_range(current)} A")

        log(f"Output {cfg['voltage_V']} V with a {cfg['inrush_limit_A']} A starting limit")
        psu.set_setpoint(voltage=cfg["voltage_V"], current=cfg["inrush_limit_A"])

        device = scan_require_one(config="auto")
        with device:
            device.start()                 # capturing before the supply turns on
            time.sleep(ARM_MARGIN_S)
            psu.output(True)
            log("Joulescope armed, output ON")
            time.sleep(SETTLE_DELAY_S)

            psu.set_setpoint(current=current)
            psu.trigger()
            log(f"Current set to {current} A, capturing {duration} s...")
            log(f"CURR readback: {psu.query(':CURR?')} A, error queue: {psu.errors()}")

            device.wait_for_duration(duration)
            data = device.stop()

        psu.output(False)
        log("Output OFF")
        fs = device.sampling_frequency

    if save_path:
        export_csv(data, fs, filepath=save_path)
        log(f"Saved {save_path}")
    return data, fs


# -- resistance -----------------------------------------------------------------
def _append_resistance_row(row):
    new_file = not RESISTANCE_CSV.exists()
    with open(RESISTANCE_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(RESISTANCE_HEADER)
        writer.writerow(row)


def measure_resistance(
    wire,
    voltage=DEFAULT_RESISTANCE_VOLTAGE_V,
    current=DEFAULT_RESISTANCE_CURRENT_A,
    n_measure=DEFAULT_N_MEASURE,
    settle_s=DEFAULT_SETTLE_S,
    cooldown_s=DEFAULT_COOLDOWN_S,
    log=print,
    should_stop=lambda: False,
):
    """Take n_measure readings, appending each to resistance_log.csv.
    Returns (mean, std, n), or None if nothing was measured."""
    resistances = []
    with PowerSupply() as psu:
        device = scan_require_one(config="auto")
        with device:
            for i in range(1, n_measure + 1):
                if should_stop():
                    log("Stopped.")
                    break

                psu.set_setpoint(voltage=voltage, current=current)  # also enforces the limits
                psu.output(True)
                if not _sleep(settle_s, should_stop):
                    log("Stopped.")
                    break

                data = device.read(contiguous_duration=0.001)
                i_a, v_v = (float(x) for x in data[-1, :])

                psu.set_setpoint(voltage=0, current=0)

                if i_a == 0:
                    log(f"[{i}/{n_measure}] zero current measured, reading skipped")
                else:
                    resistance = v_v / i_a
                    resistances.append(resistance)
                    _append_resistance_row([datetime.now().isoformat(), wire, i, i_a, v_v, resistance])
                    log(f"[{i}/{n_measure}] {i_a:.6g} A, {v_v:.6g} V, {resistance:.6g} Ω")

                if i < n_measure and not _sleep(cooldown_s, should_stop):
                    log("Stopped.")
                    break

    if not resistances:
        return None
    return statistics.mean(resistances), statistics.pstdev(resistances), len(resistances)


# -- shutdown (replaces shutdown.py) ---------------------------------------------
def _supply_off():
    """Switch off every Keithley found, so the wrong serial can never be missed."""
    supplies = devices.list_power_supplies()
    if not supplies:
        raise RuntimeError("no Keithley supply found")
    for supply in supplies:
        PowerSupply(supply["address"]).close()  # close() switches the output off first


def _joulescope_stop():
    scan_require_one(config="auto").stop()


def shutdown_all(log=print):
    """Switch everything off. Each step runs even if an earlier one fails.
    Returns True only if every step succeeded."""
    all_ok = True
    for name, step in (("Keithley", _supply_off), ("Joulescope", _joulescope_stop), ("Relays", relay.all_off)):
        try:
            step()
            log(f"{name} off")
        except Exception as exc:
            all_ok = False
            traceback.print_exc()  
            log(f"X {name} failed: {exc}")
    return all_ok