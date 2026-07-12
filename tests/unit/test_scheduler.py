# Verifies: REQ-SG-031 (poll cadence is reschedulable at runtime without a
#   restart), REQ-SG-030 (shutdown stops the scheduler cleanly).
# Scenario: start a PollScheduler against a fake poll_fn that signals a
#   threading.Event, confirm it fires promptly on start, reschedule and
#   trigger-now both work, and stop() leaves it not running.

import threading

from showgrab.service.scheduler import PollScheduler


def test_start_runs_poll_fn_promptly():
    fired = threading.Event()
    scheduler = PollScheduler(lambda: fired.set())
    scheduler.start(interval_minutes=60)  # long interval; relies on the immediate first run
    try:
        assert fired.wait(timeout=2), "poll_fn did not fire on start"
        assert scheduler.running is True
    finally:
        scheduler.stop()


def test_stop_leaves_scheduler_not_running():
    scheduler = PollScheduler(lambda: None)
    scheduler.start(interval_minutes=60)
    scheduler.stop()
    assert scheduler.running is False


def test_trigger_now_fires_an_extra_poll():
    calls = []
    event = threading.Event()

    def poll_fn():
        calls.append(1)
        event.set()

    scheduler = PollScheduler(poll_fn)
    scheduler.start(interval_minutes=60)
    try:
        assert event.wait(timeout=2)  # the immediate first run
        event.clear()
        scheduler.trigger_now()
        assert event.wait(timeout=2)
        assert len(calls) >= 2
    finally:
        scheduler.stop()


def test_reschedule_does_not_raise_and_keeps_running():
    scheduler = PollScheduler(lambda: None)
    scheduler.start(interval_minutes=60)
    try:
        scheduler.reschedule(interval_minutes=15)
        assert scheduler.running is True
    finally:
        scheduler.stop()
