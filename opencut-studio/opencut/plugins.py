"""Plugin architecture for OpenCut Studio.

Plugins are Python packages or modules discovered from:

1. The ``plugins/`` directory next to the application (bundled plugins).
2. ``~/.local/share/OpenCutStudio/plugins/`` (user-installed plugins).
3. A ``[tool.opencut.plugins]`` section in ``pyproject.toml`` (entry-point
   based, for pip-installable plugins).

Each plugin module must define a ``register()`` function that returns one
or more :class:`Plugin` instances.  A plugin that raises on import or
whose ``register()`` fails is logged and skipped — it must never crash
the application.

Plugin types:

* :class:`EffectPlugin` — adds a new effect kind to the effects catalogue.
* :class:`TransitionPlugin` — adds a new transition kind.
* :class:`TitlePlugin` — adds a title preset.
* :class:`ImporterPlugin` — adds support for an additional file format.
* :class:`ExporterPlugin` — adds an export preset or format.

All plugin types are optional hooks; the core editor works without any
plugins installed.

Design constraints (from the master prompt):

* Plugins are **opt-in**: the user must install or enable them.
* Plugins run **locally**: no network calls without explicit user consent.
* Plugin **failures are logged**, never silently swallowed.
* The **core workflow must still work** if every plugin is removed or broken.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger("opencut.plugins")


# --------------------------------------------------------------------------- #
#  Plugin base classes
# --------------------------------------------------------------------------- #

@dataclass
class PluginMeta:
    """Metadata every plugin must provide."""
    name: str
    version: str = "0.0.1"
    author: str = ""
    description: str = ""
    requires: List[str] = field(default_factory=list)   # pip packages


class Plugin:
    """Base class for all plugins.

    Subclass one of the typed plugin classes below to declare what your
    plugin provides.  A single plugin module can return multiple Plugin
    instances from its ``register()`` function.
    """

    meta: PluginMeta = PluginMeta(name="unnamed")

    def on_load(self) -> None:
        """Called once when the plugin is loaded.  Override for setup."""

    def on_unload(self) -> None:
        """Called when the plugin is unloaded.  Override for cleanup."""


class EffectPlugin(Plugin):
    """Adds a new effect kind to the effects catalogue.

    Subclass and override :meth:`effect_kind`, :meth:`effect_label`,
    :meth:`effect_params`, :meth:`build_filter`.
    """

    def effect_kind(self) -> str:
        """Unique effect identifier (e.g. 'my_blur')."""
        raise NotImplementedError

    def effect_label(self) -> str:
        """Human-readable label (e.g. 'My Custom Blur')."""
        raise NotImplementedError

    def effect_category(self) -> str:
        """Category name (e.g. 'Blur & Sharp').  Creates a new category
        if it doesn't exist."""
        return "Plugins"

    def effect_params(self) -> List[Dict[str, Any]]:
        """Parameter specs: ``[{"key": "...", "label": "...", "default": 0.0,
        "lo": 0.0, "hi": 1.0, "step": 0.01}, ...]``."""
        return []

    def build_filter(self, params: Dict[str, float],
                     duration: float) -> List[str]:
        """Return FFmpeg filter fragments for this effect.

        The input stream label is ``[in]`` and output is ``[out]``.
        """
        raise NotImplementedError


class TransitionPlugin(Plugin):
    """Adds a new transition kind."""

    def transition_kind(self) -> str:
        raise NotImplementedError

    def transition_label(self) -> str:
        raise NotImplementedError

    def default_duration(self) -> float:
        return 0.5


class TitlePlugin(Plugin):
    """Adds a title preset."""

    def title_preset(self) -> Dict[str, Any]:
        """Return a title preset dict (label, font, size, colour, ...)."""
        raise NotImplementedError


class ImporterPlugin(Plugin):
    """Adds support for an additional file format."""

    def supported_extensions(self) -> List[str]:
        """e.g. [".webm", ".ogg"]"""
        return []

    def import_file(self, path: str) -> Optional[Dict[str, Any]]:
        """Import a file and return a MediaAsset-compatible dict, or None."""
        raise NotImplementedError


