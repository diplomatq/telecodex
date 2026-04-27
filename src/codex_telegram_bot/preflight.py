from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .capabilities import resolve_runtime_capabilities
from .config import Settings
from .session_store import SessionStore


@dataclass
class PreflightReport:
    sqlite_ok: bool = False
    codex_cli_ok: bool = False
    workspace_ok: bool = False
    voice_ok: bool = False
    orphaned_run_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.sqlite_ok and self.codex_cli_ok and self.workspace_ok and self.voice_ok and not self.errors


async def run_preflight(
    *,
    settings: Settings,
    store: SessionStore,
    codex_cli_validator,
    finalize_orphaned_runs: bool = True,
) -> PreflightReport:
    report = PreflightReport()

    if not settings.approved_directory.exists() or not settings.approved_directory.is_dir():
        report.errors.append(f"APPROVED_DIRECTORY is invalid: {settings.approved_directory}")
    else:
        report.workspace_ok = True

    if await store.health_check():
        report.sqlite_ok = True
    else:
        report.errors.append("Session store health check failed")

    try:
        codex_cli_validator()
        report.codex_cli_ok = True
    except Exception as exc:
        report.errors.append(str(exc))

    capabilities = getattr(settings, "runtime_capabilities", None) or resolve_runtime_capabilities(settings)
    if not capabilities.voice:
        report.voice_ok = True
    elif settings.voice_provider == "openai":
        report.voice_ok = bool(settings.openai_api_key_str)
        if not report.voice_ok:
            report.errors.append("OPENAI_API_KEY is required when voice is enabled with VOICE_PROVIDER=openai")
    else:
        report.voice_ok = bool(
            settings.voice_api_key_str
            and settings.voice_api_base_url
            and capabilities.voice_model
        )
        if not report.voice_ok:
            report.errors.append("VOICE_API_KEY, VOICE_API_BASE_URL, and VOICE_TRANSCRIPTION_MODEL are required for voice")

    if finalize_orphaned_runs:
        report.orphaned_run_count = await store.finalize_orphaned_runs()

    return report


def render_preflight_report(report: PreflightReport, *, sqlite_path: Path) -> str:
    lines = [
        "Telecodex self-check",
        "",
        f"SQLite: {'ok' if report.sqlite_ok else 'failed'}",
        f"Codex CLI: {'ok' if report.codex_cli_ok else 'failed'}",
        f"Workspace: {'ok' if report.workspace_ok else 'failed'}",
        f"Voice: {'ok' if report.voice_ok else 'failed'}",
        f"Orphaned runs finalized: {report.orphaned_run_count}",
        f"SQLite path: {sqlite_path}",
    ]
    if report.errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in report.errors)
    return "\n".join(lines)
