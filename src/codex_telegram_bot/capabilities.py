from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeCapabilities:
    text: bool = True
    files: bool = True
    voice: bool = True
    images: bool = False
    live_status_limits: bool = False
    voice_provider: str = ""
    voice_model: str = ""


def resolve_runtime_capabilities(settings) -> RuntimeCapabilities:
    voice_enabled = bool(getattr(settings, "enable_voice_messages", False))
    voice_model = ""
    if voice_enabled:
        voice_model = getattr(settings, "resolved_voice_model", "") or ""
    return RuntimeCapabilities(
        text=True,
        files=bool(getattr(settings, "enable_file_uploads", True)),
        voice=voice_enabled,
        images=bool(getattr(settings, "codex_enable_images", False)),
        live_status_limits=bool(str(getattr(settings, "status_line_limits_prompt", "")).strip()),
        voice_provider=str(getattr(settings, "voice_provider", "")) if voice_enabled else "",
        voice_model=voice_model,
    )