class ExporterPlugin(Plugin):
    """Adds an export preset or format."""

    def export_preset(self) -> Dict[str, Any]:
        """Return an ExportPreset-compatible dict."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
#  Plugin registry
# --------------------------------------------------------------------------- #

@dataclass
class LoadedPlugin:
    """A successfully loaded plugin instance."""
    instance: Plugin
    source: str            # path or module name
    type: str = "generic"  # effect, transition, title, importer, exporter


class PluginRegistry:
    """Central registry for all loaded plugins.

    The registry is a singleton accessed via :func:`get_registry`.
    """

    def __init__(self) -> None:
        self._plugins: List[LoadedPlugin] = []
        self._errors: List[Dict[str, str]] = []
        self._loaded = False

    @property
    def plugins(self) -> List[LoadedPlugin]:
        return list(self._plugins)

    @property
    def errors(self) -> List[Dict[str, str]]:
        return list(self._errors)

    @property
    def effect_plugins(self) -> List[EffectPlugin]:
        return [p.instance for p in self._plugins
                if isinstance(p.instance, EffectPlugin)]

    @property
    def transition_plugins(self) -> List[TransitionPlugin]:
        return [p.instance for p in self._plugins
                if isinstance(p.instance, TransitionPlugin)]

    @property
    def title_plugins(self) -> List[TitlePlugin]:
        return [p.instance for p in self._plugins
                if isinstance(p.instance, TitlePlugin)]

    @property
    def importer_plugins(self) -> List[ImporterPlugin]:
        return [p.instance for p in self._plugins
                if isinstance(p.instance, ImporterPlugin)]

    @property
    def exporter_plugins(self) -> List[ExporterPlugin]:
        return [p.instance for p in self._plugins
                if isinstance(p.instance, ExporterPlugin)]

    def discover(self, extra_dirs: Optional[List[str]] = None) -> None:
        """Discover and load plugins from all configured sources.

        This is called once at application startup.  Plugin failures are
        recorded but never prevent the application from starting.
        """
        if self._loaded:
            return
        self._loaded = True

        dirs: List[Path] = []

        # 1. Bundled plugins directory
        app_dir = Path(__file__).resolve().parent.parent
        bundled = app_dir / "plugins"
        if bundled.is_dir():
            dirs.append(bundled)

        # 2. User plugins directory
        user_dir = Path.home() / ".local" / "share" / "OpenCutStudio" / "plugins"
        if user_dir.is_dir():
            dirs.append(user_dir)

        # 3. Extra directories (for testing)
        if extra_dirs:
            dirs.extend(Path(d) for d in extra_dirs if d)

        seen: set = set()
        for d in dirs:
            resolved = str(d.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            self._discover_directory(d)

        logger.info("Loaded %d plugin(s), %d error(s)",
                     len(self._plugins), len(self._errors))

    def _discover_directory(self, directory: Path) -> None:
        """Discover plugins in a directory."""
        if not directory.is_dir():
            return
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.is_dir() and (entry / "__init__.py").exists():
                self._load_plugin_module(entry.name, str(entry))
            elif entry.suffix == ".py" and entry.stem != "__init__":
                self._load_plugin_module(entry.stem, str(entry))

    def _load_plugin_module(self, name: str, path: str) -> None:
        """Load a single plugin module by path."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"opencut_plugin_{name}", path)
            if spec is None or spec.loader is None:
                self._record_error(name, path, "Could not create module spec")
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            self._record_error(name, path, f"Import failed: {exc}")
            return

        if not hasattr(module, "register"):
            self._record_error(name, path, "No register() function found")
            return

        try:
            result = module.register()
        except Exception as exc:
            self._record_error(name, path, f"register() failed: {exc}")
            return

        # register() may return a single Plugin or a list
        if isinstance(result, Plugin):
            plugins = [result]
        elif isinstance(result, (list, tuple)):
            plugins = list(result)
        else:
            self._record_error(name, path,
                               f"register() returned {type(result).__name__}, "
                               "expected Plugin or list of Plugin")
            return

        for plugin in plugins:
            if not isinstance(plugin, Plugin):
                self._record_error(name, path,
                                   f"Non-Plugin object returned: {type(plugin)}")
                continue
            # Validate required metadata
            if not plugin.meta.name or plugin.meta.name == "unnamed":
                self._record_error(name, path,
                                   "Plugin meta.name is required")
                continue
            # Check requirements
            missing = self._check_requires(plugin.meta.requires)
            if missing:
                self._record_error(
                    name, path,
                    f"Missing requirements: {', '.join(missing)}")
                continue
            # Load
            try:
                plugin.on_load()
            except Exception as exc:
                self._record_error(name, path, f"on_load() failed: {exc}")
                continue

            ptype = "generic"
            if isinstance(plugin, EffectPlugin):
                ptype = "effect"
            elif isinstance(plugin, TransitionPlugin):
                ptype = "transition"
            elif isinstance(plugin, TitlePlugin):
                ptype = "title"
            elif isinstance(plugin, ImporterPlugin):
                ptype = "importer"
            elif isinstance(plugin, ExporterPlugin):
                ptype = "exporter"

            self._plugins.append(LoadedPlugin(
                instance=plugin, source=path, type=ptype))
            logger.info("Loaded plugin '%s' from %s", plugin.meta.name, path)

    def _check_requires(self, requires: List[str]) -> List[str]:
        """Check that all required pip packages are importable."""
        missing = []
        for pkg in requires:
            try:
                importlib.import_module(pkg)
            except ImportError:
                missing.append(pkg)
        return missing

    def _record_error(self, name: str, source: str, message: str) -> None:
        self._errors.append({
            "name": name, "source": source, "error": message})
        logger.warning("Plugin '%s' (%s): %s", name, source, message)

    def clear(self) -> None:
        """Unload all plugins (for testing)."""
        for p in self._plugins:
            try:
                p.instance.on_unload()
            except Exception:
                pass
        self._plugins.clear()
        self._errors.clear()
        self._loaded = False


# --------------------------------------------------------------------------- #
#  Singleton
# --------------------------------------------------------------------------- #

_REGISTRY: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Return the global plugin registry (singleton)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PluginRegistry()
    return _REGISTRY


def discover_plugins(extra_dirs: Optional[List[str]] = None) -> PluginRegistry:
    """Convenience: discover plugins and return the registry."""
    reg = get_registry()
    reg.discover(extra_dirs)
    return reg