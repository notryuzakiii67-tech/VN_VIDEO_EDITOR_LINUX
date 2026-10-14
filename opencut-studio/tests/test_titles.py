"""Title presets: built-ins are valid, applying one styles a real text
clip (with keyframed animation), and styles round-trip through
capture/apply.  Sizes are relative to the canvas height."""
import pytest

from opencut import engine
from opencut.project import store
from opencut.timeline import ops, titles


def test_builtin_presets_only_use_bundled_fonts_and_animations():
    for preset in titles.title_presets():
        assert preset["font"] in titles.BUNDLED_FONTS
        assert preset["animation"] == "none" or \
            preset["animation"] in engine.text_animation_names()
        assert 0.0 < preset["size_rel"] < 0.3
        assert 0.0 <= preset["y"] <= 1.0


def test_add_title_applies_style_and_animation(project_916):
    preset = titles.preset_by_id("bold_impact")
    clip = titles.add_title(project_916, preset, "POV: WIN", at=1.0)
    assert clip.kind == "text"
    assert clip.start == pytest.approx(1.0)
    assert clip.text.text == "POV: WIN"
    assert clip.text.font == "Bebas Neue"
    # 9:16 canvas is 1920 tall: size is relative to height
    assert clip.text.size == pytest.approx(0.115 * 1920)
    assert clip.transform.y == pytest.approx(0.78)
    assert clip.meta.get("text_anim") == "pop_in"
    assert any(clip.animations.values())          # keyframes were created


def test_static_preset_creates_no_keyframes(project_916):
    preset = titles.preset_by_id("quiet_note")
    clip = titles.add_title(project_916, preset, "note", at=0.0)
    assert not any(clip.animations.values())
    assert clip.meta.get("text_anim") in (None, "none")


def test_capture_and_reapply_a_style(project_916):
    preset = titles.preset_by_id("clean_caption")
    first = titles.add_title(project_916, preset, "one", at=0.0)
    first.text.color = "#ffee00"
    captured = titles.preset_from_clip(project_916, first, "Mine")
    second = titles.add_title(project_916, captured, "two", at=5.0)
    assert second.text.color == "#ffee00"
    assert second.text.font == first.text.font
    assert second.text.size == pytest.approx(first.text.size)
    assert second.transform.y == pytest.approx(first.transform.y)


def test_normalise_preset_rejects_unknown_font_and_animation():
    fixed = titles.normalise_preset(
        {"font": "Comic Sans", "animation": "explode", "label": "X"})
    assert fixed["font"] == "Bebas Neue"
    assert fixed["animation"] == "none"
    assert fixed["label"] == "X"
    assert titles.normalise_preset("junk")["label"] == "Custom title"


def test_apply_preset_requires_a_text_clip(gameplay):
    from opencut.media import importer
    project = store.create_project("T", "16:9")
    asset = importer.import_media([str(gameplay)], project).added[0]
    clip = ops.add_media_clip(project, asset, at=0.0)
    with pytest.raises(ValueError):
        titles.apply_preset_to_clip(
            project, clip, titles.preset_by_id("bold_impact"))
