# Verifies: REQ-SG-024 (settings persist in SQLite; an empty table seeds
#   once from environment variables; subsequent loads read the DB and never
#   re-seed over a saved edit).
# Scenario: a fresh file-backed SQLite DB, load (triggers env seed), mutate
#   via save(), load again and confirm the edit stuck rather than reverting.

import os

from showgrab.store.db import init_db, make_engine, make_session_factory
from showgrab.store.settings_store import SettingsStore


def make_store(tmp_path):
    engine = make_engine(":memory:")
    init_db(engine)
    return SettingsStore(make_session_factory(engine))


def test_seeds_from_env_on_first_load(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWGRAB_FEED_URL", "https://showrss.info/user/1.rss")
    monkeypatch.setenv("SHOWGRAB_PREFERRED_QUALITY", "1080p")
    monkeypatch.setenv("SHOWGRAB_WAIT_HOURS", "3")
    monkeypatch.setenv("SHOWGRAB_DRY_RUN", "false")
    monkeypatch.setenv("SHOWGRAB_PATH_MAPPINGS", '[["/media1", "/downloads/tv_series"]]')
    monkeypatch.setenv("SHOWGRAB_SMTP_TO", "a@example.com, b@example.com")

    store = make_store(tmp_path)
    settings = store.load()

    assert settings.feed_url == "https://showrss.info/user/1.rss"
    assert settings.preferred_quality == "1080p"
    assert settings.wait_hours == 3.0
    assert settings.dry_run is False
    assert settings.path_mappings == [("/media1", "/downloads/tv_series")]
    assert settings.smtp_to == ["a@example.com", "b@example.com"]


def test_defaults_when_env_unset(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SHOWGRAB_"):
            monkeypatch.delenv(key, raising=False)

    store = make_store(tmp_path)
    settings = store.load()

    assert settings.dry_run is True  # safe default: never grab silently
    assert settings.preferred_quality == "720p"
    assert settings.poll_interval_minutes == 120.0
    assert settings.qbittorrent_category == "showgrab"
    assert settings.path_mappings == []


def test_save_persists_and_load_does_not_reseed_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWGRAB_PREFERRED_QUALITY", "720p")
    store = make_store(tmp_path)
    settings = store.load()
    assert settings.preferred_quality == "720p"

    settings.preferred_quality = "1080p"
    settings.wait_hours = 12.0
    store.save(settings)

    # Even though env still says 720p, the saved edit must win on reload.
    reloaded = store.load()
    assert reloaded.preferred_quality == "1080p"
    assert reloaded.wait_hours == 12.0


def test_engine_config_converts_quality_label():
    from showgrab.core.quality import Quality
    from showgrab.store.settings_store import Settings

    settings = Settings(
        feed_url="", poll_interval_minutes=30, dry_run=True,
        preferred_quality="1080p", wait_hours=6, swap_window_days=7, old_cutoff_days=180,
        qbittorrent_url="", qbittorrent_username="", qbittorrent_password="", qbittorrent_category=None,
        jellyfin_url="", jellyfin_api_key="",
        smtp_host="", smtp_port=587, smtp_username="", smtp_password="", smtp_from="", smtp_to=[],
        smtp_use_tls=True, smtp_use_ssl=False,
        tv_root="/downloads/tv_series", path_mappings=[],
    )
    config = settings.engine_config()
    assert config.preferred_quality is Quality.HD1080
    assert config.wait_hours == 6
