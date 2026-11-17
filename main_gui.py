"""
GUI for the THW testing control toolkit.

Two tabs:
  - THW Test: pick a wire, set the current and duration, run the capture.
  - Resistance: set the relay for a wire, then measure its resistance.

Run with:  python main_gui.py
"""

import sys
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QStyleFactory, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

import devices
import measurements
import traceback
import relay
import settings
from joulescope_utils.plot_cal import plot_iv

ROOT_DIR = Path(__file__).resolve().parent
LABEL_WIDTH = 140

WIRE_OPTIONS = [
    ("Wire 1 (Pins 1 & 2)", (1, 2)),
    ("Wire 2 (Pins 2 & 3)", (2, 3)),
    ("Wire 3 (Pins 3 & 4)", (3, 4)),
    ("Wire 4 (Pins 4 & 5)", (4, 5)),
    ("Wire 5 (Pins 5 & 6)", (5, 6)),
]
DEFAULT_WIRE = "Wire 1 (Pins 1 & 2)"

MAX_CURRENT_A = settings.get("limits", "max_current_A")
MAX_VOLTAGE_V = settings.get("limits", "max_voltage_V")
MAX_DURATION_S = settings.get("thw_test", "max_duration_s")


# -- shared helpers -----------------------------------------------------------
def labeled_row(text, widget):
    """A fixed-width label on the left with a widget filling the rest."""
    row = QHBoxLayout()
    row.setSpacing(6)
    label = QLabel(text)
    label.setFixedWidth(LABEL_WIDTH)
    row.addWidget(label)
    row.addWidget(widget, stretch=1)
    return row


def group(title, *items):
    """A titled box (like ttk.LabelFrame). Items are layouts or widgets."""
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    layout.setSpacing(6)
    for item in items:
        if isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    return box


def make_wire_combo():
    combo = QComboBox()
    combo.addItem("— select —", userData=None)
    for name, pins in WIRE_OPTIONS:
        combo.addItem(name, userData={"pins": pins})
    idx = combo.findText(DEFAULT_WIRE)
    if idx != -1:
        combo.setCurrentIndex(idx)
    return combo


def make_spin(minimum, maximum, value, decimals=None, step=None, suffix=""):
    """A QSpinBox (no decimals) or QDoubleSpinBox (with decimals)."""
    spin = QDoubleSpinBox() if decimals is not None else QSpinBox()
    if decimals is not None:
        spin.setDecimals(decimals) # type: ignore
    spin.setRange(minimum, maximum)
    if step is not None:
        spin.setSingleStep(step)
    spin.setSuffix(suffix)
    spin.setValue(value)
    return spin


