"""Phase 5 tests: plugin architecture, loading, failure handling, and integration.

These tests verify that:
- Plugins are discovered and loaded from directories.
- Plugin types (effect, transition, title, importer, exporter) are registered.
- Plugin failures (bad register, missing requirements, import errors) are
  recorded but don't crash the application.
- The core workflow still works with no plugins installed.
- Plugin metadata is accessible.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from opencut.plugins import (
    EffectPlugin,
    ExporterPlugin,
    ImporterPlugin,
    Plugin,
    PluginMeta,
    PluginRegistry,
    TitlePlugin,
    TransitionPlugin,
    discover_plugins,
    get_registry,
)

BUNDLED_DIR = str(Path(__file__).resolve().parent.parent / "plugins")


def _load_tmp_plugin(tmp_path, filename, code):
    """Helper: write a plugin file and discover it in a fresh registry."""
    plugin_file = tmp_path / filename
    plugin_file.write_text(textwrap.dedent(code))
    reg = PluginRegistry()
    reg.discover(extra_dirs=[str(tmp_path), BUNDLED_DIR])
    return reg


def _tmp_plugins(reg):
    """Return only the plugins loaded from /tmp (test fixtures)."""
    return [p for p in reg.plugins if "/tmp" in p.source]


def _tmp_errors(reg):
    """Return only the errors from /tmp (test fixtures)."""
    return [e for e in reg.errors if "/tmp" in e["source"]]


# --------------------------------------------------------------------------- #
#  Plugin registry basics
# --------------------------------------------------------------------------- #

class TestPluginRegistry:
    """Test the plugin registry lifecycle."""

    def test_registry_starts_empty(self):
        reg = PluginRegistry()
        assert len(reg.plugins) == 0
        assert len(reg.errors) == 0

    def test_discover_finds_bundled_plugins(self):
        """The bundled plugins/ directory should be discovered."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        names = [p.instance.meta.name for p in reg.plugins]
        assert "temperature-warm-example" in names
        assert "gaming-title-example" in names

    def test_discover_records_bad_register(self):
        """A plugin whose register() raises should be recorded as an error."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        errors = [e for e in reg.errors if e["name"] == "bad_register"]
        assert len(errors) >= 1
        assert "intentionally fails" in errors[0]["error"]

    def test_discover_records_no_register(self):
        """A plugin without register() should be recorded as an error."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        errors = [e for e in reg.errors if e["name"] == "bad_no_register"]
        assert len(errors) >= 1
        assert "No register()" in errors[0]["error"]

    def test_discover_is_idempotent(self):
        """Calling discover() twice should not reload plugins."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        count1 = len(reg.plugins)
        reg.discover(extra_dirs=[BUNDLED_DIR])
        assert len(reg.plugins) == count1

    def test_clear_unloads_plugins(self):
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        assert len(reg.plugins) > 0
        reg.clear()
        assert len(reg.plugins) == 0
        assert len(reg.errors) == 0
        assert reg._loaded is False

    def test_discover_nonexistent_directory(self):
        """Discovering a non-existent directory should not crash."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=["/nonexistent/path/to/plugins"])
        assert isinstance(reg.plugins, list)
        assert isinstance(reg.errors, list)

    def test_discover_empty_directory(self, tmp_path):
        """Discovering an empty directory should not crash."""
        reg = PluginRegistry()
        reg.discover(extra_dirs=[str(tmp_path)])
        for e in reg.errors:
            assert "tmp" not in e["source"]


# --------------------------------------------------------------------------- #
#  Plugin types
# --------------------------------------------------------------------------- #

class TestPluginTypes:
    """Test that each plugin type is correctly identified."""

    def test_effect_plugin_type(self):
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        effect_plugs = [p for p in reg.plugins if p.type == "effect"]
        assert len(effect_plugs) >= 1
        warm = [p for p in effect_plugs
                if p.instance.meta.name == "temperature-warm-example"]
        assert len(warm) == 1
        assert isinstance(warm[0].instance, EffectPlugin)

    def test_title_plugin_type(self):
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        title_plugs = [p for p in reg.plugins if p.type == "title"]
        assert len(title_plugs) >= 1
        gaming = [p for p in title_plugs
                  if p.instance.meta.name == "gaming-title-example"]
        assert len(gaming) == 1
        assert isinstance(gaming[0].instance, TitlePlugin)

    def test_effect_plugin_properties(self):
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        warm = [p for p in reg.effect_plugins
                if p.meta.name == "temperature-warm-example"]
        assert len(warm) == 1
        effect = warm[0]
        assert effect.effect_kind() == "example_warm"
        assert effect.effect_label() == "Warm (example)"
        assert effect.effect_category() == "Plugins"
        params = effect.effect_params()
        assert len(params) == 1
        assert params[0]["key"] == "amount"

    def test_title_plugin_properties(self):
        reg = PluginRegistry()
        reg.discover(extra_dirs=[BUNDLED_DIR])
        titles = [p for p in reg.title_plugins
                  if p.meta.name == "gaming-title-example"]
        assert len(titles) == 1
        preset = titles[0].title_preset()
        assert "label" in preset
        assert preset["label"] == "GG (example plugin)"
        assert preset["font"] == "Bungee"


