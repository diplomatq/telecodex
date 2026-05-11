from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class CodexResultStatus(StringEnum):
    SUCCESS = "success"
    INTERRUPTED = "interrupted"
    RESUME_FAILED = "resume_failed"
    TIMEOUT = "timeout"
    CLI_ERROR = "cli_error"
    PROTOCOL_ERROR = "protocol_error"


class ProjectRunStatus(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = CodexResultStatus.SUCCESS.value
    STOPPED_BY_USER = "stopped_by_user"
    ORPHANED_AFTER_RESTART = "orphaned_after_restart"
    RESUME_FAILED = CodexResultStatus.RESUME_FAILED.value
    TIMEOUT = CodexResultStatus.TIMEOUT.value
    CLI_ERROR = CodexResultStatus.CLI_ERROR.value
    PROTOCOL_ERROR = CodexResultStatus.PROTOCOL_ERROR.value

    @classmethod
    def from_value(cls, value: Any) -> "ProjectRunStatus":
        if isinstance(value, cls):
            return value
        normalized = str(value or cls.QUEUED.value).strip().lower()
        for candidate in cls:
            if candidate.value == normalized:
                return candidate
        if normalized == CodexResultStatus.INTERRUPTED.value:
            return cls.STOPPED_BY_USER
        return cls.QUEUED


class CodexLaunchMode(StringEnum):
    SANDBOX = "sandbox"
    FULL_ACCESS = "full_access"

    @classmethod
    def from_value(cls, value: Any) -> "CodexLaunchMode":
        if isinstance(value, cls):
            return value
        normalized = str(value or cls.SANDBOX.value).strip().lower()
        if normalized == cls.FULL_ACCESS.value:
            return cls.FULL_ACCESS
        return cls.SANDBOX


class CodexStreamEventKind(StringEnum):
    TEXT_DELTA = "text_delta"
    TEXT_SNAPSHOT = "text_snapshot"
    TOOL_CALL = "tool_call"
    LIFECYCLE = "lifecycle"
    USAGE = "usage"
    UNKNOWN = "unknown"


@dataclass
class CodexToolCall:
    name: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodexStreamEvent:
    type: str
    kind: CodexStreamEventKind = CodexStreamEventKind.UNKNOWN
    text_delta: str = ""
    text_snapshot: str = ""
    thread_id: str = ""
    tool_call: Optional[CodexToolCall] = None
    lifecycle_name: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodexResponse:
    final_text: str
    thread_id: str
    status: CodexResultStatus = CodexResultStatus.SUCCESS
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error_message: str = ""
    fallback_reason: str = ""
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    interrupted: bool = False


@dataclass
class ProjectSession:
    user_id: int
    project_path: str
    thread_id: str
    updated_at: str
    title: str = ""
    last_status: str = ""
    last_error: str = ""


@dataclass
class LocalCodexSession:
    session_id: str
    cwd: Path
    created_at: datetime
    updated_at: datetime
    source_path: Path
    first_prompt: str = ""
    title: str = ""


@dataclass
class SessionTranscriptEntry:
    role: str
    text: str


@dataclass
class SessionTranscript:
    session_id: str
    cwd: Path
    source_path: Path
    title: str = ""
    entries: list[SessionTranscriptEntry] = field(default_factory=list)
    truncated: bool = False


@dataclass
class ProjectRun:
    run_id: int
    user_id: int
    project_path: str
    thread_id: str
    status: ProjectRunStatus
    started_at: datetime
    finished_at: Optional[datetime]
    last_update_at: datetime
    first_prompt_preview: str = ""
    last_progress_summary: str = ""
    first_tool_name: str = ""
    tool_count: int = 0
    error_message: str = ""
    stop_requested: bool = False

    @property
    def project_name(self) -> str:
        return Path(self.project_path).name

    @property
    def is_active(self) -> bool:
        return self.status == ProjectRunStatus.RUNNING


@dataclass
class ProjectActivitySummary:
    project_path: str
    project_name: str
    is_current: bool
    current_session_thread_id: str = ""
    current_session_title: str = ""
    active_run: Optional[ProjectRun] = None
    latest_run: Optional[ProjectRun] = None
    recent_run_count: int = 0
    is_live: bool = False


@dataclass
class PreparedCodexRequest:
    prompt: str
    source: str
    image_paths: list[Path] = field(default_factory=list)
    cleanup_paths: list[Path] = field(default_factory=list)


@dataclass
class RequestContext:
    source: str
    user_id: Optional[int] = None
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    chat_type: str = ""
    cwd: str = ""
    command_name: str = ""
    has_previous_thread: bool = False
    prompt_chars: int = 0
    caption_chars: int = 0
    document_name: str = ""
    image_count: int = 0
    voice_duration_seconds: int = 0
    launch_mode: str = ""


@dataclass
class ProcessedVoice:
    prompt: str
    transcription: str
    duration_seconds: int


@dataclass
class ProcessedDocument:
    prompt: str
    filename: str


@dataclass
class ProcessedImage:
    prompt: str
    image_path: Path
