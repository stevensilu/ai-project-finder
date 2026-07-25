#!/usr/bin/env python3
"""AI Project Finder: local-only cross-AI session index and dashboard."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
INDEX_FILE = DATA_DIR / "index.json"
MANUAL_FILE = DATA_DIR / "manual.json"
OPEN_LOG_FILE = DATA_DIR / "open.log"
CONFIG_FILE = APP_DIR / "config.json"
DEMO_FIXTURE_FILE = APP_DIR / "demo" / "fixtures.json"
DEMO_DEFAULT_PORT = 4390
DEMO_MODE = False
IS_WINDOWS = os.name == "nt"
APP_LOCALE = "en"

DEFAULT_CONFIG: dict[str, Any] = {
    "port": 4388,
    "max_prompt_chars": 9000,
    "locale": "en",
    "sources": {
        "codex": "auto",
        "claude": "auto",
        "kimi": "auto",
        "kimi-desktop": "auto",
    },
}

HOME_DIR = Path.home()
HOME_PATTERN = re.escape(str(HOME_DIR))
PATH_SEPARATOR_PATTERN = r"[\\/]"
PATH_RE = re.compile(
    rf"{HOME_PATTERN}{PATH_SEPARATOR_PATTERN}[^\n\r\"'<>]{{1,360}}?\.(?:html?|xlsx?|csv|pptx?|pdf|docx?|md|txt|json|png|jpe?g|gif|mp4|mov|zip|skill)",
    re.IGNORECASE,
)
INTERNAL_STORAGE_FILE_RE = re.compile(
    rf"{HOME_PATTERN}/Library/Application Support/[^\n\r\"'<>]{{1,800}}?\.(?:html?|xlsx?|csv|pptx?|pdf|docx?|md|txt|json|png|jpe?g|gif|mp4|mov|zip|skill)",
    re.IGNORECASE,
)
INTERNAL_STORAGE_PATH_RE = re.compile(
    rf"{HOME_PATTERN}/Library/Application Support/[^\s\n\r\"'<>]+",
    re.IGNORECASE,
)
INJECTED_SKILL_PAYLOAD_RE = re.compile(
    rf"\s*Base directory for this skill:\s*{HOME_PATTERN}/Library/Application Support/[\s\S]*$",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
DATE_PART_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
NON_USER_CODEX_THREAD_SOURCES = {"subagent", "automation"}
GENERIC_DIRS = {
    HOME_DIR.name.lower(),
    "xia",
    "new-chat",
    "new-chat-2",
    "new-chat-3",
    "documents",
    "downloads",
    "desktop",
    "coworkos",
}


def environment_home(name: str, default: Path) -> Path:
    raw = str(os.environ.get(name) or "").strip()
    return Path(raw).expanduser() if raw else default


def normalize_locale(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    return "zh-CN" if raw.startswith("zh") else "en"


def automatic_source_paths() -> dict[str, list[Path]]:
    codex_home = environment_home("CODEX_HOME", HOME_DIR / ".codex")
    claude_home = environment_home("CLAUDE_CONFIG_DIR", HOME_DIR / ".claude")
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    paths = {
        "codex": [codex_home / "sessions"],
        "claude": [claude_home / "projects"],
        "kimi": [kimi_home / "sessions"],
        "kimi-desktop": [],
    }
    if IS_WINDOWS:
        for variable in ("APPDATA", "LOCALAPPDATA"):
            base = str(os.environ.get(variable) or "").strip()
            if not base:
                continue
            for app_folder in ("kimi-desktop", "Kimi Desktop", "Kimi"):
                paths["kimi-desktop"].append(
                    Path(base)
                    / app_folder
                    / "daimon-share"
                    / "daimon"
                    / "runtime"
                    / "kimi-code"
                    / "home"
                    / "sessions"
                )
        paths["kimi-desktop"] = list(dict.fromkeys(paths["kimi-desktop"]))
    elif Path("/Applications").exists():
        paths["kimi-desktop"].append(
            HOME_DIR
            / "Library"
            / "Application Support"
            / "kimi-desktop"
            / "daimon-share"
            / "daimon"
            / "runtime"
            / "kimi-code"
            / "home"
            / "sessions"
        )
    return paths


def expand_source_value(value: Any) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in values:
        text = os.path.expandvars(str(item or "").strip())
        if not text or text.lower() == "auto":
            continue
        paths.append(Path(text).expanduser())
    return paths


def resolved_source_paths(config: dict[str, Any]) -> dict[str, list[Path]]:
    automatic = automatic_source_paths()
    configured = config.get("sources", {})
    result: dict[str, list[Path]] = {}
    for source, defaults in automatic.items():
        value = configured.get(source, "auto") if isinstance(configured, dict) else "auto"
        custom = expand_source_value(value)
        result[source] = custom or defaults
    return result


def load_demo_payload() -> dict[str, Any]:
    """Load synthetic records without touching local AI history or data files."""
    try:
        fixture = json.loads(DEMO_FIXTURE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("demo fixture unavailable") from exc
    rows = fixture.get("records", []) if isinstance(fixture, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError("demo fixture records must be a list")

    now = datetime.now(tz=timezone.utc)
    records: list[dict[str, Any]] = []
    locale_overrides = (
        fixture.get("translations", {}).get(APP_LOCALE, {})
        if isinstance(fixture.get("translations"), dict)
        else {}
    )
    labels = {
        "codex": "Codex",
        "claude": "Claude",
        "kimi": "Kimi Code",
        "kimi-desktop": "Kimi Desktop",
    }
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip().lower()
        session_id = str(raw.get("session_id") or f"demo-{position:03d}").strip()
        localized = (
            locale_overrides.get(session_id, {})
            if isinstance(locale_overrides, dict)
            else {}
        )
        if not isinstance(localized, dict):
            localized = {}
        project = str(
            localized.get("project") or raw.get("project") or "Demo Project"
        ).strip()
        title = str(localized.get("title") or raw.get("title") or project).strip()
        if source not in labels or not SESSION_ID_RE.fullmatch(session_id):
            raise RuntimeError(f"invalid demo record at position {position}")
        try:
            updated_hours = max(0.0, float(raw.get("updated_hours_ago", position)))
            created_hours = max(updated_hours, float(raw.get("created_hours_ago", updated_hours + 2)))
            message_count = max(1, int(raw.get("message_count", 1)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid demo timing at position {position}") from exc
        updated_at = (now - timedelta(hours=updated_hours)).isoformat()
        created_at = (now - timedelta(hours=created_hours)).isoformat()
        default_workspace = (
            f"~/演示工作区/{project}"
            if APP_LOCALE == "zh-CN"
            else f"~/Demo Workspaces/{project}"
        )
        cwd = str(localized.get("cwd") or raw.get("cwd") or default_workspace).strip()
        excerpt = str(localized.get("excerpt") or raw.get("excerpt") or "").strip()
        raw_artifacts = localized.get("artifacts", raw.get("artifacts", []))
        artifacts = [
            str(item).strip()
            for item in raw_artifacts
            if str(item).strip()
        ] if isinstance(raw_artifacts, list) else []
        record = {
            "id": f"{source}:{session_id}",
            "source": source,
            "source_label": labels[source],
            "session_id": session_id,
            "session_path": f"demo://sessions/{session_id}",
            "cwd": cwd,
            "project": project,
            "customer": "",
            "title": title,
            "excerpt": excerpt,
            "artifacts": artifacts,
            "created_at": created_at,
            "updated_at": updated_at,
            "message_count": message_count,
            "origin": str(localized.get("origin") or raw.get("origin") or labels[source]),
            "search_text": " ".join(
                [title, project, cwd, excerpt, *artifacts]
            ).lower(),
        }
        if source == "kimi-desktop":
            record["open_label"] = "Open Kimi Desktop ↗"
        records.append(record)

    records.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    counts = Counter(str(item["source"]) for item in records)
    projects = {str(item["project"]) for item in records if item.get("project")}
    return {
        "demo": True,
        "locale": APP_LOCALE,
        "generated_at": now.isoformat(),
        "records": records,
        "summary": {
            "records": len(records),
            "projects": len(projects),
            "sources": dict(counts),
            "errors": 0,
            "available_sources": len(counts),
        },
        "source_status": {
            source: {"available": True, "paths": [], "demo": True}
            for source in counts
        },
        "warnings": [],
    }


def demo_open_mode(record: dict[str, Any], action: str) -> str:
    """Return a display-only open mode and never invoke a system application."""
    if action == "workspace":
        return "workspace"
    source = str(record.get("source") or "").strip().lower()
    if action == "cli":
        if source != "kimi":
            raise ValueError("CLI open is only available for Kimi Code sessions")
        return "kimi-cli"
    if action != "session":
        raise ValueError("unsupported action")
    return {
        "codex": "codex",
        "claude": "claude",
        "kimi": "kimi-web",
        "kimi-desktop": "kimi-desktop",
    }.get(source, "local")


def load_index_payload() -> dict[str, Any]:
    if DEMO_MODE:
        return load_demo_payload()
    if not INDEX_FILE.exists():
        return build_index()
    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else build_index()
    except (OSError, json.JSONDecodeError):
        return build_index()


def find_indexed_record(record_id: str) -> dict[str, Any] | None:
    if not record_id or len(record_id) > 260:
        return None
    records = load_index_payload().get("records", [])
    if not isinstance(records, list):
        return None
    return next(
        (record for record in records if isinstance(record, dict) and str(record.get("id") or "") == record_id),
        None,
    )


def system_open_command(target: str) -> list[str]:
    if IS_WINDOWS:
        return ["cmd.exe", "/d", "/s", "/c", "start", "", target]
    opener = "/usr/bin/open" if Path("/usr/bin/open").exists() else shutil.which("xdg-open")
    if not opener:
        raise FileNotFoundError("system opener unavailable")
    return [str(opener), target]


def record_open_event(
    record: dict[str, Any],
    action: str,
    *,
    outcome: str,
    mode: str = "",
    error: str = "",
) -> None:
    """Keep a small local diagnostic trail without storing prompt content."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if OPEN_LOG_FILE.exists() and OPEN_LOG_FILE.stat().st_size > 1_000_000:
            OPEN_LOG_FILE.replace(OPEN_LOG_FILE.with_suffix(".previous.log"))
        event = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "record_id": str(record.get("id") or ""),
            "source": str(record.get("source") or ""),
            "action": action,
            "outcome": outcome,
            "mode": mode,
            "error": error[:300],
        }
        with OPEN_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def claude_desktop_session_roots() -> list[tuple[str, Path]]:
    """Return observed Claude Desktop profiles, newest activity first."""
    if IS_WINDOWS:
        application_roots = [
            Path(value)
            for value in (
                os.environ.get("APPDATA"),
                os.environ.get("LOCALAPPDATA"),
            )
            if value
        ]
        profile_roots = [(root, root) for root in dict.fromkeys(application_roots)]
    else:
        profile_roots = [
            (
                HOME_DIR / "Library" / "Application Support",
                HOME_DIR / "Library" / "Logs",
            )
        ]
    candidates: list[tuple[float, str, Path]] = []
    profile_names = ["Claude"]
    configured_profiles = [
        item.strip()
        for item in str(os.environ.get("AI_PROJECT_FINDER_CLAUDE_PROFILES") or "").split(",")
        if item.strip()
    ]
    profile_names.extend(configured_profiles)
    for application_support, logs_root in profile_roots:
        discovered_profiles = list(profile_names)
        try:
            discovered_profiles.extend(
                path.name
                for path in application_support.glob("Claude*")
                if (path / "claude-code-sessions").is_dir()
            )
        except OSError:
            pass
        for profile in dict.fromkeys(discovered_profiles):
            sessions_root = application_support / profile / "claude-code-sessions"
            if not sessions_root.is_dir():
                continue
            activity_times: list[float] = []
            try:
                activity_times.append(sessions_root.stat().st_mtime)
            except OSError:
                pass
            try:
                activity_times.extend(
                    path.stat().st_mtime
                    for path in (logs_root / profile).glob("main*.log")
                )
            except OSError:
                pass
            candidates.append((max(activity_times, default=0.0), profile, sessions_root))
    candidates.sort(key=lambda item: item[0], reverse=True)
    unique: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for _, profile, sessions_root in candidates:
        if sessions_root in seen:
            continue
        seen.add(sessions_root)
        unique.append((profile, sessions_root))
    return unique


