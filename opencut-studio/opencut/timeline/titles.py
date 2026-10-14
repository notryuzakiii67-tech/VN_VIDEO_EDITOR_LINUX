"""Reusable title presets.

A title preset is a plain, JSON-serialisable ``dict`` describing a
complete text style: font, size (relative to the canvas height so a
preset survives a 16:9 → 9:16 switch), colours, outline, shadow,
optional background box, position, duration and an entrance animation
(converted to editable keyframes by the engine when applied).

Six original built-in presets ship with the app; users can capture the
style of any text clip as their own preset (the UI stores those in
``QSettings``) and re-apply them anywhere.  Applying a preset mutates a
real text clip — what you see is exactly what exports.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import engine
from ..engine import Clip, Project
from . import ops

#: Fonts bundled with the app (SIL Open Font License, see
#: ``docs/DEPENDENCIES.md``).  Presets only reference these so a title
#: always renders identically on any machine.
BUNDLED_FONTS = (
    "Alfa Slab One", "Anton", "Archivo Black", "Bebas Neue",
    "Bowlby One", "Bungee", "Caveat", "Creepster", "Fredoka", "Inter",
    "Montserrat", "Noto Sans", "Oswald", "Poppins", "Righteous",
    "Roboto", "Rubik", "Space Grotesk", "Teko", "Titan One",
)

#: Every key a preset dict may carry, with defaults for missing keys.
_PRESET_DEFAULTS: Dict[str, Any] = {
    "id": "custom",
    "label": "Custom title",
    "description": "",
    "font": "Bebas Neue",
    "size_rel": 0.085,          # fraction of the canvas height
    "color": "#ffffff",
    "bold": False,
    "italic": False,
    "outline_rel": 0.07,        # fraction of the font size
    "outline_color": "#000000",
    "shadow": 2.0,              # px at a 1080-tall canvas
    "bg_enabled": False,
    "bg_color": "#000000",
    "bg_opacity": 0.55,
    "align": "center",
    "x": 0.5,                   # clip centre, fraction of canvas width
    "y": 0.22,                  # clip centre, fraction of canvas height
    "duration": 3.0,
    "animation": "none",        # engine text-animation preset name
    "anim_in": 0.4,
}


def _preset(**kwargs: Any) -> Dict[str, Any]:
    merged = dict(_PRESET_DEFAULTS)
    merged.update(kwargs)
    return merged


TITLE_PRESETS: List[Dict[str, Any]] = [
    _preset(
        id="bold_impact", label="Bold Impact",
        description="Big condensed headline with a pop entrance — the "
                    "classic gameplay-video title.",
        font="Bebas Neue", size_rel=0.115, color="#ffffff",
        outline_rel=0.08, shadow=3.0, y=0.78,
        animation="pop_in", duration=3.0,
    ),
    _preset(
        id="clean_caption", label="Clean caption",
        description="Small readable caption on a translucent box, "
                    "bottom of frame.",
        font="Montserrat", size_rel=0.048, outline_rel=0.0, shadow=0.0,
        bg_enabled=True, bg_color="#000000", bg_opacity=0.55,
        y=0.88, animation="fade_in", duration=2.5,
    ),
    _preset(
        id="lower_third", label="Lower third",
        description="Left-aligned name strap with a slide-in.",
        font="Poppins", size_rel=0.055, outline_rel=0.05, shadow=1.5,
        bg_enabled=True, bg_color="#15181d", bg_opacity=0.72,
        align="left", x=0.30, y=0.78,
        animation="slide_right", duration=4.0,
    ),
    _preset(
        id="top_banner", label="Top banner",
        description="Compact announcement across the top of the frame.",
        font="Oswald", size_rel=0.05, outline_rel=0.06, shadow=2.0,
        y=0.09, animation="slide_down", duration=3.0,
    ),
    _preset(
        id="outline_pop", label="Outline pop",
        description="Huge heavy type, thick outline, zoom entrance.",
        font="Archivo Black", size_rel=0.13, outline_rel=0.10,
        shadow=4.0, y=0.5, animation="zoom_in", duration=2.5,
    ),
    _preset(
        id="quiet_note", label="Quiet note",
        description="Small understated note, no animation.",
        font="Inter", size_rel=0.038, outline_rel=0.03, shadow=1.0,
        color="#f2f2f2", y=0.14, animation="none", duration=3.0,
    ),
]


def title_presets() -> List[Dict[str, Any]]:
    """Copies of the built-in title presets."""
    return [dict(p) for p in TITLE_PRESETS]


def preset_by_id(preset_id: str) -> Optional[Dict[str, Any]]:
    for preset in TITLE_PRESETS:
        if preset["id"] == preset_id:
            return dict(preset)
    return None


def normalise_preset(data: Any) -> Dict[str, Any]:
    """Validate/coerce untrusted preset data (e.g. from QSettings).

    Unknown keys are dropped; wrong types fall back to the defaults.
    The font is only kept when it is one of the bundled OFL fonts.
    """
    merged = dict(_PRESET_DEFAULTS)
    if not isinstance(data, dict):
        return merged
    for key, default in _PRESET_DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            merged[key] = bool(value)
        elif isinstance(default, float):
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, str):
            merged[key] = str(value)
    if merged["font"] not in BUNDLED_FONTS:
        merged["font"] = "Bebas Neue"
    if merged["align"] not in ("left", "center", "right"):
        merged["align"] = "center"
    if merged["animation"] not in ("none", *engine.text_animation_names()):
        merged["animation"] = "none"
    return merged


def preset_from_clip(project: Project, clip: Clip,
                     label: str = "My title") -> Dict[str, Any]:
    """Capture a text clip's current styling as a reusable preset."""
    if clip.text is None:
        raise ValueError("Only text clips can be captured as title presets")
    height = max(1, project.settings.height)
    size = max(1.0, clip.text.size)
    return _preset(
        id="user", label=label,
        description="Captured from a clip in this project.",
        font=clip.text.font if clip.text.font in BUNDLED_FONTS
        else "Bebas Neue",
        size_rel=size / height,
        color=clip.text.color,
        bold=clip.text.bold, italic=clip.text.italic,
        outline_rel=clip.text.outline_width / size,
        outline_color=clip.text.outline_color,
        shadow=clip.text.shadow * 1080.0 / height,
        bg_enabled=clip.text.bg_enabled,
        bg_color=clip.text.bg_color,
        bg_opacity=clip.text.bg_opacity,
        align=clip.text.align,
        x=clip.transform.x, y=clip.transform.y,
        duration=clip.duration,
        animation=clip.meta.get("text_anim", "none"),
        anim_in=float(clip.text.anim_in or 0.4),
    )


