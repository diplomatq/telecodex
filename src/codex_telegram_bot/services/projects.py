from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from telegram.ext import ContextTypes

from ..config import Settings
from ..models import RequestContext
from ..project_labels import render_project_display_name
from ..session_store import SessionStore

RecordEvent = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class RepoOption:
    key: str
    slug: str
    label: str
    is_current: bool = False


@dataclass(frozen=True)
class RecentProjectOption:
    key: str
    slug: str
    label: str
    is_current: bool = False


@dataclass(frozen=True)
class ProjectVisibilityOption:
    key: str
    label: str
    is_hidden: bool = False
    is_current: bool = False


@dataclass(frozen=True)
class ProjectResolution:
    path: Optional[Path]
    auto_created: bool = False


class ProjectService:
    def __init__(
        self,
        settings: Settings,
        record_event: RecordEvent,
        session_store: Optional[SessionStore] = None,
    ):
        self.settings = settings
        self._record_event = record_event
        self.session_store = session_store

    async def resolve_current_project(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        request_context: Optional[RequestContext] = None,
        create_if_empty: bool = True,
    ) -> ProjectResolution:
        root = self.settings.approved_directory.resolve()
        current = context.user_data.get("current_directory")
        if current is not None:
            try:
                current_path = Path(current).resolve()
                self.ensure_in_workspace(current_path)
                if self._is_allowed_project_path(current_path):
                    return ProjectResolution(path=current_path)
            except Exception:
                pass

        remembered = await self._resolve_remembered_project(request_context)
        if remembered is not None:
            context.user_data["current_directory"] = remembered
            return ProjectResolution(path=remembered)

        user_id = request_context.user_id if request_context is not None else None
        projects = self.list_project_paths(user_id=user_id)
        if projects:
            context.user_data["current_directory"] = projects[0]
            await self._remember_current_project(request_context, projects[0])
            return ProjectResolution(path=projects[0])

        if create_if_empty and self.workspace_is_empty(root):
            project = await self.create_project(None, context=context, request_context=request_context, auto=True)
            return ProjectResolution(path=project, auto_created=True)

        return ProjectResolution(path=None)

    def ensure_in_workspace(self, path: Path) -> None:
        candidate = path.resolve()
        for root in self.project_roots:
            try:
                candidate.relative_to(root)
                return
            except ValueError:
                continue
        raise PermissionError(f"Path outside approved directory: {path}")

    def list_repo_options(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        user_id: Optional[int] = None,
    ) -> tuple[list[RepoOption], bool]:
        current = context.user_data.get("current_directory")
        current_path = Path(current).resolve() if current is not None else None
        entries = self.list_project_paths(user_id=user_id)
        truncated = len(entries) > 20
        options = [
            RepoOption(
                key=self.path_to_key(entry),
                slug=entry.name,
                label=self.render_project_label(entry),
                is_current=current_path is not None and entry == current_path,
            )
            for entry in entries[:20]
        ]
        return options, truncated

    def resolve_repo_slug(self, slug: str, *, user_id: Optional[int] = None) -> Path:
        candidates = [path for path in self.list_project_paths(user_id=user_id) if path.name == slug]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise FileNotFoundError(slug)
        raise PermissionError(slug)

    def resolve_repo_key(
        self,
        key: str,
        *,
        user_id: Optional[int] = None,
        include_hidden: bool = False,
    ) -> Path:
        candidate = Path(key).expanduser().resolve()
        self.ensure_in_workspace(candidate)
        if not candidate.exists():
            raise FileNotFoundError(key)
        if not candidate.is_dir():
            raise NotADirectoryError(key)
        if not self._is_allowed_project_path(candidate):
            raise PermissionError(key)
        if not include_hidden and user_id is not None and self._is_hidden_for_user(user_id, candidate):
            raise PermissionError(key)
        return candidate

    def list_project_paths(
        self,
        *,
        user_id: Optional[int] = None,
        include_hidden: bool = False,
    ) -> list[Path]:
        visible_names = {name.strip() for name in self.settings.project_visible_names if name.strip()}
        ignored_names = {name.strip() for name in self.settings.project_ignore_names if name.strip()}
        hidden_paths = self._hidden_project_paths(user_id) if user_id is not None and not include_hidden else set()
        entries: list[Path] = []
        primary_root = self.settings.approved_directory.resolve()
        for root in self.project_roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in sorted(root.iterdir()):
                if not path.is_dir() or path.name.startswith("."):
                    continue
                if root == primary_root and path in self.settings.additional_project_directories:
                    continue
                if path.name in ignored_names:
                    continue
                if visible_names and path.name not in visible_names:
                    continue
                resolved = path.resolve()
                if str(resolved) in hidden_paths:
                    continue
                entries.append(resolved)
        entries.sort(key=lambda item: (self.render_project_label(item), str(item)))
        return entries

    def list_project_path_strings(
        self,
        *,
        user_id: Optional[int] = None,
        include_hidden: bool = False,
    ) -> list[str]:
        return [
            str(path.resolve())
            for path in self.list_project_paths(user_id=user_id, include_hidden=include_hidden)
        ]

    async def list_recent_repo_options(
        self,
        *,
        user_id: int,
        current_project_path: Optional[Path],
        limit: int = 3,
    ) -> list[RecentProjectOption]:
        if self.session_store is None or limit <= 0:
            return []

        available_paths = self.list_project_paths(user_id=user_id)
        available_path_map = {str(path.resolve()): path for path in available_paths}
        recent_paths = await self.session_store.list_recent_projects(
            user_id,
            available_project_paths=list(available_path_map.keys()),
            current_project_path=str(current_project_path.resolve()) if current_project_path is not None else "",
            limit=limit,
        )
        return [
            RecentProjectOption(
                key=self.path_to_key(available_path_map[path]),
                slug=available_path_map[path].name,
                label=self.render_project_label(available_path_map[path]),
                is_current=current_project_path is not None and available_path_map[path] == current_project_path,
            )
            for path in recent_paths
            if path in available_path_map
        ]

    async def list_project_visibility_options(
        self,
        *,
        user_id: int,
        current_project_path: Optional[Path] = None,
    ) -> list[ProjectVisibilityOption]:
        hidden_paths = set(await self.session_store.list_hidden_projects(user_id)) if self.session_store else set()
        entries = self.list_project_paths(user_id=user_id, include_hidden=True)
        current_key = str(current_project_path.resolve()) if current_project_path is not None else ""
        return [
            ProjectVisibilityOption(
                key=self.path_to_key(entry),
                label=self.render_project_label(entry),
                is_hidden=str(entry.resolve()) in hidden_paths,
                is_current=str(entry.resolve()) == current_key,
            )
            for entry in entries
        ]

    async def set_project_hidden_state(
        self,
        *,
        user_id: int,
        project_path: Path,
        hidden: bool,
    ) -> None:
        if self.session_store is None:
            return
        resolved = project_path.resolve()
        if not self._is_allowed_project_path(resolved):
            raise PermissionError(str(project_path))
        await self.session_store.set_project_hidden_state(user_id, str(resolved), hidden=hidden)

    def workspace_is_empty(self, root: Path) -> bool:
        for entry in root.iterdir():
            if entry.name == ".DS_Store":
                continue
            if entry.suffix in {".sqlite", ".sqlite3"}:
                continue
            if entry.name.endswith((".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm")):
                continue
            if entry.name in {
                self.settings.sqlite_path.name,
                f"{self.settings.sqlite_path.name}-wal",
                f"{self.settings.sqlite_path.name}-shm",
            }:
                continue
            return False
        return True

    @property
    def project_roots(self) -> list[Path]:
        roots = [self.settings.approved_directory.resolve(), *self.settings.additional_project_directories]
        resolved: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            path = Path(root).expanduser().resolve()
            if path not in seen:
                resolved.append(path)
                seen.add(path)
        return resolved

    def render_project_label(self, path: Path) -> str:
        return render_project_display_name(path)

    def path_to_key(self, path: Path) -> str:
        return str(path.resolve())

    def _find_project_root(self, path: Path) -> Optional[Path]:
        resolved = path.resolve()
        matching_roots: list[Path] = []
        for root in self.project_roots:
            try:
                resolved.relative_to(root)
                matching_roots.append(root)
            except ValueError:
                continue
        if not matching_roots:
            return None
        return max(matching_roots, key=lambda item: len(item.parts))

    def _is_allowed_project_path(self, path: Path) -> bool:
        resolved = path.resolve()
        root = self._find_project_root(resolved)
        if root is None:
            return False
        if not resolved.exists() or not resolved.is_dir():
            return False
        if resolved.parent != root:
            return False
        visible_names = {name.strip() for name in self.settings.project_visible_names if name.strip()}
        ignored_names = {name.strip() for name in self.settings.project_ignore_names if name.strip()}
        if resolved.name in ignored_names:
            return False
        if visible_names and resolved.name not in visible_names:
            return False
        return True

    def _hidden_project_paths(self, user_id: int) -> set[str]:
        if self.session_store is None:
            return set()
        try:
            return set(self.session_store.list_hidden_projects_sync(user_id))
        except Exception:
            return set()

    def _is_hidden_for_user(self, user_id: int, path: Path) -> bool:
        return str(path.resolve()) in self._hidden_project_paths(user_id)

    @staticmethod
    def sanitize_project_name(name: str) -> str:
        candidate = name.strip().lower()
        candidate = re.sub(r"[\\/]+", "-", candidate)
        candidate = re.sub(r"[^a-z0-9._-]+", "-", candidate)
        candidate = re.sub(r"-{2,}", "-", candidate).strip(" .-_")
        if not candidate or candidate in {".", ".."}:
            raise ValueError("Invalid project name")
        return candidate

    def default_project_slug(self) -> str:
        return f"{datetime.now().astimezone().date().isoformat()}-project"

    def next_available_project_path(self, base_slug: str) -> Path:
        base = self.settings.approved_directory.resolve()
        candidate = base / base_slug
        if not candidate.exists():
            return candidate
        suffix = 2
        while True:
            candidate = base / f"{base_slug}-{suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1

    async def create_project(
        self,
        name: Optional[str],
        *,
        context: ContextTypes.DEFAULT_TYPE,
        request_context: Optional[RequestContext],
        auto: bool = False,
    ) -> Path:
        requested_name = name.strip() if name else ""
        await self._record_event(
            "project_create_requested",
            request_context,
            audit_event="project_create_requested",
            requested_name=requested_name,
            auto=auto,
        )
        try:
            base_slug = self.default_project_slug() if auto else self.sanitize_project_name(requested_name)
            project_path = self.next_available_project_path(base_slug)
            project_path.mkdir(parents=False, exist_ok=False)
        except Exception as exc:
            await self._record_event(
                "project_create_failed",
                request_context,
                audit_event="project_create_failed",
                event_status="failed",
                requested_name=requested_name,
                auto=auto,
                error_message=str(exc),
                level="warning",
            )
            raise

        context.user_data["current_directory"] = project_path
        await self._remember_current_project(request_context, project_path)
        await self._record_event(
            "project_auto_created" if auto else "project_created",
            request_context,
            audit_event="project_auto_created" if auto else "project_created",
            event_status="created",
            selected_project=project_path.name,
            requested_name=requested_name,
            auto=auto,
        )
        return project_path

    async def remember_selected_project(
        self,
        request_context: Optional[RequestContext],
        project_path: Path,
    ) -> None:
        await self._remember_current_project(request_context, project_path)

    async def _resolve_remembered_project(
        self,
        request_context: Optional[RequestContext],
    ) -> Optional[Path]:
        if self.session_store is None or request_context is None or request_context.user_id is None:
            return None
        remembered = await self.session_store.get_current_project(request_context.user_id)
        if not remembered:
            return None
        try:
            remembered_path = Path(remembered).resolve()
            self.ensure_in_workspace(remembered_path)
            if self._is_allowed_project_path(remembered_path):
                return remembered_path
        except Exception:
            return None
        return None

    async def _remember_current_project(
        self,
        request_context: Optional[RequestContext],
        project_path: Path,
    ) -> None:
        if self.session_store is None or request_context is None or request_context.user_id is None:
            return
        await self.session_store.set_current_project(
            request_context.user_id,
            str(project_path.resolve()),
        )
