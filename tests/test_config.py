from __future__ import annotations

from pathlib import Path

import pytest

from codex_telegram_bot.config import Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_bot_username": "codex_bot",
        "approved_directory": tmp_path,
        "allowed_users": "",
        "voice_provider": "openai",
        "openai_api_key": "",
        "voice_api_key": "",
        "voice_api_base_url": "",
        "voice_transcription_model": "",
    }
    values.update(overrides)
    values.setdefault("_env_file", None)
    return Settings(**values)


def test_settings_parse_allowed_users_and_defaults(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        allowed_users="1, 2,3",
        voice_provider="openai",
        _env_file=None,
    )

    assert settings.allowed_users == [1, 2, 3]
    assert settings.voice_provider == "openai"
    assert settings.resolved_voice_model == "whisper-1"
    assert settings.max_active_runs_per_user == 5


def test_settings_parse_allowed_users_json_string_directly(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        allowed_users="[1,2,3]",
        voice_provider="openai",
    )

    assert settings.allowed_users == [1, 2, 3]


def test_settings_parse_allowed_users_from_env_file_json_list(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=token",
                "TELEGRAM_BOT_USERNAME=codex_bot",
                f"APPROVED_DIRECTORY={tmp_path}",
                "ALLOWED_USERS=[1,2,3]",
                "VOICE_PROVIDER=openai",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.allowed_users == [1, 2, 3]


def test_settings_parse_scalar_allowed_users_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=token",
                "TELEGRAM_BOT_USERNAME=codex_bot",
                f"APPROVED_DIRECTORY={tmp_path}",
                "ALLOWED_USERS=1",
                "VOICE_PROVIDER=openai",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.allowed_users == [1]


def test_settings_parse_csv_allowed_users_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=token",
                "TELEGRAM_BOT_USERNAME=codex_bot",
                f"APPROVED_DIRECTORY={tmp_path}",
                "ALLOWED_USERS=1, 2,3",
                "VOICE_PROVIDER=openai",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.allowed_users == [1, 2, 3]


def test_settings_validate_log_level(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, log_level="debug")
    assert settings.log_level == "DEBUG"

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        make_settings(tmp_path, log_level="TRACE")


def test_settings_validate_timeout_and_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CODEX_TIMEOUT_SECONDS"):
        make_settings(tmp_path, codex_timeout_seconds=0)

    with pytest.raises(ValueError, match="CODEX_CONTEXT_WINDOW"):
        make_settings(tmp_path, codex_context_window=0)

    with pytest.raises(ValueError, match="STATUS_LINE_LIMITS_REFRESH_SECONDS"):
        make_settings(tmp_path, status_line_limits_refresh_seconds=-1)

    with pytest.raises(ValueError, match="MAX_ACTIVE_RUNS_PER_USER"):
        make_settings(tmp_path, max_active_runs_per_user=0)


def test_settings_parse_status_line_env_fields(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=token",
                "TELEGRAM_BOT_USERNAME=codex_bot",
                f"APPROVED_DIRECTORY={tmp_path}",
                "ALLOWED_USERS=1",
                "VOICE_PROVIDER=openai",
                "STATUS_LINE_ENABLED=false",
                "STATUS_LINE_TEMPLATE='Project {project}'",
                "STATUS_LINE_LIMITS_REFRESH_SECONDS=60",
                "STATUS_LINE_LIMITS_PROMPT='Return {\"limit_5h\":\"unknown\"}'",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.status_line_enabled is False
    assert settings.status_line_template == "Project {project}"
    assert settings.status_line_limits_refresh_seconds == 60
    assert settings.status_line_limits_prompt == 'Return {"limit_5h":"unknown"}'


def test_settings_validate_approved_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="APPROVED_DIRECTORY"):
        make_settings(tmp_path, approved_directory=missing)


def test_settings_parse_additional_project_directories_and_filters(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()

    settings = make_settings(
        tmp_path,
        additional_project_directories=f"{extra}",
        project_visible_names="api, web",
        project_ignore_names='["secret"]',
    )

    assert settings.additional_project_directories == [extra.resolve()]
    assert settings.project_visible_names == ["api", "web"]
    assert settings.project_ignore_names == ["secret"]


def test_settings_validate_additional_project_directories(tmp_path: Path) -> None:
    missing = tmp_path / "missing-extra"
    with pytest.raises(ValueError, match="ADDITIONAL_PROJECT_DIRECTORIES"):
        make_settings(tmp_path, additional_project_directories=[missing])


def test_settings_validate_openai_compatible_voice_provider(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        voice_provider="openai_compatible",
        voice_api_key="key",
        voice_api_base_url="https://api.groq.com/openai/v1/",
        voice_transcription_model="whisper-large-v3-turbo",
    )

    assert settings.voice_provider == "openai_compatible"
    assert settings.voice_api_base_url == "https://api.groq.com/openai/v1"
    assert settings.resolved_voice_model == "whisper-large-v3-turbo"
    assert settings.runtime_capabilities.voice is True
    assert settings.runtime_capabilities.voice_provider == "openai_compatible"
    assert settings.runtime_capabilities.voice_model == "whisper-large-v3-turbo"


def test_settings_runtime_capabilities_for_disabled_voice_and_live_limits(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        enable_voice_messages=False,
        enable_file_uploads=False,
        codex_enable_images=True,
        status_line_limits_prompt="/status",
    )

    assert settings.runtime_capabilities.text is True
    assert settings.runtime_capabilities.files is False
    assert settings.runtime_capabilities.voice is False
    assert settings.runtime_capabilities.images is True
    assert settings.runtime_capabilities.live_status_limits is True


def test_settings_require_openai_compatible_voice_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VOICE_API_KEY"):
        make_settings(
            tmp_path,
            voice_provider="openai_compatible",
            voice_api_base_url="https://api.groq.com/openai/v1",
            voice_transcription_model="whisper-large-v3-turbo",
        )

    with pytest.raises(ValueError, match="VOICE_API_BASE_URL"):
        make_settings(
            tmp_path,
            voice_provider="openai_compatible",
            voice_api_key="key",
            voice_transcription_model="whisper-large-v3-turbo",
        )

    with pytest.raises(ValueError, match="VOICE_TRANSCRIPTION_MODEL"):
        make_settings(
            tmp_path,
            voice_provider="openai_compatible",
            voice_api_key="key",
            voice_api_base_url="https://api.groq.com/openai/v1",
        )


def test_settings_reject_removed_voice_providers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VOICE_PROVIDER"):
        make_settings(tmp_path, voice_provider="mistral")

    with pytest.raises(ValueError, match="VOICE_PROVIDER"):
        make_settings(tmp_path, voice_provider="local")


def test_settings_validate_default_launch_mode(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, codex_default_launch_mode="full_access")

    assert str(settings.codex_default_launch_mode) == "full_access"

    with pytest.raises(ValueError, match="CODEX_DEFAULT_LAUNCH_MODE"):
        make_settings(tmp_path, codex_default_launch_mode="unsafe")
