# Copyright 2019 Jetperch LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
js320_driver.py

The `joulescope` PyPI package (v1.5.0) only knows how to talk to the JS110
and JS220 -- it has no JS320 support. This module provides a small wrapper
around `pyjoulescope_driver.Driver` (the low-level package `joulescope`
itself is built on) that exposes just enough of the old `joulescope.Device`
API -- context-manager open/close, parameter_set, read(), sampling_frequency
-- for this project's scripts to keep working with a JS320 with minimal
changes.

start()/wait_for_duration()/stop() split the all-in-one read() into its
parts so a caller can begin acquisition first and only afterwards trigger
some other piece of hardware (e.g. a power supply), so that hardware's
turn-on transient lands inside the capture instead of being started before
the Joulescope is listening.

The stream topic names used here (s/i/ctrl, s/i/!data, s/v/ctrl, s/v/!data,
h/fs, decimate_factor/sample_id/sample_rate/data payload keys) come straight
from pyjoulescope_driver's own Record helper (pyjoulescope_driver/record.py),
which drives any connected device -- JS110, JS220, or JS320 -- identically.
"""

import logging
import time

import numpy as np
from pyjoulescope_driver import Driver

log = logging.getLogger(__name__)

DEFAULT_SAMPLING_FREQUENCY = 100_000  # 100 kSPS -- 10x decimated from the 1 MSPS default to keep capture files smaller


def scan_require_one(config='auto'):
    """Find the single connected Joulescope and wrap it as a JS320Device.

    Mirrors joulescope.scan_require_one() closely enough to be a drop-in
    replacement for the scripts in this project.

    :param config: 'auto' to configure the device for typical operation on
        open, or 'ignore' to leave its current configuration untouched.
    :raise RuntimeError: if zero or more than one device is found.
    """
    driver = Driver()
    device_paths = driver.device_paths()
    if len(device_paths) == 0:
        driver.finalize()
        raise RuntimeError('No Joulescope device found')
    if len(device_paths) > 1:
        driver.finalize()
        raise RuntimeError(f'Found {len(device_paths)} Joulescope devices, expected 1: {device_paths}')
    return JS320Device(driver, device_paths[0], config=config)


class JS320Device:
    """joulescope.Device-like wrapper for a JS320 built on pyjoulescope_driver."""

    def __init__(self, driver, device_path, config='auto'):
        self._driver = driver
        self._path = device_path
        self._config = config
        self.is_open = False
        self.sampling_frequency = DEFAULT_SAMPLING_FREQUENCY
        self.buffer_duration = 30  # seconds, kept only for parameter_set() parity
        self._is_streaming = False
        self._i_chunks = []
        self._v_chunks = []
        self._i_count = 0
        self._v_count = 0
        # pyjoulescope_driver's unsubscribe() requires the exact same bound
        # method *object* that was passed to subscribe() -- accessing
        # self._on_i fresh each time constructs a new (if equal) bound
        # method object, which unsubscribe() then fails to find. Cache the
        # bound methods once so start() and stop() share the same instance.
        self._on_i_cb = self._on_i
        self._on_v_cb = self._on_v

    def __str__(self):
        return self._path

    def _topic(self, topic):
        return f'{self._path}/{topic}'

    def publish(self, topic, value, timeout=None):
        self._driver.publish(self._topic(topic), value, timeout)

    def query(self, topic, timeout=None):
        return self._driver.query(self._topic(topic), timeout)

    def parameter_set(self, name, value):
        """Minimal parameter_set for API parity with the legacy joulescope package."""
        if name == 'buffer_duration':
            self.buffer_duration = value
        else:
            raise KeyError(f'Unsupported parameter: {name}')

    def open(self):
        self._driver.open(self._path)
        self.is_open = True
        try:
            self.publish('h/fs', self.sampling_frequency)
        except Exception:
            log.warning('Could not set sampling frequency on %s', self._path)
        if self._config in (None, 'auto'):
            for topic in ('s/i/range/mode', 's/v/range/mode'):
                try:
                    self.publish(topic, 'auto')
                except Exception:
                    log.warning('Could not set %s on %s', topic, self._path)
        return self

    def close(self):
        if self.is_open:
            try:
                self._driver.close(self._path)
            finally:
                self.is_open = False
        self._driver.finalize()

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _on_i(self, topic, value):
        self._i_chunks.append(value['data'])
        self._i_count += len(value['data'])

    def _on_v(self, topic, value):
        self._v_chunks.append(value['data'])
        self._v_count += len(value['data'])

    def start(self):
        """Begin streaming current/voltage samples.

        Follow with wait_for_duration() to block until enough samples have
        arrived, then stop() to end the stream and get the captured data.
        Splitting acquisition start out from read() lets a caller start the
        Joulescope listening, then trigger other hardware, so that other
        hardware's turn-on transient lands inside the capture.
        """
        if self._is_streaming:
            raise RuntimeError('Already streaming; call stop() first')
        self._i_chunks = []
        self._v_chunks = []
        self._i_count = 0
        self._v_count = 0
        self._driver.subscribe(self._topic('s/i/!data'), 'pub', self._on_i_cb)
        self._driver.subscribe(self._topic('s/v/!data'), 'pub', self._on_v_cb)
        self.publish('s/i/ctrl', 1)
        self.publish('s/v/ctrl', 1)
        self._is_streaming = True

    def wait_for_duration(self, capture_duration):
        """Block until `capture_duration` seconds of samples have arrived since start()."""
        target = max(1, int(round(capture_duration * self.sampling_frequency)))
        t_end = time.time() + capture_duration + 5.0  # generous safety margin
        while (self._i_count < target or self._v_count < target) and time.time() < t_end:
            time.sleep(0.01)

    def stop(self):
        """Stop streaming and return everything captured since start().

        :return: An (N, 2) np.ndarray(float32) with columns [current, voltage],
            in the order captured (earliest sample first).
        """
        if not self._is_streaming:
            return np.zeros((0, 2), dtype=np.float32)
        self.publish('s/i/ctrl', 0)
        self.publish('s/v/ctrl', 0)
        self._driver.unsubscribe(self._topic('s/i/!data'), self._on_i_cb)
        self._driver.unsubscribe(self._topic('s/v/!data'), self._on_v_cb)
        self._is_streaming = False

        current = np.concatenate(self._i_chunks) if self._i_chunks else np.zeros(0, dtype=np.float32)
        voltage = np.concatenate(self._v_chunks) if self._v_chunks else np.zeros(0, dtype=np.float32)
        n = min(len(current), len(voltage))
        return np.column_stack([current[:n], voltage[:n]]).astype(np.float32)

    def read(self, duration=None, contiguous_duration=None):
        """Capture current/voltage samples and return the most recent
        `duration`/`contiguous_duration` seconds worth.

        :param duration: The capture duration in seconds.
        :param contiguous_duration: The contiguous capture duration in
            seconds (most recent samples with no gaps).
        :return: An (N, 2) np.ndarray(float32) with columns [current, voltage].

        Mirrors joulescope.Device.read(out_format='calibrated'). For a
        capture that needs to start before some other event (e.g. a power
        supply turning on), use start()/wait_for_duration()/stop() directly
        instead -- this method trims to only the most recent samples, which
        would otherwise cut off exactly the pre-trigger data you wanted.
        """
        capture_duration = contiguous_duration if contiguous_duration is not None else duration
        if capture_duration is None:
            raise ValueError('Must specify duration or contiguous_duration')

        self.start()
        try:
            self.wait_for_duration(capture_duration)
        finally:
            data = self.stop()

        target = max(1, int(round(capture_duration * self.sampling_frequency)))
        n = min(len(data), target)
        return data[-n:] if n else data[:0]
