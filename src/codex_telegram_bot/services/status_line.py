from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import CodexLaunchMode, CodexResponse
from ..project_labels import render_project_display_name
from ..processes import subprocess_group_kwargs, terminate_process_tree


UNKNOWN = "unknown"
MACRO_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|>])")


@dataclass(frozen=True)
class CodexLimitStatus:
    limit_5h: str = UNKNOWN
    limit_week: str = UNKNOWN
    updated_at: str = UNKNOWN
    last_error: str = ""
    context_used: Optional[int] = None
    context_limit: Optional[int] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class StatusLineRenderer:
    LIMIT_MACROS = {
        "limit_5h",
        "limit_week",
        "limit_updated_at",
        "context_used",
        "context_remaining",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "total_tokens",
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    @classmethod
    def needs_limit_status(cls, template: str) -> bool:
        return bool(cls.LIMIT_MACROS.intersection(MACRO_RE.findall(template or "")))

    def render(
        self,
        *,
        cwd: Optional[Path],
        response: Optional[CodexResponse] = None,
        thread_id: str = "",
        launch_mode: Optional[CodexLaunchMode | str] = None,
        limits: Optional[CodexLimitStatus] = None,
        include_token_summary: bool = True,
    ) -> str:
        if not self.settings.status_line_enabled:
            return ""

        template = self.settings.status_line_template.strip()
        if not template:
            return ""

        values = self._build_values(
            cwd=cwd,
            response=response,
            thread_id=thread_id,
            launch_mode=launch_mode,
            limits=limits,
            include_token_summary=include_token_summary,
        )

        def replace_macro(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                return match.group(0)
            return self._markdown_escape(values[name])

        return MACRO_RE.sub(replace_macro, template)

    def _build_values(
        self,
        *,
        cwd: Optional[Path],
        response: Optional[CodexResponse],
        thread_id: str,
        launch_mode: Optional[CodexLaunchMode | str],
        limits: Optional[CodexLimitStatus],
        include_token_summary: bool,
    ) -> dict[str, str]:
        session = ""
        if response is not None and response.thread_id:
            session = response.thread_id
        elif thread_id:
            session = thread_id

        context_limit = int(self.settings.codex_context_window)
        if limits is not None and limits.context_limit is not None:
            context_limit = int(limits.context_limit)

        if limits is not None and limits.context_used is not None:
            input_tokens = int(limits.input_tokens or 0)
            cached_input_tokens = int(limits.cached_input_tokens or 0)
            output_tokens = int(limits.output_tokens or 0)
            total_tokens = int(limits.total_tokens or limits.context_used)
            context_used = int(limits.context_used)
        elif response is not None:
            input_tokens = response.input_tokens
            cached_input_tokens = response.cached_input_tokens
            output_tokens = response.output_tokens
            total_tokens = input_tokens + output_tokens
            context_used = total_tokens
        else:
            input_tokens = 0
            cached_input_tokens = 0
            output_tokens = 0
            total_tokens = 0
            context_used = 0

        has_token_usage = response is not None and (
            response.input_tokens != 0
            or response.cached_input_tokens != 0
            or response.output_tokens != 0
        )
        has_context_usage = has_token_usage or (
            limits is not None and limits.context_used is not None
        )

        values = {
            "project": render_project_display_name(cwd) if cwd is not None else UNKNOWN,
            "cwd": str(cwd) if cwd is not None else UNKNOWN,
            "cwd_basename": cwd.name if cwd is not None else UNKNOWN,
            "model": self.settings.codex_model or "default",
            "effort": self.settings.codex_reasoning_effort or UNKNOWN,
            "mode": self._mode_value(launch_mode),
            "session": session or UNKNOWN,
            "session_short": session[:8] if session else UNKNOWN,
            "status": str(response.status) if response is not None else UNKNOWN,
            "duration_ms": str(response.duration_ms) if response is not None else UNKNOWN,
            "duration_s": self._duration_seconds(response.duration_ms)
            if response is not None
            else UNKNOWN,
            "context_limit": str(context_limit),
            "context_used": str(context_used) if has_context_usage else UNKNOWN,
            "context_remaining": str(max(context_limit - context_used, 0))
            if has_context_usage
            else UNKNOWN,
            "limit_5h": limits.limit_5h if limits is not None else UNKNOWN,
            "limit_week": limits.limit_week if limits is not None else UNKNOWN,
            "limit_updated_at": limits.updated_at if limits is not None else UNKNOWN,
        }

        for name, value in {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }.items():
            values[name] = str(value) if include_token_summary and has_context_usage else UNKNOWN
        return values

    @staticmethod
    def _mode_value(launch_mode: Optional[CodexLaunchMode | str]) -> str:
        if launch_mode is None:
            return UNKNOWN
        if isinstance(launch_mode, CodexLaunchMode):
            return launch_mode.value
        value = str(launch_mode).strip()
        return value or UNKNOWN

    @staticmethod
    def _duration_seconds(duration_ms: int) -> str:
        value = f"{duration_ms / 1000:.3f}".rstrip("0").rstrip(".")
        return value or "0"

    @staticmethod
    def _markdown_escape(value: str) -> str:
        return str(value)


class CodexLimitStatusProvider:
    def __init__(
        self,
        settings: Settings,
        logger: Any = None,
        *,
        timeout_seconds: Optional[float] = None,
    ):
        self.settings = settings
        self.logger = logger
        self.timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(self.settings.status_line_limits_timeout_seconds)
        )
        self._cache: Optional[CodexLimitStatus] = None
        self._cache_key: Optional[tuple[str, str]] = None
        self._cache_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def get_status(
        self,
        *,
        cwd: Optional[Path] = None,
        thread_id: str = "",
    ) -> CodexLimitStatus:
        cache_key = self._cache_identity(cwd=cwd, thread_id=thread_id)
        now = time.monotonic()
        if self._is_cache_fresh(now, cache_key):
            assert self._cache is not None
            return self._cache

        async with self._lock:
            now = time.monotonic()
            if self._is_cache_fresh(now, cache_key):
                assert self._cache is not None
                return self._cache

            if not self.settings.status_line_limits_prompt.strip():
                self._cache = self._read_latest_local_status(
                    cwd=cwd,
                    thread_id=thread_id,
                ) or CodexLimitStatus()
                self._cache_key = cache_key
                self._cache_monotonic = now
                return self._cache

            self._cache = await self._refresh(cwd=cwd, thread_id=thread_id)
            self._cache_key = cache_key
            self._cache_monotonic = time.monotonic()
            return self._cache

    def _is_cache_fresh(self, now: float, cache_key: tuple[str, str]) -> bool:
        if self._cache is None or self._cache_key != cache_key:
            return False
        refresh_seconds = self.settings.status_line_limits_refresh_seconds
        return refresh_seconds > 0 and now - self._cache_monotonic < refresh_seconds

    def _cache_identity(self, *, cwd: Optional[Path], thread_id: str) -> tuple[str, str]:
        resolved_cwd = str((cwd or self.settings.approved_directory).expanduser().resolve())
        return (resolved_cwd, thread_id.strip())

    async def _refresh(self, *, cwd: Optional[Path], thread_id: str) -> CodexLimitStatus:
        try:
            stdout, stderr, returncode = await self._run_codex(cwd=cwd)
            if returncode != 0:
                return self._with_local_fallback(
                    cwd=cwd,
                    thread_id=thread_id,
                    last_error=f"codex_cli_error:{returncode}:{stderr[:300]}",
                )
            parsed = self._parse_limit_output(stdout)
            if parsed is None:
                return self._with_local_fallback(
                    cwd=cwd,
                    thread_id=thread_id,
                    last_error="invalid_json",
                )
            return CodexLimitStatus(
                limit_5h=str(parsed.get("limit_5h") or UNKNOWN),
                limit_week=str(parsed.get("limit_week") or UNKNOWN),
                updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                context_used=self._optional_int(parsed.get("context_used")),
                context_limit=self._optional_int(parsed.get("context_limit")),
                input_tokens=self._optional_int(parsed.get("input_tokens")),
                cached_input_tokens=self._optional_int(parsed.get("cached_input_tokens")),
                output_tokens=self._optional_int(parsed.get("output_tokens")),
                total_tokens=self._optional_int(parsed.get("total_tokens")),
            )
        except asyncio.TimeoutError:
            return self._with_local_fallback(cwd=cwd, thread_id=thread_id, last_error="timeout")
        except FileNotFoundError:
            return self._with_local_fallback(
                cwd=cwd,
                thread_id=thread_id,
                last_error="codex_cli_not_found",
            )
        except Exception as exc:
            return self._with_local_fallback(cwd=cwd, thread_id=thread_id, last_error=str(exc))

    async def _run_codex(self, *, cwd: Optional[Path]) -> tuple[str, str, int]:
        run_cwd = (cwd or self.settings.approved_directory).resolve()
        cmd = [self.settings.codex_cli_path, "exec", "--json"]
        if self.settings.codex_skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["--sandbox", "read-only", "--config", 'web_search="disabled"'])
        if self.settings.codex_model:
            cmd.extend(["--model", self.settings.codex_model])
        if self.settings.codex_reasoning_effort:
            cmd.extend([
                "--config",
                f'model_reasoning_effort="{self.settings.codex_reasoning_effort}"',
            ])
        cmd.append(self.settings.status_line_limits_prompt)

        env = os.environ.copy()
        env["PWD"] = str(run_cwd)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(run_cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_group_kwargs(),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            await terminate_process_tree(process)
            raise
        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            int(process.returncode or 0),
        )

    @classmethod
    def _parse_limit_output(cls, stdout: str) -> Optional[dict[str, Any]]:
        direct = cls._parse_json_object(stdout)
        if direct is not None and ("limit_5h" in direct or "limit_week" in direct):
            return direct

        text_parts: list[str] = []
        latest_token_count: Optional[dict[str, Any]] = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            event = cls._parse_json_object(line)
            if event is None:
                continue
            token_count = cls._extract_token_count_status(event)
            if token_count is not None:
                latest_token_count = token_count
            if "limit_5h" in event or "limit_week" in event:
                return event
            text_parts.extend(cls._extract_event_text(event))

        if latest_token_count is not None:
            return latest_token_count

        return cls._parse_json_object("\n".join(text_parts))

    @classmethod
    def _extract_token_count_status(cls, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        payload = event.get("payload") if event.get("type") == "event_msg" else event
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            return None

        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
        if not isinstance(usage, dict):
            usage = {}

        total_tokens = cls._optional_int(usage.get("total_tokens"))
        if total_tokens is None:
            total_tokens = sum(
                value
                for value in (
                    cls._optional_int(usage.get("input_tokens")),
                    cls._optional_int(usage.get("output_tokens")),
                )
                if value is not None
            )

        rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
        primary = (
            rate_limits.get("primary")
            if isinstance(rate_limits.get("primary"), dict)
            else None
        )
        secondary = (
            rate_limits.get("secondary")
            if isinstance(rate_limits.get("secondary"), dict)
            else None
        )

        return {
            "limit_5h": cls._format_rate_limit_window(primary),
            "limit_week": cls._format_rate_limit_window(secondary),
            "context_used": total_tokens,
            "context_limit": cls._optional_int(info.get("model_context_window")),
            "input_tokens": cls._optional_int(usage.get("input_tokens")),
            "cached_input_tokens": cls._optional_int(usage.get("cached_input_tokens")),
            "output_tokens": cls._optional_int(usage.get("output_tokens")),
            "total_tokens": total_tokens,
        }

    @classmethod
    def _format_rate_limit_window(cls, window: Optional[dict[str, Any]]) -> str:
        if not window:
            return UNKNOWN
        used_percent = cls._optional_float(window.get("used_percent"))
        if used_percent is None:
            return UNKNOWN
        used = f"{used_percent:g}%"
        resets_at = cls._optional_int(window.get("resets_at"))
        if resets_at is None:
            return f"{used} used"
        reset_time = datetime.fromtimestamp(resets_at).astimezone().strftime("%H:%M")
        return f"{used} used, reset {reset_time}"

    @staticmethod
    def _optional_int(value: object) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: object) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_json_object(text: str) -> Optional[dict[str, Any]]:
        stripped = text.strip()
        if not stripped:
            return None
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        if not stripped.startswith("{"):
            match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
            if not match:
                return None
            stripped = match.group(0)
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _extract_event_text(event: dict[str, Any]) -> list[str]:
        parts: list[str] = []
        if event.get("type") == "agent_message_delta":
            text = str(event.get("delta") or event.get("text") or "")
            if text:
                parts.append(text)
        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "") or "")
            if item_type in {"agent_message", "assistant_message"}:
                text = str(item.get("text") or "")
                if text:
                    parts.append(text)
        return parts

    def _unknown(self, last_error: str) -> CodexLimitStatus:
        if self.logger is not None:
            try:
                self.logger.debug("codex_limit_status_unknown", error=last_error)
            except Exception:
                pass
        return CodexLimitStatus(last_error=last_error)

    def _with_local_fallback(
        self,
        *,
        cwd: Optional[Path],
        thread_id: str,
        last_error: str,
    ) -> CodexLimitStatus:
        local_status = self._read_latest_local_status(cwd=cwd, thread_id=thread_id)
        if local_status is None:
            return self._unknown(last_error)
        return CodexLimitStatus(
            limit_5h=local_status.limit_5h,
            limit_week=local_status.limit_week,
            updated_at=local_status.updated_at,
            last_error=last_error,
            context_used=local_status.context_used,
            context_limit=local_status.context_limit,
            input_tokens=local_status.input_tokens,
            cached_input_tokens=local_status.cached_input_tokens,
            output_tokens=local_status.output_tokens,
            total_tokens=local_status.total_tokens,
        )

    def _read_latest_local_status(
        self,
        *,
        cwd: Optional[Path],
        thread_id: str,
    ) -> Optional[CodexLimitStatus]:
        target_cwd = str((cwd or self.settings.approved_directory).expanduser().resolve())
        sessions_dir = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "sessions"
        if not sessions_dir.exists() or not sessions_dir.is_dir():
            return None

        try:
            session_files = sorted(
                sessions_dir.rglob("*.jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None

        for session_file in session_files:
            status = self._read_local_session_status(
                session_file=session_file,
                target_cwd=target_cwd,
                thread_id=thread_id,
            )
            if status is not None:
                return status
        return None

    @classmethod
    def _read_local_session_status(
        cls,
        *,
        session_file: Path,
        target_cwd: str,
        thread_id: str,
    ) -> Optional[CodexLimitStatus]:
        try:
            latest_status: Optional[dict[str, Any]] = None
            with session_file.open("r", encoding="utf-8") as handle:
                first_line = handle.readline()
                if not first_line:
                    return None
                try:
                    meta = json.loads(first_line)
                except json.JSONDecodeError:
                    return None
                payload = meta.get("payload") or {}
                if meta.get("type") != "session_meta":
                    return None
                if str(payload.get("cwd") or "") != target_cwd:
                    return None
                session_id = str(payload.get("id") or "")
                if thread_id and session_id != thread_id:
                    return None

                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token_count = cls._extract_token_count_status(event)
                    if token_count is not None:
                        latest_status = token_count
        except OSError:
            return None

        if latest_status is None:
            return None
        return CodexLimitStatus(
            limit_5h=str(latest_status.get("limit_5h") or UNKNOWN),
            limit_week=str(latest_status.get("limit_week") or UNKNOWN),
            updated_at=datetime.fromtimestamp(cls._safe_mtime(session_file), timezone.utc).isoformat(
                timespec="seconds"
            ),
            context_used=cls._optional_int(latest_status.get("context_used")),
            context_limit=cls._optional_int(latest_status.get("context_limit")),
            input_tokens=cls._optional_int(latest_status.get("input_tokens")),
            cached_input_tokens=cls._optional_int(latest_status.get("cached_input_tokens")),
            output_tokens=cls._optional_int(latest_status.get("output_tokens")),
            total_tokens=cls._optional_int(latest_status.get("total_tokens")),
        )

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
