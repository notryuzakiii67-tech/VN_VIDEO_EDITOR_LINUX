"""Project persistence: ``.vnp`` bundles, autosave, crash recovery, recents."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import MediaAsset, Project

PROJECT_EXT = ".vnp"
PROJECT_MAGIC = "vn-editor-project"
FORMAT_VERSION = 1

APP_NAME = "vn-studio"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def recovery_dir() -> Path:
    d = data_dir() / "recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumb_dir() -> Path:
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ProjectIOError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Save / load
# --------------------------------------------------------------------------- #

def save(project: Project, path: str, thumbnail: Optional[bytes] = None,
         embed_media: bool = False) -> str:
    """Write a project bundle.  Returns the path actually written."""
    path = str(path)
    if not path.lower().endswith(PROJECT_EXT):
        path += PROJECT_EXT
    project.modified = time.time()
    payload = project.to_dict()
    payload["format"] = PROJECT_MAGIC
    payload["version"] = FORMAT_VERSION

    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(Path(path).parent or "."))
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(payload, indent=1))
            z.writestr("about.txt",
                       f"{APP_NAME} project bundle\n"
                       f"name: {project.name}\n"
                       f"saved: {time.ctime()}\n")
            if thumbnail:
                z.writestr("thumbnail.png", thumbnail)
            if embed_media:
                for asset in project.assets:
                    try:
                        if asset.path and os.path.exists(asset.path):
                            z.write(asset.path, f"media/{os.path.basename(asset.path)}")
                    except OSError:
                        pass
        os.replace(tmp_name, path)
    except Exception as exc:  # pragma: no cover - disk issues
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ProjectIOError(f"Could not save project: {exc}") from exc
    return path


def save_plain_json(project: Project, path: str) -> str:
    payload = project.to_dict()
    payload["format"] = PROJECT_MAGIC
    payload["version"] = FORMAT_VERSION
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return path


def load(path: str) -> Project:
    path = str(path)
    if not os.path.exists(path):
        raise ProjectIOError(f"Project not found: {path}")
    data: Optional[Dict[str, Any]] = None
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as z:
                if "project.json" not in z.namelist():
                    raise ProjectIOError("Bundle does not contain project.json")
                data = json.loads(z.read("project.json").decode("utf-8"))
        except zipfile.BadZipFile as exc:
            raise ProjectIOError(f"Damaged project bundle: {exc}") from exc
    else:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectIOError(f"Not a valid project file: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectIOError("Malformed project data")
    if data.get("format") not in (None, PROJECT_MAGIC):
        raise ProjectIOError("Unrecognised project format")
    project = Project.from_dict(data)
    project.editor_state = dict(project.editor_state or {})
    project.editor_state["file_path"] = path
    _reconcile_assets(project)
    return project


def _reconcile_assets(project: Project) -> None:
    """Detect moved/missing media and try to relink by filename nearby."""
    known_paths = {a.path for a in project.assets if a.path}
    for asset in project.assets:
        asset.missing = not (asset.path and os.path.exists(asset.path))
    for clip in project.all_clips():
        if clip.kind in ("text", "sticker", "shape", "adjustment"):
            continue
        if clip.path and os.path.exists(clip.path):
            continue
        asset = project.asset(clip.asset_id)
        if asset and asset.path and os.path.exists(asset.path):
            clip.path = asset.path
        else:
            clip.meta["missing"] = True


def relink(project: Project, asset_id: str, new_path: str) -> int:
    """Point an asset (and every clip using it) at a new file."""
    asset = project.asset(asset_id)
    if asset is None:
        return 0
    old = asset.path
    asset.path = new_path
    asset.missing = not os.path.exists(new_path)
    count = 0
    for clip in project.all_clips():
        if clip.asset_id == asset_id or (old and clip.path == old):
            clip.path = new_path
            clip.meta.pop("missing", None)
            count += 1
    return count


# --------------------------------------------------------------------------- #
#  Autosave + crash recovery
# --------------------------------------------------------------------------- #

class AutoSave:
    """Keeps a rolling on-disk copy so a crash never loses the edit."""

    def __init__(self, interval: float = 20.0, keep: int = 6) -> None:
        self.interval = interval
        self.keep = keep
        self._last = 0.0
        self._dirty = False
        self._path: Optional[str] = None

    def mark_dirty(self) -> None:
        self._dirty = True

    def due(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self._dirty and (now - self._last) >= self.interval

    def set_path(self, path: Optional[str]) -> None:
        self._path = path
        self.slot_file(path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def slot_file(project_path: Optional[str]) -> Path:
        if project_path:
            import hashlib
            key = hashlib.sha1(str(project_path).encode("utf-8")).hexdigest()[:16]
        else:
            key = "untitled"
        return recovery_dir() / f"{key}.vnp"

    def write(self, project: Project, thumb: Optional[bytes] = None) -> Optional[Path]:
        try:
            target = self.slot_file(self._path)
            save(project, str(target), thumbnail=thumb)
            self._write_meta(project)
            self._last = time.time()
            self._dirty = False
            self._prune()
            return target
        except Exception:
            return None

    def _write_meta(self, project: Project) -> None:
        meta = recovery_dir() / "recovery.json"
        entries: List[Dict[str, Any]] = []
        if meta.exists():
            try:
                entries = json.loads(meta.read_text("utf-8"))
            except Exception:
                entries = []
        entry = {
            "slot": self.slot_file(self._path).name,
            "name": project.name,
            "project_path": self._path or "",
            "time": time.time(),
        }
        entries = [e for e in entries if e.get("slot") != entry["slot"]]
        entries.insert(0, entry)
        try:
            meta.write_text(json.dumps(entries[:40], indent=1), "utf-8")
        except OSError:
            pass

    def _prune(self) -> None:
        try:
            files = sorted(recovery_dir().glob("*.vnp"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files[self.keep:]:
                f.unlink()
        except OSError:
            pass


def recovery_entries() -> List[Dict[str, Any]]:
    meta = recovery_dir() / "recovery.json"
    if not meta.exists():
        return []
    try:
        entries = json.loads(meta.read_text("utf-8"))
    except Exception:
        return []
    out = []
    for e in entries:
        slot = recovery_dir() / str(e.get("slot", ""))
        if slot.exists():
            e = dict(e)
            e["slot_path"] = str(slot)
            e["mtime"] = slot.stat().st_mtime
            out.append(e)
    return out


def clear_recovery(slot_path: str) -> None:
    try:
        p = Path(slot_path)
        if p.exists():
            p.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  Recent files
# --------------------------------------------------------------------------- #

class RecentFiles:
    def __init__(self, limit: int = 12) -> None:
        self.path = config_dir() / "recent.json"
        self.limit = limit
        self.items: List[str] = self._read()

    def _read(self) -> List[str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data][: self.limit]
        except Exception:
            pass
        return []

    def _write(self) -> None:
        try:
            self.path.write_text(json.dumps(self.items[: self.limit], indent=1), "utf-8")
        except OSError:
            pass

    def add(self, path: str) -> None:
        path = str(path)
        self.items = [p for p in self.items if p != path]
        self.items.insert(0, path)
        del self.items[self.limit:]
        self._write()

    def remove(self, path: str) -> None:
        self.items = [p for p in self.items if p != str(path)]
        self._write()

    def existing(self) -> List[str]:
        return [p for p in self.items if os.path.exists(p)]


# --------------------------------------------------------------------------- #
#  Settings (app preferences)
# --------------------------------------------------------------------------- #

DEFAULT_SETTINGS: Dict[str, Any] = {
    "preview_scale": 0.5,
    "preview_fps": 30,
    "proxy_enabled": False,
    "proxy_height": 540,
    "autosave_minutes": 0.33,
    "timeline_zoom": 60.0,
    "snapping": True,
    "snap_threshold": 8,
    "default_duration_image": 4.0,
    "default_transition": "fade",
    "default_transition_duration": 0.5,
    "export_preset": "1080p",
    "export_codec": "libx264",
    "export_crf": 20,
    "export_preset_speed": "medium",
    "export_audio_bitrate": "192k",
    "hardware_decode": True,
    "theme_accent": "#ff3b6b",
    "last_import_dir": "",
    "last_export_dir": "",
    "show_waveforms": True,
    "ripple_delete": False,
    "auto_play_on_scrub": False,
    "autosave": True,
    "recovery": True,
    "preview_quality": "auto",
    "auto_keyframe": True,
}


class Settings:
    def __init__(self) -> None:
        self.path = config_dir() / "settings.json"
        self.data: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict):
                self.data.update(raw)
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, indent=1), "utf-8")
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)


SETTINGS = Settings()


# --------------------------------------------------------------------------- #
#  Module-level convenience wrappers used by the UI
# --------------------------------------------------------------------------- #

RECENT = RecentFiles()
AUTOSAVE = AutoSave()


def projects_dir() -> Path:
    """Default home for user projects (``~/.local/share/vn-studio/projects``)."""
    d = data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> Dict[str, Any]:
    """Current preferences as a plain dict."""
    return dict(SETTINGS.data)


def update_settings(**kwargs: Any) -> None:
    """Persist one or more preferences."""
    for key, value in kwargs.items():
        SETTINGS.set(key, value)


def get_setting(key: str, default: Any = None) -> Any:
    return SETTINGS.get(key, default)


def add_recent(path: str) -> None:
    if path:
        RECENT.add(str(path))


def recent_projects(limit: int = 12) -> List[str]:
    return RECENT.existing()[:limit]


def clear_recent() -> None:
    RECENT.items = []
    RECENT._write()


def autosave(project: Project, path: Optional[str] = None,
             force: bool = False) -> Optional[Path]:
    """Write a recovery snapshot for *project*.

    Called on a timer by the editor; returns the snapshot path or ``None``.
    """
    if not force and not SETTINGS.get("recovery", True):
        return None
    AUTOSAVE.set_path(path)
    return AUTOSAVE.write(project)


def prune_recovery(keep: int = 6) -> None:
    AUTOSAVE.keep = keep
    AUTOSAVE._prune()


def recent_media(limit: int = 20) -> List[str]:
    """Recently imported media files (stored in the preferences file)."""
    items = list(SETTINGS.get("recent_media", []) or [])
    return [p for p in items if os.path.exists(p)][:limit]


def add_recent_media(path: str, limit: int = 40) -> None:
    items = [p for p in (SETTINGS.get("recent_media", []) or []) if p != str(path)]
    items.insert(0, str(path))
    update_settings(recent_media=items[:limit])
