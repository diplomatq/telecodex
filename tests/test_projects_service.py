from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_telegram_bot.config import Settings
from codex_telegram_bot.models import RequestContext
from codex_telegram_bot.project_labels import render_project_display_name
from codex_telegram_bot.services.projects import ProjectService
from codex_telegram_bot.session_store import SessionStore


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_bot_username": "codex_bot",
        "approved_directory": tmp_path,
        "allowed_users": "42",
    }
    values.update(overrides)
    values.setdefault("_env_file", None)
    return Settings(**values)


async def noop_record_event(*args, **kwargs) -> None:
    return None


def test_workspace_is_empty_ignores_sqlite_sidecars(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event)

    (tmp_path / "db.sqlite3").write_text("")
    (tmp_path / "db.sqlite3-wal").write_text("")
    (tmp_path / "db.sqlite3-shm").write_text("")

    assert service.workspace_is_empty(tmp_path) is True

    (tmp_path / "README.md").write_text("content")
    assert service.workspace_is_empty(tmp_path) is False


def test_list_repo_options_marks_current_project(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event)
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()
    context = SimpleNamespace(user_data={"current_directory": tmp_path / "web"})

    options, truncated = service.list_repo_options(context)

    assert truncated is False
    assert [option.slug for option in options] == ["api", "web"]
    assert options[0].is_current is False
    assert options[1].is_current is True
    assert options[1].label == f"{tmp_path.name}/web"


def test_list_repo_options_supports_extra_roots_and_filters(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "secret").mkdir()
    (extra / "worker").mkdir()
    settings = make_settings(
        tmp_path,
        additional_project_directories=[extra],
        project_ignore_names=["secret"],
    )
    service = ProjectService(settings, noop_record_event)
    context = SimpleNamespace(user_data={})

    options, truncated = service.list_repo_options(context)

    assert truncated is False
    assert [option.label for option in options] == [
        f"{extra.name}/worker",
        f"{tmp_path.name}/api",
    ]
    assert all(option.key.startswith("/") for option in options)


def test_list_repo_options_respects_visible_names(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()
    (extra / "worker").mkdir()
    settings = make_settings(
        tmp_path,
        additional_project_directories=[extra],
        project_visible_names=["worker", "api"],
    )
    service = ProjectService(settings, noop_record_event)

    options, _ = service.list_repo_options(SimpleNamespace(user_data={}))

    assert [option.label for option in options] == [
        f"{extra.name}/worker",
        f"{tmp_path.name}/api",
    ]


def test_render_project_display_name_uses_parent_and_project_segments() -> None:
    assert render_project_display_name(Path("/opt/demo")) == "opt/demo"
    assert render_project_display_name(Path("/www/wwwroot/site")) == "wwwroot/site"
    assert render_project_display_name(Path("/single")) == "single"


@pytest.mark.asyncio
async def test_user_hidden_projects_filter_with_env_rules(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()
    extra = tmp_path / "extra"
    extra.mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "secret").mkdir()
    (extra / "worker").mkdir()
    settings = make_settings(
        tmp_path,
        additional_project_directories=[extra],
        project_ignore_names=["secret"],
        project_visible_names=["api", "worker", "secret"],
    )
    service = ProjectService(settings, noop_record_event, store)

    await store.set_project_hidden_state(42, str((tmp_path / "api").resolve()), hidden=True)

    visible_for_42 = service.list_project_path_strings(user_id=42)
    visible_for_7 = service.list_project_path_strings(user_id=7)
    all_for_42 = service.list_project_path_strings(user_id=42, include_hidden=True)

    assert str((tmp_path / "secret").resolve()) not in visible_for_42
    assert str((tmp_path / "api").resolve()) not in visible_for_42
    assert str((extra / "worker").resolve()) in visible_for_42
    assert str((tmp_path / "api").resolve()) in visible_for_7
    assert str((tmp_path / "api").resolve()) in all_for_42
    await store.close()


@pytest.mark.asyncio
async def test_hidden_current_project_stays_resolved_but_drops_from_repo_lists(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event, store)
    api = tmp_path / "api"
    web = tmp_path / "web"
    api.mkdir()
    web.mkdir()
    await store.set_current_project(42, str(api.resolve()))
    await store.set_project_hidden_state(42, str(api.resolve()), hidden=True)
    context = SimpleNamespace(user_data={})

    project = await service.resolve_current_project(
        context,
        request_context=RequestContext(source="command", user_id=42),
        create_if_empty=False,
    )
    options, _ = service.list_repo_options(context, user_id=42)

    assert project.path == api.resolve()
    assert [option.slug for option in options] == ["web"]
    await store.close()


@pytest.mark.asyncio
async def test_create_project_sanitizes_name_and_updates_context(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event)
    context = SimpleNamespace(user_data={})

    project = await service.create_project(
        "My API",
        context=context,
        request_context=RequestContext(source="command"),
    )

    assert project.name == "my-api"
    assert context.user_data["current_directory"] == project
    assert project.is_dir()


@pytest.mark.asyncio
async def test_resolve_current_project_prefers_remembered_project(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event, store)
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()
    await store.set_current_project(42, str((tmp_path / "web").resolve()))
    context = SimpleNamespace(user_data={})

    project = await service.resolve_current_project(
        context,
        request_context=RequestContext(source="command", user_id=42),
    )

    assert project.path == (tmp_path / "web").resolve()
    assert context.user_data["current_directory"] == (tmp_path / "web").resolve()
    await store.close()


@pytest.mark.asyncio
async def test_list_recent_repo_options_returns_current_first(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()
    settings = make_settings(tmp_path)
    service = ProjectService(settings, noop_record_event, store)
    (tmp_path / "api").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "ops").mkdir()

    await store.set_current_project(42, str((tmp_path / "web").resolve()))
    await store.set_current_project(42, str((tmp_path / "ops").resolve()))

    recent = await service.list_recent_repo_options(
        user_id=42,
        current_project_path=(tmp_path / "api").resolve(),
        limit=3,
    )

    assert [option.slug for option in recent] == ["api", "ops", "web"]
    assert recent[0].is_current is True
    assert recent[1].is_current is False
    await store.close()
