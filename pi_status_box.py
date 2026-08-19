#!/usr/bin/env python3
"""
VisionSortAI Pi Status Box
===========================
Lives in the analyze-and-backup project root, alongside cli.py, and reads
its Azure/mount-path configuration directly from analyze_and_backup.config
-- run it with the project's own .venv so that import resolves.

Drives one status peripheral, wired per pi_status_box_wiring_final.md:

  10-segment LED bar graph -- dual purpose:
       - Idle: fills up as the Pi becomes "ready" (power -> internet ->
         Azure Blob/Cosmos), all on when fully ready.
       - While cli.py is actively processing a card (see JOB_FLAG_PATH
         below): readiness display pauses and the bar graph instead runs
         a back-and-forth "chase" animation, then flashes solid on
         (success) or blinks (failure) briefly when the job finishes,
         before returning to showing readiness.

The 8x8 LED matrix + 2x74HC595 driver that used to provide the loading
animation was dropped earlier in this project (too fiddly to get wired
reliably) -- the bar graph covers that role instead. The physical
safe-shutdown push button has ALSO been dropped (couldn't get a reliable
switch reading off two different physical buttons across every leg
combination -- likely a bad breadboard section, not worth the remaining
time to chase further). To shut the Pi down safely now, just SSH in and
run `sudo shutdown -h now` as usual. So the whole status box only needs
the 10 bar-graph pins, nothing else.

STILL OUTSTANDING:
  - Nothing -- MH-SD card module, bar graph readiness display, and the
    job-in-progress chase/flash animation are all wired and confirmed
    working on hardware.

Requires: gpiozero (preinstalled on Raspberry Pi OS), plus whatever's
already in requirements.txt (dotenv, azure-storage-blob, azure-cosmos).
Run with: python3 pi_status_box.py   (from the project root, venv active)
"""

import socket
import threading
import time
from pathlib import Path

from gpiozero import LEDBarGraph

from analyze_and_backup.config import CONFIG

# ---------------------------------------------------------------------------
# Configuration -- matches pi_status_box_wiring_final.md
# ---------------------------------------------------------------------------

BAR_GRAPH_PINS = [5, 6, 12, 13, 16, 19, 20, 21, 26, 18]  # BCM, 10 LEDs

# Coordination file cli.py touches while actively processing a card, so
# this daemon knows when to show the "busy" animation on the bar graph.
# Lives next to CONFIG.stop_file_path, following the same "flag file"
# pattern the project already uses for the web interface's Stop button.
JOB_FLAG_PATH = Path(CONFIG.local_backup_dir) / "PI_STATUS_JOB_RUNNING"

INTERNET_CHECK_HOST = ("8.8.8.8", 53)
CHECK_INTERVAL_SECONDS = 5
JOB_POLL_INTERVAL_SECONDS = 0.5

# ---------------------------------------------------------------------------
# Readiness checks -> bar graph
# ---------------------------------------------------------------------------


def check_power():
    # If this code is running, the Pi is powered. Kept as a named check so
    # the bar graph fill logic below stays symmetric / easy to extend.
    return True


def check_internet(timeout=2):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(INTERNET_CHECK_HOST)
        return True
    except OSError:
        return False


def check_azure(timeout=3):
    """Actually exercises the Azure services this project uses, rather than
    pinging generic Azure infrastructure -- Blob Storage (required for the
    real backup path) and, if configured, Cosmos DB (optional -- storage.py
    already falls back to a local JSON-lines log without it, so a missing
    Cosmos config isn't treated as "not ready" here, only a missing/broken
    Blob Storage connection is)."""
    if not CONFIG.azure_storage_connection_string:
        return False  # not configured at all -- matches cli.py's own check

    try:
        from azure.storage.blob import BlobServiceClient

        client = BlobServiceClient.from_connection_string(
            CONFIG.azure_storage_connection_string,
            connection_timeout=timeout,
            read_timeout=timeout,
        )
        client.get_account_information()  # cheapest real round-trip call
    except Exception as e:
        print(f"[azure] Blob Storage check failed: {e}")
        return False

    if CONFIG.azure_cosmos_endpoint and CONFIG.azure_cosmos_key:
        try:
            from azure.cosmos import CosmosClient

            cosmos = CosmosClient(
                CONFIG.azure_cosmos_endpoint,
                CONFIG.azure_cosmos_key,
                connection_timeout=timeout,
            )
            cosmos.get_database_client(CONFIG.azure_cosmos_database).read()
        except Exception as e:
            # Blob is up but Cosmos isn't -- storage.py itself would fall
            # back to the local JSONL index in this situation and keep
            # working, so this isn't fatal to readiness, just logged.
            print(f"[azure] Cosmos DB check failed (non-fatal, has local fallback): {e}")

    return True


