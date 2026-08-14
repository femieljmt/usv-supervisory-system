"""Route helpers for the dependency-free dashboard HTTP server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dashboard.database_reader import DashboardDataReader


SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


class DashboardRoutes:
    def __init__(
        self,
        *,
        project_root: Path,
        reader: DashboardDataReader,
        default_track_limit: int,
        refresh_seconds: float = 2.0,
    ) -> None:
        self._project_root = project_root
        self._reader = reader
        self._default_track_limit = default_track_limit
        self._refresh_seconds = max(1.0, float(refresh_seconds))
        self._template_path = project_root / "dashboard" / "templates" / "index.html"
        self._static_root = project_root / "dashboard" / "static"

    def dispatch(self, raw_path: str) -> tuple[int, str, bytes]:
        parsed = urlparse(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            if not self._template_path.exists():
                return 404, "text/plain; charset=utf-8", b"Not found"
            html = self._template_path.read_text(encoding="utf-8").replace(
                "__DASHBOARD_REFRESH_SECONDS__",
                str(self._refresh_seconds),
            )
            return 200, "text/html; charset=utf-8", html.encode("utf-8")

        if path == "/api/health":
            return self._json_response({"status": "ok"})

        if path == "/api/vehicles":
            return self._json_response({"vehicles": self._reader.list_vehicles()})

        if path == "/api/sessions":
            vehicle_id = _single(query, "vehicle_id")
            if not vehicle_id:
                return self._json_response({"vehicle_id": None, "sessions": []})
            return self._json_response(
                {
                    "vehicle_id": vehicle_id,
                    "sessions": self._reader.list_sessions(vehicle_id),
                }
            )

        if path == "/api/dashboard":
            vehicle_id = _single(query, "vehicle_id")
            session_id = _single(query, "session_id")
            track_limit = _safe_int(
                _single(query, "track_limit"),
                default=self._default_track_limit,
                minimum=1,
                maximum=5000,
            )
            track_mode = (_single(query, "track_mode") or "main").lower()
            if track_mode not in {"main", "recent"}:
                track_mode = "main"
            return self._json_response(
                self._reader.snapshot(
                    vehicle_id=vehicle_id,
                    session_id=session_id,
                    track_limit=track_limit,
                    track_mode=track_mode,
                )
            )

        if path == "/api/track/playback":
            vehicle_id = _single(query, "vehicle_id")
            session_id = _single(query, "session_id")
            if not vehicle_id or not session_id:
                return self._json_response(
                    {"error": "vehicle_id dan session_id wajib diisi", "points": []}
                )
            limit = _safe_int(
                _single(query, "limit"),
                default=5000,
                minimum=1,
                maximum=10000,
            )
            return self._json_response(
                self._reader.playback_track(
                    vehicle_id=vehicle_id,
                    session_id=session_id,
                    limit=limit,
                )
            )

        if path == "/api/export/session.csv":
            vehicle_id = _single(query, "vehicle_id")
            session_id = _single(query, "session_id")
            if not vehicle_id or not session_id:
                return (
                    400,
                    "application/json; charset=utf-8",
                    json.dumps(
                        {"error": "vehicle_id dan session_id wajib diisi"},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
            body = self._reader.export_session_csv(
                vehicle_id=vehicle_id,
                session_id=session_id,
            )
            return 200, "text/csv; charset=utf-8", body

        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            candidate = (self._static_root / relative).resolve()
            static_root = self._static_root.resolve()
            if candidate != static_root and static_root not in candidate.parents:
                return 403, "text/plain; charset=utf-8", b"Forbidden"
            content_type = _content_type(candidate.suffix)
            return self._file_response(candidate, content_type)

        return 404, "application/json; charset=utf-8", json.dumps(
            {"error": "Not found"}
        ).encode("utf-8")

    def _json_response(self, value: Any) -> tuple[int, str, bytes]:
        return (
            200,
            "application/json; charset=utf-8",
            json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"),
        )

    def _file_response(
        self,
        path: Path,
        content_type: str,
    ) -> tuple[int, str, bytes]:
        if not path.exists() or not path.is_file():
            return 404, "text/plain; charset=utf-8", b"Not found"
        return 200, content_type, path.read_bytes()


def safe_export_filename(vehicle_id: str, session_id: str) -> str:
    vehicle = SAFE_FILENAME.sub("_", vehicle_id).strip("._") or "vehicle"
    session = SAFE_FILENAME.sub("_", session_id).strip("._") or "session"
    return f"telemetry_{vehicle}_{session}.csv"


def _single(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _safe_int(
    raw: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _content_type(suffix: str) -> str:
    return {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".ico": "image/x-icon",
    }.get(suffix.lower(), "application/octet-stream")