class LogView(QTextEdit):
    """Read-only output log with coloured message helpers."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText("Output will appear here…")
        self.setFont(QFont("Consolas", 9))

    def plain(self, msg):
        self.append(msg)

    def info(self, msg):
        self.append(f'<span style="color:#555555;">{msg}</span>')

    def ok(self, msg):
        self.append(f'<span style="color:#1a7f37;font-weight:bold;">{msg}</span>')

    def err(self, msg):
        self.append(f'<span style="color:#c0392b;font-weight:bold;">{msg}</span>')

    def warn(self, msg):
        self.append(f'<span style="color:#b36b00;">{msg}</span>')


def log_group(log):
    return group("Log", log)


class Worker(QThread):
    """Runs task(log, should_stop) in the background.

    Emits info(text) for log lines, result(value) on success and error(text)
    on failure. should_stop() turns True after requestInterruption().
    """
    info = pyqtSignal(str)
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, task):
        super().__init__()
        self._task = task

    def run(self):
        try:
            self.result.emit(self._task(self.info.emit, self.isInterruptionRequested))
        except Exception as exc:
            traceback.print_exc()  
            self.error.emit(f"X {exc}")


# -- tabs ---------------------------------------------------------------------
class ThwTestTab(QWidget):
    """Set the relay for a wire, then run the supply and Joulescope capture."""

    def __init__(self):
        super().__init__()
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        self.wire_combo = make_wire_combo()
        root.addWidget(group("Hardware", labeled_row("Wire Selection", self.wire_combo)))

        self.current_spin = make_spin(0.001, MAX_CURRENT_A, 0.4, decimals=3, step=0.05, suffix=" A")
        self.duration_spin = make_spin(1, MAX_DURATION_S, min(60, MAX_DURATION_S), suffix=" s")
        self.save_check = QCheckBox("Save CSV (asks where when you press Execute)")
        self.save_check.setChecked(True)
        self.plot_check = QCheckBox("Show plot when finished")
        self.plot_check.setChecked(True)
        root.addWidget(group(
            "Test",
            labeled_row("High Current Step", self.current_spin),
            labeled_row("Test Duration", self.duration_spin),
            self.save_check,
            self.plot_check,
        ))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch(1)
        self.btn_shutdown = QPushButton("Shutdown")
        self.btn_shutdown.clicked.connect(self._on_shutdown)
        self.btn_execute = QPushButton("Execute")
        self.btn_execute.clicked.connect(self._on_execute)
        btn_row.addWidget(self.btn_shutdown)
        btn_row.addWidget(self.btn_execute)
        root.addLayout(btn_row)

        self.log = LogView()
        root.addWidget(log_group(self.log), stretch=1)



    # -- running --
    def busy(self):
        return bool(self._worker and self._worker.isRunning())

    def _set_running(self, running):
        self.btn_execute.setEnabled(not running)
        self.btn_shutdown.setEnabled(not running)
        self.btn_execute.setText("Running…" if running else "Execute")

    def _on_execute(self):
        if self.busy():
            return
        data = self.wire_combo.currentData()
        if not data:
            self.log.warn("Select a wire first.")
            return

        save_path = None
        if self.save_check.isChecked():
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Joulescope measurement CSV",
                time.strftime("long_measure_%Y%m%d_%H%M%S.csv"), "CSV files (*.csv)",
            )
            if not save_path:
                self.log.warn("Cancelled: no save location chosen.")
                return

        pins = data["pins"]
        current = self.current_spin.value()
        duration = self.duration_spin.value()
        self._show_plot = self.plot_check.isChecked()

        def task(log, should_stop):
            log(f"Setting relays for pins {pins[0]} and {pins[1]}...")
            relay.set_relays(*pins)  # raises on failure, so the test never starts
            log("✓ Relays set")
            return measurements.run_thw_test(current, duration, save_path, log=log)

        worker = Worker(task)
        worker.info.connect(self.log.info)
        worker.result.connect(self._on_test_done)
        worker.error.connect(self.log.err)
        worker.finished.connect(lambda: self._set_running(False))
        self._worker = worker
        self._set_running(True)
        worker.start()

    def _on_test_done(self, outcome):
        self.log.ok("✓ Test complete")
        if self._show_plot and outcome:
            try:
                plot_iv(*outcome)  # on the GUI thread: matplotlib must not run in a worker
            except Exception as exc:
                traceback.print_exc()  
                self.log.err(f"X Plot failed: {exc}")

    def _on_shutdown(self):
        reply = QMessageBox.question(
            self, "Confirm Shutdown",
            "Switch off the supply, Joulescope and relays?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        worker = Worker(lambda log, stop: measurements.shutdown_all(log))
        worker.info.connect(self.log.info)
        worker.result.connect(
            lambda ok: self.log.ok("✓ All off") if ok else self.log.err("✗ Some steps failed, see above")
        )
        worker.error.connect(self.log.err)
        worker.finished.connect(lambda: self._set_running(False))
        self._worker = worker
        self._set_running(True)
        worker.start()


class ResistanceTab(QWidget):
    """Set the relay for a wire, then measure its resistance."""

    def __init__(self):
        super().__init__()
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        self.wire_combo = make_wire_combo()
        root.addWidget(group("Hardware", labeled_row("Wire Selection", self.wire_combo)))

        self.voltage_spin = make_spin(
            0.1, MAX_VOLTAGE_V, measurements.DEFAULT_RESISTANCE_VOLTAGE_V, decimals=2, step=0.5, suffix=" V")
        self.current_spin = make_spin(
            0.001, MAX_CURRENT_A, measurements.DEFAULT_RESISTANCE_CURRENT_A, decimals=3, step=0.05, suffix=" A")
        self.count_spin = make_spin(1, 100, measurements.DEFAULT_N_MEASURE)
        self.settle_spin = make_spin(0.1, 60, measurements.DEFAULT_SETTLE_S, decimals=1, step=0.5, suffix=" s")
        self.cooldown_spin = make_spin(0, 600, measurements.DEFAULT_COOLDOWN_S, decimals=1, step=1, suffix=" s")
        root.addWidget(group(
            "Measurement",
            labeled_row("Voltage", self.voltage_spin),
            labeled_row("Current Limit", self.current_spin),
            labeled_row("Measurements", self.count_spin),
            labeled_row("Settle Time", self.settle_spin),
            labeled_row("Cool-down", self.cooldown_spin),
        ))

        self.result_label = QLabel("No measurement yet")
        font = self.result_label.font()
        self.result_label.setFont(font)
        root.addWidget(group("Result", self.result_label))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch(1)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_start = QPushButton("Start")
        self.btn_start.clicked.connect(self._on_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_start)
        root.addLayout(btn_row)

        self.log = LogView()
        root.addWidget(log_group(self.log), stretch=1)

    def busy(self):
        return bool(self._worker and self._worker.isRunning())

    def _on_start(self):
        if self.busy():
            return
        data = self.wire_combo.currentData()
        if not data:
            self.log.warn("Select a wire first.")
            return

        pins = data["pins"]
        wire_name = self.wire_combo.currentText()
        voltage = self.voltage_spin.value()
        current = self.current_spin.value()
        n_measure = int(self.count_spin.value())
        settle_s = self.settle_spin.value()
        cooldown_s = self.cooldown_spin.value()

        def task(log, should_stop):
            log(f"Setting relays for pins {pins[0]} and {pins[1]}...")
            relay.set_relays(*pins)  # raises on failure, so the measurement never starts
            log("✓ Relays set")
            return measurements.measure_resistance(
                wire=wire_name, voltage=voltage, current=current, n_measure=n_measure,
                settle_s=settle_s, cooldown_s=cooldown_s, log=log, should_stop=should_stop,
            )

        worker = Worker(task)
        worker.info.connect(self.log.info)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        self._worker = worker

        self.result_label.setText("Measuring…")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        worker.start()

    def _on_stop(self):
        if self.busy():
            self.log.warn("Stopping after the current step…")
            self._worker.requestInterruption() #type: ignore

    def _on_result(self, outcome):
        if not outcome:
            self.result_label.setText("No result")
            return
        mean, std, n = outcome
        self.result_label.setText(f"{mean:.6g} ± {std:.3g} Ω  ({n} measurements)")
        self.log.ok(f"The resistance is {mean:.6g} ± {std:.3g} Ω")

    def _on_error(self, message):
        self.result_label.setText("Failed")
        self.log.err(message)

    def _on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def stop_and_wait(self):
        """Used on window close so the supply output is always switched off."""
        if self.busy():
            self._worker.requestInterruption() #type: ignore

            self._worker.wait(15000) #type: ignore



#--Devices 
class DevicesTab(QWidget):
    """Shows the connected instruments and the settings in config.json."""

    def __init__(self, is_busy):
        super().__init__()
        self._is_busy = is_busy  # returns True while a test is using the instruments
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        self.config_text = QTextEdit()
        self.config_text.setReadOnly(True)
        self.config_text.setFont(QFont("Consolas", 9))
        self.config_text.setFixedHeight(130)
        root.addWidget(group("Configured (config.json)", self.config_text))

        self.devices_text = QTextEdit()
        self.devices_text.setReadOnly(True)
        self.devices_text.setFont(QFont("Consolas", 9))
        root.addWidget(group("Detected", self.devices_text), stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_scan = QPushButton("Rescan")
        self.btn_scan.clicked.connect(self.scan)
        btn_row.addWidget(self.btn_scan)
        root.addLayout(btn_row)

        self._show_config()

    def _show_config(self):
        relay_cfg = settings.get("relay")
        supply_cfg = settings.get("power_supply")
        limits = settings.get("limits")
        self.config_text.setPlainText(
            f"Relay COM port:   {relay_cfg['com_port']}\n"
            f"Supply address:   {supply_cfg['visa_address']}\n"
            f"Supply serial:    {supply_cfg['serial'] or '(not set)'}\n"
            f"Max voltage:      {limits['max_voltage_V']} V\n"
            f"Max current:      {limits['max_current_A']} A\n"
            f"Config file:      {settings.CONFIG_PATH}"
        )

    def scan(self):
        if self._worker and self._worker.isRunning():
            return
        if self._is_busy():
            # Querying the supply while a test runs could disturb the measurement.
            self.devices_text.setPlainText("A test is running. Rescan when it has finished.")
            return
        self.devices_text.setPlainText("Scanning…")
        self.btn_scan.setEnabled(False)
        worker = Worker(lambda log, stop: devices.describe())
        worker.result.connect(self.devices_text.setPlainText)
        worker.error.connect(self.devices_text.setPlainText)
        worker.finished.connect(lambda: self.btn_scan.setEnabled(True))
        self._worker = worker
        worker.start()

    def busy(self):
        return bool(self._worker and self._worker.isRunning())


# -- application --------------------------------------------------------------
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("THW Test Control")
        self.setMinimumSize(660, 600)
        icon = ROOT_DIR / "data" / "phase_icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        tabs = QTabWidget()
        self.test_tab = ThwTestTab()
        self.resistance_tab = ResistanceTab()
        self.devices_tab = DevicesTab(
            is_busy=lambda: self.test_tab.busy() or self.resistance_tab.busy()
        )
        tabs.addTab(self.test_tab, "THW Test")
        tabs.addTab(self.resistance_tab, "Resistance")
        tabs.addTab(self.devices_tab, "Devices")
        self.setCentralWidget(tabs)

        QTimer.singleShot(0, self.devices_tab.scan)  # scan once the window is up

    def closeEvent(self, event):
        if self.test_tab.busy():
            QMessageBox.warning(
                self, "Test running",
                "A test is running. The capture can't be interrupted, so wait for it to finish.",
            )
            event.ignore()
            return
        self.resistance_tab.stop_and_wait()
        if self.devices_tab.busy():
            self.devices_tab._worker.wait(5000) #type: ignore

        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    # Native Windows look (closest to Tk's default); Fusion elsewhere.
    styles = {name.lower(): name for name in QStyleFactory.keys()}
    app.setStyle(styles.get("windowsvista", "Fusion"))
    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()