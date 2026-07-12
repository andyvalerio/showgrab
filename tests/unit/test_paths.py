# Verifies: REQ-SG-028 (save-path resolution maps a media-server path through
#   configured prefixes, longest-prefix-wins; falls back to a sanitized new
#   folder — following this library's existing dot-separated naming
#   convention — under a configured TV root when no mapping applies; and
#   looks_like_release_folder detects a mis-registered single-episode
#   folder by name alone, per the live finding of 2026-07-12).
# Scenario: exercise map_series_path, new_series_save_path,
#   sanitize_folder_name, and looks_like_release_folder directly (pure
#   functions, no I/O).

from showgrab.core.paths import (
    looks_like_release_folder,
    map_series_path,
    new_series_save_path,
    sanitize_folder_name,
)


def test_map_series_path_translates_matching_prefix():
    mappings = [("/media1", "/downloads/tv_series")]
    result = map_series_path("/media1/Silo", mappings)
    assert result == "/downloads/tv_series/Silo"


def test_map_series_path_returns_none_when_no_mapping_matches():
    mappings = [("/media1", "/downloads/tv_series")]
    assert map_series_path("/media2/Movie", mappings) is None


def test_map_series_path_picks_longest_matching_prefix():
    mappings = [
        ("/media1", "/downloads/generic"),
        ("/media1/anime", "/downloads/anime"),
    ]
    result = map_series_path("/media1/anime/Show", mappings)
    assert result == "/downloads/anime/Show"


def test_new_series_save_path_joins_root_and_sanitized_name():
    assert new_series_save_path("Silo", "/downloads/tv_series") == "/downloads/tv_series/Silo"


def test_new_series_save_path_strips_trailing_slash_from_root():
    assert new_series_save_path("Silo", "/downloads/tv_series/") == "/downloads/tv_series/Silo"


def test_sanitize_folder_name_matches_library_convention():
    # Observed live 2026-07-12: the library's genuine series folders are
    # named exactly this way.
    assert sanitize_folder_name("American Dad!") == "American.Dad"
    assert sanitize_folder_name("Doctor Who") == "Doctor.Who"
    assert sanitize_folder_name("Silo") == "Silo"


def test_sanitize_folder_name_strips_punctuation_and_joins_with_dots():
    assert sanitize_folder_name('Doctor Who: The Movie? (2005)') == "Doctor.Who.The.Movie.2005"


def test_sanitize_folder_name_collapses_whitespace_and_strips_trailing_dots():
    assert sanitize_folder_name("  Avatar   The Last Airbender ...  ") == "Avatar.The.Last.Airbender"


def test_sanitize_folder_name_empty_input_falls_back():
    assert sanitize_folder_name("???") == "Unknown"


def test_looks_like_release_folder_detects_episode_code_and_resolution():
    assert looks_like_release_folder(
        "www.Torrenting.com - American Dad! S21E10 Idiot Rich 1080p DSNP WEB-DL DDP5 1 H 264-NTb"
    )
    assert looks_like_release_folder("Doctor Who 2023 S00E02 Wild Blue Yonder 1080p DSNP WEB-DL")


def test_looks_like_release_folder_false_for_clean_series_names():
    assert not looks_like_release_folder("American.Dad")
    assert not looks_like_release_folder("Silo")
    assert not looks_like_release_folder("Doctor.Who")
