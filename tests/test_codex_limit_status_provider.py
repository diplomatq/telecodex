from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from codex_telegram_bot.config import Settings
from codex_telegram_bot.services.status_line import CodexLimitStatusProvider


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_bot_username": "codex_bot",
        "approved_directory": tmp_path,
        "codex_cli_path": "codex",
        "status_line_limits_timeout_seconds": 8,
        "status_line_limits_prompt": "Return limits as JSON",
    }
    values.update(overrides)
    values.setdefault("_env_file", None)
    return Settings(**values)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        hang: bool = False,
    ):
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")
        self.pid = 4321
        self.returncode: int | None = None if hang else returncode
        self._returncode = returncode
        self._done = asyncio.Event()
        self.killed = False
        self.hang = hang
        if not hang:
            self._done.set()

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.hang:
            await self._done.wait()
        return self._stdout, self._stderr

    async def wait(self) -> int:
        await self._done.wait()
        return int(self.returncode or 0)

    def kill(self) -> None:
        self.killed = True
        self.returncode = self._returncode
        self._done.set()


@pytest.mark.asyncio
async def test_limit_provider_parses_json_from_codex_exec_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(
        stdout=(
            '{"type":"item.completed","item":{"type":"assistant_message",'
            '"text":"{\\"limit_5h\\":\\"2h left\\",\\"limit_week\\":\\"80% left\\"}"}}\n'
        )
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    provider = CodexLimitStatusProvider(make_settings(tmp_path))

    status = await provider.get_status(cwd=tmp_path)

    assert status.limit_5h == "2h left"
    assert status.limit_week == "80% left"
    assert status.updated_at != "unknown"
    assert status.last_error == ""


@pytest.mark.asyncio
async def test_limit_provider_parses_token_count_event_from_codex_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(
        stdout=(
            '{"type":"event_msg","payload":{"type":"token_count",'
            '"info":{"last_token_usage":{"input_tokens":10,"cached_input_tokens":4,'
            '"output_tokens":5,"total_tokens":15},"model_context_window":100},'
            '"rate_limits":{"primary":{"used_percent":2,"window_minutes":300,'
            '"resets_at":1776947986},"secondary":{"used_percent":29,'
            '"window_minutes":10080,"resets_at":1777405707},"plan_type":"plus"}}}\n'
        )
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    provider = CodexLimitStatusProvider(make_settings(tmp_path, status_line_limits_prompt="/status"))

    status = await provider.get_status(cwd=tmp_path, thread_id="thread")

    assert status.limit_5h.startswith("2% used")
    assert status.limit_week.startswith("29% used")
    assert status.context_used == 15
    assert status.context_limit == 100
    assert status.input_tokens == 10
    assert status.cached_input_tokens == 4
    assert status.output_tokens == 5
    assert status.total_tokens == 15


@pytest.mark.asyncio
async def test_limit_provider_uses_cache_before_refresh_interval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeProcess(stdout='{"limit_5h":"fresh","limit_week":"fresh-week"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    provider = CodexLimitStatusProvider(make_settings(tmp_path, status_line_limits_refresh_seconds=300))

    first = await provider.get_status(cwd=tmp_path)
    second = await provider.get_status(cwd=tmp_path)

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("process", "expected_error"),
    [
        (FakeProcess(stdout="not json"), "invalid_json"),
        (FakeProcess(stderr="boom", returncode=2), "codex_cli_error:2:boom"),
        (FakeProcess(hang=True), "timeout"),
    ],
)
async def test_limit_provider_returns_unknown_on_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process: FakeProcess,
    expected_error: str,
) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    provider = CodexLimitStatusProvider(make_settings(tmp_path), timeout_seconds=0.001)

    status = await provider.get_status(cwd=tmp_path)

    assert status.limit_5h == "unknown"
    assert status.limit_week == "unknown"
    assert status.updated_at == "unknown"
    assert status.last_error.startswith(expected_error)


@pytest.mark.asyncio
async def test_limit_provider_starts_codex_in_separate_process_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess(stdout='{"limit_5h":"fresh","limit_week":"fresh-week"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    provider = CodexLimitStatusProvider(make_settings(tmp_path))

    await provider.get_status(cwd=tmp_path)

    if os.name == "nt":
        assert "start_new_session" not in captured
    else:
        assert captured["start_new_session"] is True


@pytest.mark.asyncio
async def test_limit_provider_kills_process_group_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(hang=True)
    killed: list[tuple[int, int]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    def fake_killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        process.kill()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("codex_telegram_bot.processes.os.killpg", fake_killpg)
    provider = CodexLimitStatusProvider(make_settings(tmp_path), timeout_seconds=0.001)

    status = await provider.get_status(cwd=tmp_path)

    assert status.last_error.startswith("timeout")
    if os.name != "nt":
        assert killed == [(process.pid, 9)]