class ReadinessMonitor:
    """Polls power/internet/Azure and drives the bar graph accordingly.

    Fill pattern: power alone lights ~3 segments, +internet lights ~7,
    +Azure lights all 10. Adjust the weighting below to taste.

    Can be paused (see pause()/resume()) so BarGraphPulser can take over
    the same bar graph for the duration of a job without the two fighting
    over the pins -- while paused, this just idles instead of touching
    bar_graph.value.
    """

    def __init__(self, bar_graph):
        self.bar_graph = bar_graph
        self._running = False
        self._thread = None
        self._paused = threading.Event()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def _loop(self):
        while self._running:
            if self._paused.is_set():
                time.sleep(0.1)
                continue

            power_ok = check_power()
            internet_ok = check_internet()
            azure_ok = check_azure() if internet_ok else False

            if azure_ok:
                fraction = 1.0
            elif internet_ok:
                fraction = 0.7
            elif power_ok:
                fraction = 0.3
            else:
                fraction = 0.0

            self.bar_graph.value = fraction
            time.sleep(CHECK_INTERVAL_SECONDS)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)


# ---------------------------------------------------------------------------
# Job-in-progress indicator -- bar graph "chase" animation, replaces the
# old 8x8 matrix spinner. Takes over the bar graph from ReadinessMonitor
# for the duration of a job, then hands it back.
# ---------------------------------------------------------------------------


class BarGraphPulser:
    """Bounces a single lit segment back and forth along the bar graph
    while a card is being processed (a "Cylon scan"), then flashes solid
    on (success) or blinks a few times (failure) briefly when done, and
    resumes ReadinessMonitor's normal display.
    """

    def __init__(self, bar_graph, readiness_monitor):
        self.bar_graph = bar_graph
        self.readiness_monitor = readiness_monitor
        self._stop_event = None
        self._thread = None

    def _chase_loop(self, stop_event, delay=0.08):
        n = len(self.bar_graph)
        # 0,1,2,...,n-1,n-2,...,1 -- bounces back and forth without
        # repeating the two end segments twice in a row.
        sequence = list(range(n)) + list(range(n - 2, 0, -1))
        while not stop_event.is_set():
            for i in sequence:
                if stop_event.is_set():
                    break
                self.bar_graph.off()
                self.bar_graph[i].on()
                time.sleep(delay)

    def _stop_chase(self):
        if self._stop_event:
            self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._stop_event = None
        self._thread = None

    def start_job(self):
        self.readiness_monitor.pause()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._chase_loop, args=(self._stop_event,), daemon=True
        )
        self._thread.start()

    def finish_job(self, success=True):
        self._stop_chase()
        if success:
            self.bar_graph.on()
            time.sleep(1.0)
        else:
            for _ in range(4):
                self.bar_graph.on()
                time.sleep(0.15)
                self.bar_graph.off()
                time.sleep(0.15)
        self.bar_graph.off()
        self.readiness_monitor.resume()

class JobWatcher:
    """Polls JOB_FLAG_PATH and drives `job` automatically -- this is what
    makes the bar graph switch into its "busy" animation on its own
    whenever the systemd-triggered cli.py run is actually processing a
    card, with no manual start_job()/finish_job() calls needed from
    anywhere. See cli.py's touch/unlink of JOB_FLAG_PATH around
    process_card()."""

    def __init__(self, job: BarGraphPulser):
        self.job = job
        self._running = False
        self._thread = None

    def _loop(self):
        was_running = False
        while self._running:
            job_running = JOB_FLAG_PATH.exists()
            if job_running and not was_running:
                self.job.start_job()
            elif was_running and not job_running:
                self.job.finish_job(success=True)
            was_running = job_running
            time.sleep(JOB_POLL_INTERVAL_SECONDS)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # active_high=False: this bar graph is wired common-anode (all 10
    # anodes tied to +3.3V through individual resistors, GPIOs on the
    # cathodes) -- see pi_status_box_wiring_final.md. LEDs light when the
    # GPIO is driven LOW, so gpiozero needs to know "on" means LOW here.
    bar_graph = LEDBarGraph(*BAR_GRAPH_PINS, pwm=False, active_high=False)

    readiness = ReadinessMonitor(bar_graph)
    readiness.start()

    job = BarGraphPulser(bar_graph, readiness)
    job_watcher = JobWatcher(job)
    job_watcher.start()

    print("Status box running.")
    print(f"  - watching {JOB_FLAG_PATH} for cli.py job activity")
    print("  - (no physical button in this build -- SSH in and run 'sudo shutdown -h now' to power off safely)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        readiness.stop()
        job_watcher.stop()
        bar_graph.off()


if __name__ == "__main__":
    main()