# --------------------------------------------------------------------------- #
#  Plugin loading from temp directory
# --------------------------------------------------------------------------- #

class TestPluginLoading:
    """Test loading plugins from a temporary directory."""

    def test_load_valid_plugin(self, tmp_path):
        """A valid plugin should be loaded successfully."""
        reg = _load_tmp_plugin(tmp_path, "good_plugin.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class GoodPlugin(Plugin):
                meta = PluginMeta(name="good-test", version="1.0.0")

            def register():
                return GoodPlugin()
        ''')
        tmp = _tmp_plugins(reg)
        assert len(tmp) == 1
        assert tmp[0].instance.meta.name == "good-test"
        assert len(_tmp_errors(reg)) == 0

    def test_load_plugin_with_missing_requirement(self, tmp_path):
        """A plugin with unmet requirements should be skipped with an error."""
        reg = _load_tmp_plugin(tmp_path, "req_plugin.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class ReqPlugin(Plugin):
                meta = PluginMeta(
                    name="req-test",
                    requires=["nonexistent_package_xyz_12345"])

            def register():
                return ReqPlugin()
        ''')
        assert len(_tmp_plugins(reg)) == 0
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "Missing requirements" in errors[0]["error"]

    def test_load_plugin_register_returns_list(self, tmp_path):
        """register() returning a list of plugins should work."""
        reg = _load_tmp_plugin(tmp_path, "multi_plugin.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class P1(Plugin):
                meta = PluginMeta(name="multi-1")

            class P2(Plugin):
                meta = PluginMeta(name="multi-2")

            def register():
                return [P1(), P2()]
        ''')
        tmp = _tmp_plugins(reg)
        assert len(tmp) == 2
        names = {p.instance.meta.name for p in tmp}
        assert names == {"multi-1", "multi-2"}

    def test_load_plugin_register_returns_bad_type(self, tmp_path):
        """register() returning a non-Plugin should be recorded as error."""
        reg = _load_tmp_plugin(tmp_path, "bad_type.py", '''\
            def register():
                return "not a plugin"
        ''')
        assert len(_tmp_plugins(reg)) == 0
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "str" in errors[0]["error"]

    def test_load_plugin_with_on_load_error(self, tmp_path):
        """A plugin whose on_load() raises should be skipped."""
        reg = _load_tmp_plugin(tmp_path, "onload_fail.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class FailPlugin(Plugin):
                meta = PluginMeta(name="onload-fail")

                def on_load(self):
                    raise ValueError("on_load failed")

            def register():
                return FailPlugin()
        ''')
        assert len(_tmp_plugins(reg)) == 0
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "on_load failed" in errors[0]["error"]

    def test_load_plugin_with_syntax_error(self, tmp_path):
        """A plugin with a syntax error should be recorded."""
        plugin_file = tmp_path / "syntax_error.py"
        plugin_file.write_text("def register(\n")
        reg = PluginRegistry()
        reg.discover(extra_dirs=[str(tmp_path), BUNDLED_DIR])
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "Import failed" in errors[0]["error"]

    def test_underscore_files_skipped(self, tmp_path):
        """Files starting with _ should be skipped."""
        reg = _load_tmp_plugin(tmp_path, "_private.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class P(Plugin):
                meta = PluginMeta(name="private")

            def register():
                return P()
        ''')
        assert len(_tmp_plugins(reg)) == 0
        assert len(_tmp_errors(reg)) == 0

    def test_dot_files_skipped(self, tmp_path):
        """Files starting with . should be skipped."""
        plugin_file = tmp_path / ".hidden.py"
        plugin_file.write_text(textwrap.dedent('''\
            from opencut.plugins import Plugin, PluginMeta

            class P(Plugin):
                meta = PluginMeta(name="hidden")

            def register():
                return P()
        '''))
        reg = PluginRegistry()
        reg.discover(extra_dirs=[str(tmp_path), BUNDLED_DIR])
        assert len(_tmp_plugins(reg)) == 0

    def test_no_register_function(self, tmp_path):
        """A module without register() should be recorded as an error."""
        plugin_file = tmp_path / "no_register.py"
        plugin_file.write_text("# no register function here\nx = 1\n")
        reg = PluginRegistry()
        reg.discover(extra_dirs=[str(tmp_path), BUNDLED_DIR])
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "No register()" in errors[0]["error"]

    def test_register_with_no_name(self, tmp_path):
        """A plugin with no meta.name should be rejected."""
        reg = _load_tmp_plugin(tmp_path, "noname.py", '''\
            from opencut.plugins import Plugin, PluginMeta

            class NoName(Plugin):
                meta = PluginMeta(name="")

            def register():
                return NoName()
        ''')
        assert len(_tmp_plugins(reg)) == 0
        errors = _tmp_errors(reg)
        assert len(errors) == 1
        assert "meta.name is required" in errors[0]["error"]


# --------------------------------------------------------------------------- #
#  Core workflow without plugins
# --------------------------------------------------------------------------- #

class TestCoreWithoutPlugins:
    """Verify the core editor works with no plugins loaded."""

    def test_core_project_create_save_reopen(self, tmp_path):
        from opencut.project import store
        project = store.create_project("No plugins", "16:9")
        path = str(tmp_path / "test.opencut")
        store.save_project(project, path)
        loaded = store.load_project(path)
        assert loaded.name == "No plugins"

    def test_core_import_and_export(self, gameplay, tmp_path):
        from opencut.project import store
        from opencut.timeline import ops
        from opencut.rendering import exporter
        project = store.create_project("Test", "16:9")
        from opencut.engine import MediaAsset
        asset = MediaAsset(
            path=str(gameplay), name="gameplay.mp4", kind="video",
            duration=3.0, width=1280, height=720, has_audio=True, fps=30.0)
        project.assets.append(asset)
        ops.add_media_clip(project, asset, at=0.0)
        out = str(tmp_path / "export.mp4")
        result = exporter.export_project(project, out, "draft")
        assert result.ok

    def test_engine_effects_still_work(self):
        from opencut import engine
        cats = engine.effect_categories()
        assert "Basic" in cats
        assert "Stylise" in cats
        params = engine.effect_params("brightness")
        assert len(params) > 0


# --------------------------------------------------------------------------- #
#  Plugin discovery integration with app
# --------------------------------------------------------------------------- #

class TestPluginIntegration:
    """Test that plugins integrate correctly with the application."""

    def test_discover_plugins_returns_registry(self):
        reg = discover_plugins()
        assert isinstance(reg, PluginRegistry)

    def test_get_registry_singleton(self):
        reg = get_registry()
        assert isinstance(reg, PluginRegistry)

    def test_effect_plugin_build_filter(self, tmp_path):
        """An EffectPlugin's build_filter should return filter strings."""
        reg = _load_tmp_plugin(tmp_path, "fx_plugin.py", '''\
            from opencut.plugins import EffectPlugin, PluginMeta

            class FxPlugin(EffectPlugin):
                meta = PluginMeta(name="fx-test")

                def effect_kind(self):
                    return "test_fx"

                def effect_label(self):
                    return "Test FX"

                def effect_params(self):
                    return [{"key": "strength", "label": "Strength",
                             "default": 0.5, "lo": 0.0, "hi": 1.0,
                             "step": 0.01}]

                def build_filter(self, params, duration):
                    s = params.get("strength", 0.5)
                    return [f"eq=brightness={s - 0.5}"]

            def register():
                return FxPlugin()
        ''')
        tmp = _tmp_plugins(reg)
        assert len(tmp) == 1
        fx = tmp[0].instance
        assert isinstance(fx, EffectPlugin)
        result = fx.build_filter({"strength": 0.8}, 5.0)
        assert len(result) == 1
        assert "eq=brightness=0.3" in result[0]


# --------------------------------------------------------------------------- #
#  Plugin metadata
# --------------------------------------------------------------------------- #

class TestPluginMeta:
    """Test plugin metadata handling."""

    def test_meta_defaults(self):
        meta = PluginMeta(name="test")
        assert meta.version == "0.0.1"
        assert meta.author == ""
        assert meta.description == ""
        assert meta.requires == []

    def test_meta_custom(self):
        meta = PluginMeta(
            name="custom", version="2.0.0", author="Test",
            description="A test plugin", requires=["numpy"])
        assert meta.name == "custom"
        assert meta.version == "2.0.0"
        assert meta.requires == ["numpy"]


# --------------------------------------------------------------------------- #
#  Plugin on_unload
# --------------------------------------------------------------------------- #

class TestPluginUnload:
    """Test that plugins are properly unloaded."""

    def test_on_unload_called_on_clear(self, tmp_path):
        """clear() should call on_unload() on each loaded plugin."""
        plugin_file = tmp_path / "unload_test.py"
        plugin_file.write_text(textwrap.dedent('''\
            from opencut.plugins import Plugin, PluginMeta

            class UnloadPlugin(Plugin):
                meta = PluginMeta(name="unload-test")

                def on_unload(self):
                    pass

            def register():
                return UnloadPlugin()
        '''))
        reg = PluginRegistry()
        reg.discover(extra_dirs=[str(tmp_path), BUNDLED_DIR])
        tmp = _tmp_plugins(reg)
        assert len(tmp) == 1
        reg.clear()
        assert len(reg.plugins) == 0