# Verifies: wiring REQ-SG-016/017 into engine actions — a GrabAction resolves
#   a save path then calls add_magnet; a SwapAction deletes the old infohash
#   then adds the new one; execution is all-attempted (one failure doesn't
#   skip later actions), and outcome.ok reflects whether ANY action failed
#   (the signal REQ-SG-027's persistence gate depends on).
# Scenario: a fake Downloader (records calls, can be told to fail on a given
#   infohash) driven through execute_actions with a canned path resolver.

from datetime import datetime, timezone

from showgrab.core.models import GrabAction, SwapAction, Variant
from showgrab.core.quality import Quality
from showgrab.service.executor import execute_actions

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


class FakeDownloader:
    def __init__(self, fail_infohashes: set[str] | None = None):
        self.added: list[tuple[str, str, str | None]] = []
        self.deleted: list[str] = []
        self._fail = fail_infohashes or set()

    def add_magnet(self, magnet: str, save_path: str, category: str | None = None) -> None:
        if magnet in self._fail:
            raise RuntimeError(f"boom adding {magnet}")
        self.added.append((magnet, save_path, category))

    def delete_with_files(self, infohash: str) -> None:
        if infohash in self._fail:
            raise RuntimeError(f"boom deleting {infohash}")
        self.deleted.append(infohash)


def variant(infohash: str, quality: Quality = Quality.HD720) -> Variant:
    return Variant(
        quality=quality, is_remux=False, is_repack=False,
        infohash=infohash, magnet=f"magnet:?xt=urn:btih:{infohash}", pub_date=NOW, episode_id=infohash,
    )


def test_grab_action_resolves_path_and_adds_magnet():
    dl = FakeDownloader()
    action = GrabAction(key=("1", 1, 1), show_name="Silo", variant=variant("abc"))
    outcome = execute_actions([action], dl, lambda show: f"/downloads/tv_series/{show}", category="showgrab")
    assert outcome.ok
    assert dl.added == [("magnet:?xt=urn:btih:abc", "/downloads/tv_series/Silo", "showgrab")]


def test_swap_action_deletes_old_then_adds_new():
    dl = FakeDownloader()
    action = SwapAction(key=("1", 1, 1), show_name="Silo", old_infohash="old123", new_variant=variant("new456"))
    outcome = execute_actions([action], dl, lambda show: "/downloads/tv_series/Silo")
    assert outcome.ok
    assert dl.deleted == ["old123"]
    assert dl.added == [("magnet:?xt=urn:btih:new456", "/downloads/tv_series/Silo", None)]


def test_one_failure_does_not_skip_remaining_actions():
    dl = FakeDownloader(fail_infohashes={"magnet:?xt=urn:btih:bad"})
    actions = [
        GrabAction(key=("1", 1, 1), show_name="Good Show", variant=variant("good")),
        GrabAction(key=("2", 1, 1), show_name="Bad Show", variant=variant("bad")),
        GrabAction(key=("3", 1, 1), show_name="Also Good", variant=variant("good2")),
    ]
    outcome = execute_actions(actions, dl, lambda show: "/downloads/tv_series")
    assert not outcome.ok
    assert len(outcome.failed) == 1
    assert len(outcome.succeeded) == 2
    # the two good actions still actually happened against the downloader
    assert [a[0] for a in dl.added] == [
        "magnet:?xt=urn:btih:good",
        "magnet:?xt=urn:btih:good2",
    ]


def test_all_success_gives_empty_failed_list_and_ok_true():
    dl = FakeDownloader()
    action = GrabAction(key=("1", 1, 1), show_name="Silo", variant=variant("abc"))
    outcome = execute_actions([action], dl, lambda show: "/downloads/tv_series/Silo")
    assert outcome.failed == []
    assert outcome.ok is True