def apply_preset_to_clip(project: Project, clip: Clip,
                         preset: Dict[str, Any]) -> None:
    """Style an existing text clip from a preset (position included)."""
    if clip.text is None:
        raise ValueError("Only text clips can take a title preset")
    preset = normalise_preset(preset)
    height = max(1, project.settings.height)
    size = max(12.0, preset["size_rel"] * height)
    text = clip.text
    text.font = preset["font"]
    text.size = size
    text.color = preset["color"]
    text.bold = bool(preset["bold"])
    text.italic = bool(preset["italic"])
    text.outline_width = preset["outline_rel"] * size
    text.outline_color = preset["outline_color"]
    text.shadow = preset["shadow"] * height / 1080.0
    text.shadow_color = "#000000"
    text.bg_enabled = bool(preset["bg_enabled"])
    text.bg_color = preset["bg_color"]
    text.bg_opacity = preset["bg_opacity"]
    text.align = preset["align"]
    clip.transform.x = float(preset["x"])
    clip.transform.y = float(preset["y"])
    animation = preset["animation"]
    if animation and animation != "none":
        text.anim_in = float(preset["anim_in"])
        ops.apply_text_animation(clip, animation,
                                 anim_in=float(preset["anim_in"]))


def add_title(project: Project, preset: Dict[str, Any], text: str,
              at: Optional[float] = None) -> Clip:
    """Create a text clip styled by ``preset`` at ``at`` (or playhead/end)."""
    preset = normalise_preset(preset)
    clip = ops.add_text_clip(project, at=at, text=text,
                             duration=float(preset["duration"]))
    apply_preset_to_clip(project, clip, preset)
    clip.name = text.splitlines()[0][:24] or preset["label"]
    return clip
