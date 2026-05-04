"""Tests for typed pipeline config validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline import run
from pipeline.config import AppConfig, validate_config_dict


def _valid_config() -> dict:
    return {
        "pipeline": {
            "summarizer": {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "prompt_version": "v2",
            },
            "summary_min_chars": 700,
            "summary_max_chars": 1200,
        },
        "channels": [
            {
                "id": "UCsT0YIqwnpJCM-mx7-gSA4Q",
                "name": "슈카월드",
                "slug": "shuka",
            }
        ],
        "blogs": [],
    }


def test_validate_config_dict_accepts_minimal_valid_config():
    config = validate_config_dict(_valid_config())

    assert isinstance(config, AppConfig)
    assert config.pipeline.summarizer.output_format == "json"
    assert config.pipeline.summarizer.max_output_tokens == 1600
    assert config.pipeline.summary_headline_max_chars == 24
    assert config.pipeline.min_duration_seconds == 1200
    assert config.pipeline.max_discovery_concurrency == 4
    assert config.pipeline.max_processing_concurrency == 2


def test_validate_config_dict_rejects_invalid_output_format():
    raw = _valid_config()
    raw["pipeline"]["summarizer"]["output_format"] = "xml"

    with pytest.raises(ValueError, match="pipeline.summarizer.output_format"):
        validate_config_dict(raw)


def test_validate_config_dict_rejects_invalid_summary_bounds():
    raw = _valid_config()
    raw["pipeline"]["summary_min_chars"] = 1200
    raw["pipeline"]["summary_max_chars"] = 700

    with pytest.raises(ValueError, match="summary_max_chars"):
        validate_config_dict(raw)


def test_validate_config_dict_rejects_invalid_concurrency():
    raw = _valid_config()
    raw["pipeline"]["max_processing_concurrency"] = 0

    with pytest.raises(ValueError, match="pipeline.max_processing_concurrency"):
        validate_config_dict(raw)


def test_load_config_runs_typed_validation(tmp_path: Path):
    raw = _valid_config()
    raw["pipeline"]["summarizer"]["transient_retries"] = 0
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="pipeline.summarizer.transient_retries"):
        run.load_config(path)


def test_load_config_rejects_non_mapping_root(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="config root must be a mapping"):
        run.load_config(path)
