from __future__ import annotations

from pathlib import Path

import pytest

from codex_telegram_bot.config import Settings
from codex_telegram_bot.models import CodexLaunchMode, CodexResponse, CodexResultStatus
from codex_telegram_bot.services.status_line import (
    CodexLimitStatus,
    CodexLimitStatusProvider,
    StatusLineRenderer,
)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_bot_username": "codex_bot",
        "approved_directory": tmp_path,
        "codex_model": "gpt5",
        "codex_context_window": 100,
        "status_line_limits_timeout_seconds": 8,
        "status_line_template": (
            "Project {project} {cwd} {cwd_basename} | "
            "Model {model} {effort} {mode} | "
            "Session {session} {session_short} {status} {duration_ms} {duration_s} | "
            "Tokens {input_tokens}/{cached_input_tokens}/{output_tokens}/{total_tokens} | "
            "Context {context_used}/{context_remaining}/{context_limit} | "
            "Limits {limit_5h}/{limit_week}/{limit_updated_at}"
        ),
    }
    values.update(overrides)
    values.setdefault("_env_file", None)
    return Settings(**values)


def escaped(value: object) -> str:
    return StatusLineRenderer._markdown_escape(str(value))


def test_status_line_renderer_substitutes_basic_macros(tmp_path: Path) -> None:
    cwd = tmp_path / "app"
    cwd.mkdir()
    settings = make_settings(tmp_path, codex_reasoning_effort="high")
    renderer = StatusLineRenderer(settings)
    response = CodexResponse(
        final_text="done",
        thread_id="thread-abcdef",
        status=CodexResultStatus.SUCCESS,
        input_tokens=10,
        cached_input_tokens=4,
        output_tokens=5,
        duration_ms=1234,
    )
    limits = CodexLimitStatus(limit_5h="80%", limit_week="70%", updated_at="2026-04-22T00:00:00Z")

    line = renderer.render(
        cwd=cwd,
        response=response,
        launch_mode=CodexLaunchMode.FULL_ACCESS,
        limits=limits,
    )

    assert f"Project {tmp_path.name}/app {cwd} app" in line
    assert "Model gpt5 high full_access" in line
    assert "Session thread-abcdef thread-a success 1234 1.234" in line
    assert "Tokens 10/4/5/15" in line
    assert "Context 15/85/100" in line
    assert "Limits 80%/70%/2026-04-22T00:00:00Z" in line


def test_status_line_renderer_prefers_provider_context_over_response(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        status_line_template="{context_used}/{context_remaining}/{context_limit}",
    )
    renderer = StatusLineRenderer(settings)
    response = CodexResponse(final_text="done", thread_id="thread", input_tokens=10, output_tokens=5)

    line = renderer.render(
        cwd=tmp_path,
        response=response,
        limits=CodexLimitStatus(context_used=40, context_limit=120, total_tokens=40),
    )

    assert line == "40/80/120"


def test_status_line_renderer_uses_unknown_for_missing_values(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, codex_model=None, codex_reasoning_effort=None)
    renderer = StatusLineRenderer(settings)

    line = renderer.render(cwd=None)

    assert "Project unknown unknown unknown" in line
    assert "Model default unknown unknown" in line
    assert "Session unknown unknown unknown unknown unknown" in line
    assert "Tokens unknown/unknown/unknown/unknown" in line
    assert "Context unknown/unknown/100" in line
    assert "Limits unknown/unknown/unknown" in line


def test_status_line_renderer_leaves_unknown_macro_visible(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, status_line_template="ok {project} {typo_macro}")
    renderer = StatusLineRenderer(settings)
    cwd = tmp_path / "app"
    cwd.mkdir()

    assert renderer.render(cwd=cwd) == f"ok {tmp_path.name}/app {{typo_macro}}"


def test_status_line_renderer_respects_enabled_flag(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, status_line_enabled=False)
    renderer = StatusLineRenderer(settings)

    assert renderer.render(cwd=tmp_path) == ""


def test_status_line_renderer_can_hide_token_macros(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        status_line_template="{input_tokens}/{total_tokens}/{context_remaining}",
    )
    renderer = StatusLineRenderer(settings)
    response = CodexResponse(
        final_text="done",
        thread_id="thread",
        input_tokens=10,
        output_tokens=5,
    )

    line = renderer.render(
        cwd=tmp_path,
        response=response,
        include_token_summary=False,
    )

    assert line == "unknown/unknown/85"


def test_status_line_renderer_uses_context_from_limit_status(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        status_line_template="{input_tokens}/{total_tokens}/{context_remaining}/{context_limit}",
    )
    renderer = StatusLineRenderer(settings)

    line = renderer.render(
        cwd=tmp_path,
        limits=CodexLimitStatus(
            context_used=40,
            context_limit=120,
            input_tokens=30,
            output_tokens=10,
            total_tokens=40,
        ),
    )

    assert line == "30/40/80/120"


@pytest.mark.asyncio
async def test_limit_status_provider_cache_is_scoped_to_project_and_thread(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        status_line_limits_prompt="",
        status_line_limits_refresh_seconds=300,
    )
    provider = CodexLimitStatusProvider(settings)
    api_dir = tmp_path / "api"
    web_dir = tmp_path / "web"
    api_dir.mkdir()
    web_dir.mkdir()

    calls: list[tuple[str, str]] = []

    def fake_read_latest_local_status(*, cwd: Path | None, thread_id: str):
        resolved = str((cwd or settings.approved_directory).resolve())
        calls.append((resolved, thread_id))
        if resolved == str(api_dir.resolve()):
            return CodexLimitStatus(limit_5h="api", updated_at="2026-04-28T10:00:00+00:00")
        return CodexLimitStatus(limit_5h="web", updated_at="2026-04-28T10:05:00+00:00")

    provider._read_latest_local_status = fake_read_latest_local_status  # type: ignore[method-assign]

    api_first = await provider.get_status(cwd=api_dir, thread_id="thread-api")
    api_second = await provider.get_status(cwd=api_dir, thread_id="thread-api")
    web_first = await provider.get_status(cwd=web_dir, thread_id="thread-web")
    web_second = await provider.get_status(cwd=web_dir, thread_id="thread-web")

    assert api_first.limit_5h == "api"
    assert api_second.limit_5h == "api"
    assert web_first.limit_5h == "web"
    assert web_second.limit_5h == "web"
    assert calls == [
        (str(api_dir.resolve()), "thread-api"),
        (str(web_dir.resolve()), "thread-web"),
    ]