def active_claude_desktop_sessions_root() -> tuple[str, Path] | None:
    """Return the most recently active Claude Desktop profile's metadata root."""
    roots = claude_desktop_session_roots()
    return roots[0] if roots else None


def load_claude_desktop_session_map(*, all_profiles: bool = False) -> dict[str, dict[str, str]]:
    """Map Claude Code CLI IDs to their original Desktop session metadata."""
    roots = claude_desktop_session_roots()
    if not roots:
        return {}
    ranked: dict[str, tuple[tuple[int, int, int, int], dict[str, str]]] = {}
    selected_roots = roots if all_profiles else roots[:1]
    for profile_priority, (profile, sessions_root) in enumerate(reversed(selected_roots), start=1):
        for path in sessions_root.rglob("local_*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            cli_session_id = str(payload.get("cliSessionId") or "").strip()
            desktop_session_id = str(payload.get("sessionId") or path.stem).strip()
            if not SESSION_ID_RE.fullmatch(cli_session_id) or not SESSION_ID_RE.fullmatch(desktop_session_id):
                continue
            title = str(payload.get("title") or "").strip()
            title_source = str(payload.get("titleSource") or "").strip().lower()
            try:
                last_activity = int(payload.get("lastActivityAt") or 0)
            except (TypeError, ValueError):
                last_activity = 0
            # Preserve original titles across profiles. For otherwise equal
            # records, prefer the profile with the most recent activity.
            rank = (
                int(bool(title)),
                int(title_source == "user"),
                profile_priority,
                last_activity,
            )
            metadata = {
                "desktop_session_id": desktop_session_id,
                "desktop_title": title,
                "desktop_profile": profile,
            }
            current = ranked.get(cli_session_id)
            if current is None or rank > current[0]:
                ranked[cli_session_id] = (rank, metadata)
    return {cli_id: value[1] for cli_id, value in ranked.items()}


def kimi_binary() -> Path:
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    executable_names = ("kimi.exe", "kimi") if IS_WINDOWS else ("kimi",)
    candidates = [
        directory / executable
        for directory in (kimi_home / "bin", HOME_DIR / ".local" / "bin")
        for executable in executable_names
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    discovered = shutil.which("kimi")
    if discovered:
        return Path(discovered)
    raise FileNotFoundError("Kimi Code unavailable")


def find_kimi_web_origin() -> str | None:
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    instances_dir = kimi_home / "server" / "instances"
    try:
        instance_files = sorted(
            instances_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for instance_file in instance_files:
        try:
            payload = json.loads(instance_file.read_text(encoding="utf-8"))
            host = str(payload.get("host") or "").strip().lower()
            port = int(payload.get("port") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if host not in LOOPBACK_HOSTS or not 1 <= port <= 65535:
            continue
        try:
            with socket.create_connection((host, port), timeout=0.25):
                display_host = f"[{host}]" if ":" in host else host
                return f"http://{display_host}:{port}"
        except OSError:
            continue
    return None


def ensure_kimi_web_origin() -> str:
    origin = find_kimi_web_origin()
    if origin:
        return origin

    subprocess.Popen(
        [str(kimi_binary()), "web", "--no-open"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(50):
        time.sleep(0.1)
        origin = find_kimi_web_origin()
        if origin:
            return origin
    raise FileNotFoundError("Kimi Web UI did not start")


def kimi_cli_launcher(session_id: str) -> Path:
    binary = kimi_binary()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        launcher = DATA_DIR / "open-kimi-session.cmd"
        escaped_binary = str(binary).replace('"', '""')
        escaped_session = session_id.replace('"', '""')
        command = (
            "@echo off\n"
            f'"{escaped_binary}" --session "{escaped_session}"\n'
        )
    else:
        launcher = DATA_DIR / "open-kimi-session.command"
        command = (
            "#!/bin/zsh\n"
            f"exec {shlex.quote(str(binary))} --session {shlex.quote(session_id)}\n"
        )
    launcher.write_text(command, encoding="utf-8")
    if not IS_WINDOWS:
        launcher.chmod(0o700)
    return launcher


def build_open_command(record: dict[str, Any], action: str) -> tuple[list[str], str]:
    if action == "workspace":
        raw_path = str(record.get("cwd") or "").strip()
        if not raw_path:
            raise ValueError("workspace unavailable")
        workspace = Path(raw_path).expanduser()
        if workspace.is_file():
            workspace = workspace.parent
        if not workspace.is_dir():
            raise FileNotFoundError("workspace unavailable")
        return system_open_command(str(workspace)), "workspace"

    source = str(record.get("source") or "").lower()
    session_id = str(record.get("session_id") or "").strip()
    if source in {"codex", "claude", "kimi", "kimi-desktop"} and not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("invalid session id")
    if source in {"codex", "claude", "kimi", "kimi-desktop"}:
        session_path = Path(str(record.get("session_path") or "")).expanduser()
        if not session_path.is_file():
            raise FileNotFoundError("session transcript unavailable; refresh the index")
    encoded_id = quote(session_id, safe="")

    if action == "cli":
        if source != "kimi":
            raise ValueError("CLI open is only available for Kimi Code sessions")
        return system_open_command(str(kimi_cli_launcher(session_id))), "kimi-cli"
    if action != "session":
        raise ValueError("unsupported action")

    if source == "codex":
        return system_open_command(f"codex://threads/{encoded_id}"), "codex"
    if source == "claude":
        # The active profile may have changed since the index was built.
        # Resolve it at click time so CC Switch and the official profile do not
        # leave stale Desktop IDs in the page.
        active_metadata = load_claude_desktop_session_map().get(session_id, {})
        desktop_session_id = str(active_metadata.get("desktop_session_id") or "").strip()
        if SESSION_ID_RE.fullmatch(desktop_session_id):
            encoded_desktop_id = quote(desktop_session_id, safe="")
            return system_open_command(
                f"claude://claude.ai/claude-code-desktop/{encoded_desktop_id}"
            ), "claude-desktop"
        return system_open_command(f"claude://resume?session={encoded_id}"), "claude"
    if source == "kimi":
        origin = find_kimi_web_origin()
        if not origin:
            raise FileNotFoundError("Kimi Web UI unavailable")
        return system_open_command(f"{origin}/sessions/{encoded_id}"), "kimi-web"
    if source == "kimi-desktop":
        return system_open_command("kimi-work://home"), "kimi-desktop"

    location = str(record.get("session_path") or record.get("cwd") or "").strip()
    parsed = urlparse(location)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return system_open_command(location), "web"
    local_target = Path(location).expanduser()
    if local_target.exists():
        return system_open_command(str(local_target)), "local"
    raise FileNotFoundError("session target unavailable")


def launch_record(record: dict[str, Any], action: str) -> str:
    try:
        if action == "session" and str(record.get("source") or "").lower() == "kimi":
            ensure_kimi_web_origin()
        command, mode = build_open_command(record, action)
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            raise OSError(detail or f"system opener exited with {completed.returncode}")
        record_open_event(record, action, outcome="opened", mode=mode)
        return mode
    except Exception as exc:
        record_open_event(
            record,
            action,
            outcome="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def load_config() -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            custom = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            config.update({k: v for k, v in custom.items() if k != "sources"})
            config["sources"].update(custom.get("sources", {}))
        except (OSError, json.JSONDecodeError):
            pass
    return config


def read_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = item.get("type", "")
                if kind in {"text", "input_text", "output_text"}:
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
        return "\n".join(parts)
    if isinstance(content, dict):
        value = content.get("text") or content.get("content")
        return value if isinstance(value, str) else ""
    return ""


def clean_user_text(text: str) -> str:
    if not text:
        return ""
    block_names = (
        "recommended_plugins",
        "environment_context",
        "INSTRUCTIONS",
        "permissions instructions",
        "app-context",
    )
    # Claude Desktop may append an entire skill definition to a user prompt.
    # It is runtime context rather than a user-authored request.
    cleaned = INJECTED_SKILL_PAYLOAD_RE.sub("", text)
    # Desktop AI clients also inject private attachment-cache paths. Keep the
    # meaningful filename searchable, while dropping directories and UUIDs.
    cleaned = INTERNAL_STORAGE_FILE_RE.sub(
        lambda match: Path(match.group(0)).name,
        cleaned,
    )
    cleaned = INTERNAL_STORAGE_PATH_RE.sub(" ", cleaned)
    for name in block_names:
        cleaned = re.sub(
            rf"<{re.escape(name)}(?:\s[^>]*)?>[\s\S]*?</{re.escape(name)}>",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"# AGENTS\.md instructions", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]{1,120}>", " ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def title_from_prompts(prompts: list[str], fallback: str) -> str:
    for prompt in prompts:
        cleaned = clean_user_text(prompt)
        if len(cleaned) < 4:
            continue
        sentence = re.split(r"(?<=[。！？.!?])\s+|\n", cleaned, maxsplit=1)[0]
        return (sentence or cleaned)[:180].strip()
    return fallback


def iso_from_value(value: Any, fallback: float | None = None) -> str:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            pass
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    seconds = fallback if fallback is not None else time.time()
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def canonical_project(cwd: str, title: str) -> tuple[str, str]:
    normalized = cwd.rstrip("/")
    parts = [
        part
        for part in Path(normalized).parts
        if part not in {"/", "Users", "home", HOME_DIR.name}
    ]
    customer = ""

    if "projects" in parts:
        index = len(parts) - 1 - parts[::-1].index("projects")
        if index + 1 < len(parts):
            return parts[index + 1], customer

    for marker in ("1.1 客户", "客户"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                customer = parts[index + 1]
                tail = parts[index + 2 : index + 4]
                return " · ".join([customer, *tail]) if tail else customer, customer

    if "Codex" in parts:
        index = parts.index("Codex")
        tail = parts[index + 1 :]
        if tail and DATE_PART_RE.match(tail[0]):
            tail = tail[1:]
        if tail and tail[-1].lower() not in GENERIC_DIRS:
            return tail[-1], customer

    basename = Path(normalized).name if normalized else ""
    if basename and basename.lower() not in GENERIC_DIRS:
        return basename, customer

    hint = clean_user_text(title)
    words = re.findall(r"[A-Za-z][A-Za-z0-9._-]{2,}|[\u4e00-\u9fff]{2,12}", hint)
    return " ".join(words[:4])[:64] or "未归类项目", customer


def find_artifacts(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in PATH_RE.findall(text):
        path = match.rstrip(".,;:，。；：)]}、")
        if path not in seen:
            seen.add(path)
            found.append(path)
        if len(found) >= 10:
            break
    return found


def make_record(
    *,
    source: str,
    session_id: str,
    session_path: str,
    cwd: str,
    title: str,
    prompts: list[str],
    created_at: str,
    updated_at: str,
    message_count: int,
    origin: str,
) -> dict[str, Any]:
    clean_prompts = [clean_user_text(item) for item in prompts if clean_user_text(item)]
    combined = "\n".join(clean_prompts)
    project, customer = canonical_project(cwd, title)
    artifacts = find_artifacts(combined)
    excerpt = combined[:9000]
    return {
        "id": f"{source}:{session_id}",
        "source": source,
        "source_label": {
            "codex": "Codex",
            "claude": "Claude",
            "kimi": "Kimi Code",
            "kimi-desktop": "Kimi Desktop",
        }.get(source, source),
        "session_id": session_id,
        "session_path": session_path,
        "cwd": cwd,
        "project": project,
        "customer": customer,
        "title": clean_user_text(title)[:220] or project,
        "excerpt": excerpt,
        "artifacts": artifacts,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": message_count,
        "origin": origin,
        "search_text": " ".join([title, project, customer, cwd, excerpt, *artifacts]).lower(),
    }


def is_user_initiated_codex_session(meta: dict[str, Any]) -> bool:
    thread_source = str(meta.get("thread_source") or "").strip().lower()
    if thread_source in NON_USER_CODEX_THREAD_SOURCES:
        return False
    source = meta.get("source")
    if isinstance(source, dict) and "subagent" in source:
        return False
    return True


def parse_codex(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}
    prompts: list[str] = []
    total_chars = 0
    created = ""
    updated = ""
    message_count = 0
    for row in read_json_lines(path):
        timestamp = row.get("timestamp")
        if timestamp:
            created = created or str(timestamp)
            updated = str(timestamp)
        if row.get("type") == "session_meta":
            meta = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            if meta and not is_user_initiated_codex_session(meta):
                return None
        if row.get("type") == "response_item":
            payload = row.get("payload", {})
            if isinstance(payload, dict) and payload.get("role") == "user":
                text = text_from_content(payload.get("content"))
                if text and total_chars < max_prompt_chars:
                    prompts.append(text[: max_prompt_chars - total_chars])
                    total_chars += len(text)
                    message_count += 1
    if not meta and not prompts:
        return None
    if meta and not is_user_initiated_codex_session(meta):
        return None
    stat = path.stat()
    session_id = str(meta.get("id") or meta.get("session_id") or path.stem)
    cwd = str(meta.get("cwd") or "")
    title = title_from_prompts(prompts, Path(cwd).name or "Codex 会话")
    return make_record(
        source="codex",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(created or meta.get("timestamp"), stat.st_ctime),
        updated_at=iso_from_value(updated, stat.st_mtime),
        message_count=message_count,
        origin=str(meta.get("source") or meta.get("originator") or "Codex session"),
    )


def parse_claude(
    path: Path,
    max_prompt_chars: int,
    desktop_sessions: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    prompts: list[str] = []
    total_chars = 0
    created = ""
    updated = ""
    cwd = ""
    session_id = path.stem
    version = ""
    custom_title = ""
    message_count = 0
    for row in read_json_lines(path):
        cwd = str(row.get("cwd") or cwd)
        session_id = str(row.get("sessionId") or session_id)
        version = str(row.get("version") or version)
        timestamp = row.get("timestamp")
        if timestamp:
            created = created or str(timestamp)
            updated = str(timestamp)
        if row.get("type") == "user":
            message = row.get("message", {})
            text = text_from_content(message.get("content") if isinstance(message, dict) else message)
            if text and total_chars < max_prompt_chars:
                prompts.append(text[: max_prompt_chars - total_chars])
                total_chars += len(text)
                message_count += 1
        if row.get("type") == "custom-title":
            candidate_title = str(row.get("customTitle") or "").strip()
            if candidate_title:
                custom_title = candidate_title
    if not prompts and not cwd:
        return None
    stat = path.stat()
    desktop_metadata = (desktop_sessions or {}).get(session_id, {})
    desktop_title = str(desktop_metadata.get("desktop_title") or "").strip()
    title = custom_title or desktop_title or title_from_prompts(prompts, Path(cwd).name or "Claude 会话")
    record = make_record(
        source="claude",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(created, stat.st_ctime),
        updated_at=iso_from_value(updated, stat.st_mtime),
        message_count=message_count,
        origin=f"Claude Code {version}".strip(),
    )
    desktop_session_id = str(desktop_metadata.get("desktop_session_id") or "").strip()
    if SESSION_ID_RE.fullmatch(desktop_session_id):
        record["desktop_session_id"] = desktop_session_id
        record["desktop_profile"] = str(desktop_metadata.get("desktop_profile") or "")
    record["title_source"] = "custom-title" if custom_title else "desktop" if desktop_title else "first-prompt"
    return record


def parse_kimi(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    title = str(state.get("title") or state.get("lastPrompt") or "Kimi 会话")
    last_prompt = str(state.get("lastPrompt") or "")
    prompts = [title]
    if last_prompt and last_prompt != title:
        prompts.append(last_prompt)
    prompts = [item[:max_prompt_chars] for item in prompts]
    cwd = str(state.get("cwd") or state.get("workDir") or "")
    session_id = str(state.get("id") or path.parent.name)
    stat = path.stat()
    return make_record(
        source="kimi",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(state.get("createdAt"), stat.st_ctime),
        updated_at=iso_from_value(state.get("updatedAt"), stat.st_mtime),
        message_count=max(1, len(prompts)),
        origin="Kimi Code",
    )


def parse_kimi_desktop(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    session_folder = path.parent.name
    if not session_folder.startswith(("conv-", "session_")):
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    title = str(state.get("title") or state.get("lastPrompt") or "Kimi Desktop session")
    last_prompt = str(state.get("lastPrompt") or "")
    prompts = [title]
    if last_prompt and last_prompt != title:
        prompts.append(last_prompt)
    prompts = [item[:max_prompt_chars] for item in prompts]
    cwd = str(state.get("cwd") or state.get("workDir") or "")
    session_id = str(state.get("id") or session_folder)
    stat = path.stat()
    record = make_record(
        source="kimi-desktop",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(state.get("createdAt"), stat.st_ctime),
        updated_at=iso_from_value(state.get("updatedAt"), stat.st_mtime),
        message_count=max(1, len(prompts)),
        origin="Kimi Desktop / Work",
    )
    record["open_label"] = "Open Kimi Desktop ↗"
    return record


def load_manual() -> list[dict[str, Any]]:
    if not MANUAL_FILE.exists():
        return []
    try:
        rows = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def build_index() -> dict[str, Any]:
    if DEMO_MODE:
        return load_demo_payload()
    config = load_config()
    max_chars = int(config.get("max_prompt_chars", 9000))
    source_paths = resolved_source_paths(config)
    claude_desktop_sessions = load_claude_desktop_session_map(all_profiles=True)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    notices: list[str] = []
    source_status: dict[str, dict[str, Any]] = {}

    adapters = {
        "codex": ("**/*.jsonl", parse_codex),
        "claude": (
            "**/*.jsonl",
            lambda path, limit: parse_claude(path, limit, claude_desktop_sessions),
        ),
        "kimi": ("**/state.json", parse_kimi),
        "kimi-desktop": ("**/state.json", parse_kimi_desktop),
    }
    for source, (pattern, parser) in adapters.items():
        roots = [root for root in source_paths.get(source, []) if root.exists()]
        source_status[source] = {
            "available": bool(roots),
            "paths": [str(root) for root in roots],
        }
        if not roots:
            notices.append(f"{source}: not detected on this computer")
            continue
        for root in roots:
            for path in root.glob(pattern):
                if source == "claude" and "subagents" in path.parts:
                    continue
                try:
                    record = parser(path, max_chars)
                    if record:
                        records.append(record)
                except Exception as exc:  # one damaged session must not block the index
                    errors.append(f"{source}: {path.name}: {type(exc).__name__}")

    manual_rows = load_manual()
    for row in manual_rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "other").lower()
        when = iso_from_value(row.get("updated_at"))
        title = str(row.get("title") or "手工记录")
        cwd = str(row.get("location") or "")
        record = make_record(
            source=source,
            session_id=str(row.get("id") or uuid.uuid4()),
            session_path=cwd,
            cwd=cwd,
            title=title,
            prompts=[str(row.get("notes") or "")],
            created_at=when,
            updated_at=when,
            message_count=1,
            origin="手工补录",
        )
        if row.get("project"):
            record["project"] = str(row["project"])
            record["search_text"] += " " + str(row["project"]).lower()
        records.append(record)

    deduplicated: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id") or "")
        existing = deduplicated.get(record_id)
        if existing is None or (
            bool(Path(str(record.get("session_path") or "")).exists()),
            int(record.get("message_count") or 0),
            str(record.get("updated_at") or ""),
        ) > (
            bool(Path(str(existing.get("session_path") or "")).exists()),
            int(existing.get("message_count") or 0),
            str(existing.get("updated_at") or ""),
        ):
            deduplicated[record_id] = record
    records = sorted(
        deduplicated.values(),
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )
    counts = Counter(item["source"] for item in records)
    projects = {item["project"] for item in records if item.get("project")}
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "records": records,
        "summary": {
            "records": len(records),
            "projects": len(projects),
            "sources": dict(counts),
            "errors": len(errors),
            "available_sources": sum(bool(item["available"]) for item in source_status.values()),
        },
        "source_status": source_status,
        "warnings": [*errors, *notices][:30],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(INDEX_FILE)
    return payload


class FinderHandler(SimpleHTTPRequestHandler):
    server_version = "AIProjectFinder/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/index":
            self.send_json(load_index_payload())
            return
        if parsed.path == "/api/health":
            self.send_json({
                "ok": True,
                "demo": DEMO_MODE,
                "index_exists": True if DEMO_MODE else INDEX_FILE.exists(),
            })
            return
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            if "lang" not in query:
                separator = "&" if parsed.query else ""
                self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
                self.send_header(
                    "Location",
                    f"/?{parsed.query}{separator}lang={quote(APP_LOCALE, safe='-')}",
                )
                self.end_headers()
                return
            self.path = "/index.html"
        super().do_GET()

    def read_body_json(self) -> dict[str, Any]:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 100_000)
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/reindex":
            try:
                payload = load_demo_payload() if DEMO_MODE else build_index()
                self.send_json({
                    "ok": True,
                    "demo": DEMO_MODE,
                    "summary": payload["summary"],
                    "generated_at": payload["generated_at"],
                })
            except Exception as exc:
                self.send_json({"ok": False, "error": type(exc).__name__}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/open":
            request = self.read_body_json()
            record = find_indexed_record(str(request.get("record_id") or ""))
            action = str(request.get("action") or "session")
            if record is None:
                self.send_json({"ok": False, "error": "record not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                mode = demo_open_mode(record, action) if DEMO_MODE else launch_record(record, action)
                self.send_json({"ok": True, "demo": DEMO_MODE, "mode": mode})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc), "error_code": "target_unavailable"},
                    HTTPStatus.NOT_FOUND,
                )
            except subprocess.TimeoutExpired:
                self.send_json(
                    {"ok": False, "error": "system opener timed out", "error_code": "open_timeout"},
                    HTTPStatus.GATEWAY_TIMEOUT,
                )
            except OSError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc) or type(exc).__name__, "error_code": "client_open_failed"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        if parsed.path == "/api/manual":
            if DEMO_MODE:
                self.send_json(
                    {
                        "ok": False,
                        "error": "demo mode is read only",
                        "error_code": "demo_read_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
                return
            row = self.read_body_json()
            if not str(row.get("title") or "").strip():
                self.send_json({"ok": False, "error": "title is required"}, HTTPStatus.BAD_REQUEST)
                return
            manual = load_manual()
            row["id"] = str(uuid.uuid4())
            row["updated_at"] = row.get("updated_at") or datetime.now(tz=timezone.utc).isoformat()
            manual.append(row)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            MANUAL_FILE.write_text(json.dumps(manual, ensure_ascii=False, indent=2), encoding="utf-8")
            payload = build_index()
            self.send_json({"ok": True, "summary": payload["summary"]}, HTTPStatus.CREATED)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    global APP_LOCALE, DEMO_MODE
    parser = argparse.ArgumentParser(description="AI Project Finder local dashboard")
    parser.add_argument("--build-only", action="store_true", help="refresh index and exit")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    parser.add_argument(
        "--locale",
        choices=("en", "zh-CN"),
        default=None,
        help="interface language; overrides config.json and AI_PROJECT_FINDER_LOCALE",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run with synthetic demo data on port 4390; no local sources are read",
    )
    args = parser.parse_args()
    DEMO_MODE = bool(args.demo)
    locale_override = args.locale or os.environ.get("AI_PROJECT_FINDER_LOCALE")
    config = {} if DEMO_MODE else load_config()
    APP_LOCALE = normalize_locale(
        locale_override or ("en" if DEMO_MODE else config.get("locale", "en"))
    )
    is_chinese = APP_LOCALE == "zh-CN"

    if args.build_only:
        payload = load_demo_payload() if DEMO_MODE else build_index()
        if is_chinese:
            print(
                f"已索引 {payload['summary']['records']} 个会话，"
                f"包含 {payload['summary']['projects']} 个项目线索。"
            )
        else:
            print(
                f"Indexed {payload['summary']['records']} sessions across "
                f"{payload['summary']['projects']} project clues."
            )
        return

    if DEMO_MODE:
        payload = load_demo_payload()
        if is_chinese:
            print(
                f"已载入 {payload['summary']['records']} 个合成会话，"
                f"包含 {payload['summary']['projects']} 个演示项目。"
            )
        else:
            print(
                f"Loaded {payload['summary']['records']} synthetic sessions across "
                f"{payload['summary']['projects']} demo projects."
            )
    elif not INDEX_FILE.exists():
        payload = build_index()
        if is_chinese:
            print(
                f"已索引 {payload['summary']['records']} 个会话，"
                f"包含 {payload['summary']['projects']} 个项目线索。"
            )
        else:
            print(
                f"Indexed {payload['summary']['records']} sessions across "
                f"{payload['summary']['projects']} project clues."
            )
    else:
        threading.Thread(target=build_index, daemon=True, name="index-refresh").start()

    port = args.port or (DEMO_DEFAULT_PORT if DEMO_MODE else int(config.get("port", 4388)))
    url = f"http://127.0.0.1:{port}/?lang={quote(APP_LOCALE, safe='-')}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), FinderHandler)
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 48, 98}:
            if is_chinese:
                print(f"AI Project Finder 已经在此地址运行：{url}")
            else:
                print(f"AI Project Finder is already available at {url}")
            if args.open:
                webbrowser.open(url)
            return
        raise
    if is_chinese:
        print(f"AI Project Finder 已在此地址运行：{url}")
    else:
        print(f"AI Project Finder is available at {url}")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
