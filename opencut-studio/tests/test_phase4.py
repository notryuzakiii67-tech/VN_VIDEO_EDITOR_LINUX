"""Phase 4 tests: effects, colour grading, compositing, audio processing.

These tests exercise the Phase 4 additions — the effects catalogue, blend
modes, masks, chroma key, audio processing, and the inspector UI panels —
without requiring a real display or real media files.
"""
from __future__ import annotations

import os
import pytest

from opencut import engine
from opencut.project import store
from opencut.timeline import ops


# --------------------------------------------------------------------------- #
#  Engine facade: effects/blending/masks
# --------------------------------------------------------------------------- #

class TestEffectsFacade:
    """Verify the new effect/blend/mask engine functions."""

    def test_effect_categories(self):
        cats = engine.effect_categories()
        assert isinstance(cats, dict)
        assert "Basic" in cats
        assert "Colour" in cats
        assert "Stylise" in cats

    def test_effect_params(self):
        params = engine.effect_params("basic")
        assert len(params) > 0
        keys = [p.key for p in params]
        assert "brightness" in keys
        assert "contrast" in keys
        assert "saturation" in keys

    def test_effect_label(self):
        assert engine.effect_label("blur") == "Blur"
        assert engine.effect_label("bw") == "Black & White"
        assert engine.effect_label("vignette") == "Vignette"

    def test_effect_defaults(self):
        defaults = engine.effect_defaults("basic")
        assert isinstance(defaults, dict)
        assert "brightness" in defaults
        assert "contrast" in defaults
        assert defaults["brightness"] == 0.0

    def test_blend_modes(self):
        assert isinstance(engine.BLEND_MODES, list)
        assert "normal" in engine.BLEND_MODES
        assert "screen" in engine.BLEND_MODES
        assert "multiply" in engine.BLEND_MODES

    def test_mask_shapes(self):
        assert isinstance(engine.MASK_SHAPES, list)
        assert "none" in engine.MASK_SHAPES
        assert "rect" in engine.MASK_SHAPES
        assert "ellipse" in engine.MASK_SHAPES

    def test_all_effects_have_params(self):
        """Every effect kind should return a params list (even if empty)."""
        for cat, kinds in engine.effect_categories().items():
            for kind in kinds:
                params = engine.effect_params(kind)
                assert isinstance(params, list), f"{kind} params not a list"
                label = engine.effect_label(kind)
                assert isinstance(label, str) and label, f"{kind} has no label"

    def test_all_effects_have_defaults(self):
        """Every effect kind should have default parameter values."""
        for cat, kinds in engine.effect_categories().items():
            for kind in kinds:
                defaults = engine.effect_defaults(kind)
                assert isinstance(defaults, dict), f"{kind} defaults not a dict"


# --------------------------------------------------------------------------- #
#  Model: FilterInstance, MaskProps, ChromaProps
# --------------------------------------------------------------------------- #

class TestClipEffects:
    """Test effect management on clips."""

    def test_add_filter(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        # Add a brightness filter
        fx = clip.add_filter("brightness", {"brightness": 0.3})
        assert fx.kind == "brightness"
        assert fx.params["brightness"] == 0.3
        assert len(clip.filters) == 1

    def test_remove_filter(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        fx = clip.add_filter("blur", {"blur": 0.5})
        clip.remove_filter(fx.id)
        assert len(clip.filters) == 0

    def test_filter_serialisation(self):
        from vn_editor.core.model import FilterInstance
        fx = FilterInstance(kind="brightness", params={"brightness": 0.3})
        d = fx.to_dict()
        assert d["kind"] == "brightness"
        assert d["params"]["brightness"] == 0.3
        fx2 = FilterInstance.from_dict(d)
        assert fx2.kind == "brightness"
        assert fx2.params["brightness"] == 0.3

    def test_mask_props_defaults(self):
        from vn_editor.core.model import MaskProps
        m = MaskProps()
        assert m.shape == "none"
        assert m.feather == 0.02
        assert m.invert is False

    def test_chroma_props_defaults(self):
        from vn_editor.core.model import ChromaProps
        c = ChromaProps()
        assert c.enabled is False
        assert c.color == "#00b140"
        assert c.similarity == 0.18

    def test_clip_blend_mode(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        assert clip.blend == "normal"
        clip.blend = "screen"
        assert clip.blend == "screen"

    def test_clip_mask_shape(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        assert clip.mask.shape == "none"
        clip.mask.shape = "ellipse"
        assert clip.mask.shape == "ellipse"

    def test_clip_chroma_toggle(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        assert clip.chroma.enabled is False
        clip.chroma.enabled = True
        clip.chroma.similarity = 0.25
        assert clip.chroma.enabled is True
        assert clip.chroma.similarity == 0.25

    def test_audio_processing_fields(self):
        project = store.create_project("Test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080, has_audio=True)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        clip.audio.eq_low = 3.0
        clip.audio.compressor = 0.5
        clip.audio.denoise = 0.3
        clip.audio.echo = 0.2
        clip.audio.reverb = 0.1
        clip.audio.normalize = True
        assert clip.audio.eq_low == 3.0
        assert clip.audio.compressor == 0.5
        assert clip.audio.denoise == 0.3


# --------------------------------------------------------------------------- #
#  Inspector Phase 4 UI groups
# --------------------------------------------------------------------------- #

class TestInspectorPhase4:
    """Test that the Phase 4 groups exist and work."""

    def test_colour_group_built(self, qtbot):
        """Selecting a video clip should build the Colour group."""
        from opencut.ui.main_window import MainWindow
        win = MainWindow()
        qtbot.addWidget(win)
        # Import a video
        project = win.project
        from opencut.engine import MediaAsset
        import conftest
        # Use a real fixture
        asset = MediaAsset(
            path="/tmp/pytest-of-user/pytest-current/media0/gameplay.mp4",
            name="gameplay.mp4", kind="video",
            duration=6.0, width=1280, height=720, has_audio=True,
            fps=30.0)
        if not os.path.exists(asset.path):
            pytest.skip("Fixture not available")
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        win.timeline.set_selection([clip.id])
        # Check that the inspector has a Colour group
        found = False
        for i in range(win.inspector._layout.count()):
            w = win.inspector._layout.itemAt(i).widget()
            if w and hasattr(w, 'title') and callable(w.title):
                if 'Colour' in w.title():
                    found = True
        assert found, "Colour group not found in inspector"

    def test_compositing_group_built(self, qtbot):
        """Selecting a video clip should build the Compositing group."""
        from opencut.ui.main_window import MainWindow
        win = MainWindow()
        qtbot.addWidget(win)
        project = win.project
        from opencut.engine import MediaAsset
        asset = MediaAsset(
            path="/tmp/pytest-of-user/pytest-current/media0/gameplay.mp4",
            name="gameplay.mp4", kind="video",
            duration=6.0, width=1280, height=720, has_audio=True,
            fps=30.0)
        if not os.path.exists(asset.path):
            pytest.skip("Fixture not available")
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        win.timeline.set_selection([clip.id])
        found = False
        for i in range(win.inspector._layout.count()):
            w = win.inspector._layout.itemAt(i).widget()
            if w and hasattr(w, 'title') and callable(w.title):
                if 'Compositing' in w.title():
                    found = True
        assert found, "Compositing group not found in inspector"

    def test_effects_group_built(self, qtbot):
        """Selecting a video clip should build the Effects group."""
        from opencut.ui.main_window import MainWindow
        win = MainWindow()
        qtbot.addWidget(win)
        project = win.project
        from opencut.engine import MediaAsset
        asset = MediaAsset(
            path="/tmp/pytest-of-user/pytest-current/media0/gameplay.mp4",
            name="gameplay.mp4", kind="video",
            duration=6.0, width=1280, height=720, has_audio=True,
            fps=30.0)
        if not os.path.exists(asset.path):
            pytest.skip("Fixture not available")
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        win.timeline.set_selection([clip.id])
        found = False
        for i in range(win.inspector._layout.count()):
            w = win.inspector._layout.itemAt(i).widget()
            if w and hasattr(w, 'title') and callable(w.title):
                if 'Effects' in w.title():
                    found = True
        assert found, "Effects group not found in inspector"

    def test_audio_processing_group_built(self, qtbot):
        """Selecting a video clip with audio should build the Audio processing group."""
        from opencut.ui.main_window import MainWindow
        win = MainWindow()
        qtbot.addWidget(win)
        project = win.project
        from opencut.engine import MediaAsset
        asset = MediaAsset(
            path="/tmp/pytest-of-user/pytest-current/media0/gameplay.mp4",
            name="gameplay.mp4", kind="video",
            duration=6.0, width=1280, height=720, has_audio=True,
            fps=30.0)
        if not os.path.exists(asset.path):
            pytest.skip("Fixture not available")
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        win.timeline.set_selection([clip.id])
        found = False
        for i in range(win.inspector._layout.count()):
            w = win.inspector._layout.itemAt(i).widget()
            if w and hasattr(w, 'title') and callable(w.title):
                if 'Audio processing' in w.title():
                    found = True
        assert found, "Audio processing group not found in inspector"


# --------------------------------------------------------------------------- #
#  Round-trip: effects survive save/reopen
# --------------------------------------------------------------------------- #

class TestEffectsRoundTrip:
    """Test that effects survive project save/reopen."""

    def test_filters_round_trip(self, tmp_path):
        project = store.create_project("FX test", "16:9")
        asset = engine.MediaAsset(
            path="/fake/video.mp4", name="test.mp4", kind="video",
            duration=5.0, width=1920, height=1080)
        project.assets.append(asset)
        clip = ops.add_media_clip(project, asset, at=0.0)
        # Add effects
        clip.add_filter("brightness", {"brightness": 0.3})
        clip.add_filter("blur", {"blur": 0.5})
        clip.mask.shape = "ellipse"
        clip.mask.feather = 0.1
        clip.blend = "screen"
        clip.chroma.enabled = True
        clip.chroma.similarity = 0.25
        clip.audio.eq_low = 3.0
        clip.audio.compressor = 0.5
        # Save and reopen
        path = str(tmp_path / "test.opencut")
        store.save_project(project, path)
        loaded = store.load_project(path)
        lc = loaded.all_clips()[0]
        # Verify effects
        assert len(lc.filters) == 2
        kinds = {f.kind for f in lc.filters}
        assert "brightness" in kinds
        assert "blur" in kinds
        for f in lc.filters:
            if f.kind == "brightness":
                assert abs(f.params["brightness"] - 0.3) < 0.01
            if f.kind == "blur":
                assert abs(f.params["blur"] - 0.5) < 0.01
        # Verify compositing
        assert lc.mask.shape == "ellipse"
        assert abs(lc.mask.feather - 0.1) < 0.01
        assert lc.blend == "screen"
        assert lc.chroma.enabled is True
        assert abs(lc.chroma.similarity - 0.25) < 0.01
        # Verify audio processing
        assert abs(lc.audio.eq_low - 3.0) < 0.01
        assert abs(lc.audio.compressor - 0.5) < 0.01