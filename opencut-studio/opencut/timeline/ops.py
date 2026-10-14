"""Timeline editing operations.

Pure functions on the engine's project model: they mutate the project
they are given and return what they did.  Undo/redo is the caller's job
(the UI wraps calls in history transactions; the tests drive history
directly).  Keeping the operations free of Qt and of history makes them
trivially testable and reusable by future scripting/plugin APIs.

Invariants maintained here:

* clips on one track never overlap;
* a clip's ``start`` is never negative;
* trimming a media clip never exposes source material outside
  ``[0, source duration]`` (still images, text and freeze frames have no
  such limit — they hold a single frame);
* no clip is shorter than :data:`MIN_CLIP_SECONDS`.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .. import engine
from ..engine import Clip, MediaAsset, Project, TextProps, Track

MIN_CLIP_SECONDS = 0.1
MIN_SPEED = 0.1
MAX_SPEED = 8.0
DEFAULT_TEXT_SECONDS = 3.0
DEFAULT_IMAGE_SECONDS = 3.0


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _sorted_clips(track: Track) -> List[Clip]:
    return sorted(track.clips, key=lambda c: (c.start, c.id))


def _neighbours(track: Track, clip: Clip) -> Tuple[Optional[Clip], Optional[Clip]]:
    prev: Optional[Clip] = None
    nxt: Optional[Clip] = None
    for other in _sorted_clips(track):
        if other.id == clip.id:
            continue
        if other.end <= clip.start + 1e-9:
            if prev is None or other.end > prev.end:
                prev = other
        elif other.start >= clip.end - 1e-9:
            if nxt is None or other.start < nxt.start:
                nxt = other
    return prev, nxt


def _source_duration(project: Project, clip: Clip) -> float:
    """Seconds of source media behind a clip (0 when not applicable)."""
    asset = project.asset(clip.asset_id) if clip.asset_id else None
    if asset is not None and asset.duration > 0:
        return asset.duration
    try:
        return float(clip.meta.get("src_duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_still(project: Project, clip: Clip) -> bool:
    """True when the clip holds one frame (image, text, freeze frame)."""
    if clip.kind in ("image", "text") or clip.freeze:
        return True
    asset = project.asset(clip.asset_id) if clip.asset_id else None
    return bool(asset is not None and asset.kind == "image")


def _place_without_overlap(track: Track, start: float, duration: float) -> float:
    """First start time >= ``start`` where [start, start+duration) is free."""
    start = max(0.0, start)
    for other in _sorted_clips(track):
        if start < other.end and start + duration > other.start:
            start = other.end
    return start


def track_for_asset(project: Project, asset: MediaAsset) -> Track:
    """The default track for an asset: audio→audio, image→overlay, else video."""
    if asset.kind == "audio":
        return project.ensure_track_for("audio")
    if asset.kind == "image":
        return project.ensure_track_for("overlay")
    return project.ensure_track_for("video")


# --------------------------------------------------------------------------- #
#  Adding clips
# --------------------------------------------------------------------------- #
def add_media_clip(project: Project, asset: MediaAsset,
                   at: Optional[float] = None,
                   track: Optional[Track] = None) -> Clip:
    """Append ``asset`` to the timeline; returns the new clip."""
    track = track or track_for_asset(project, asset)
    duration = asset.duration if asset.duration > 0.05 else 4.0
    if asset.kind == "image":
        duration = DEFAULT_IMAGE_SECONDS
    start = _place_without_overlap(
        track, project.duration if at is None else at, duration)
    clip = Clip(kind=("image" if asset.kind == "image" else
                      "audio" if asset.kind == "audio" else "video"),
                track_id=track.id, start=start, duration=duration)
    clip.path = asset.path
    clip.asset_id = asset.id
    clip.name = asset.name
    clip.meta["has_audio"] = asset.has_audio
    clip.meta["src_w"] = asset.width
    clip.meta["src_h"] = asset.height
    clip.meta["src_duration"] = asset.duration
    if asset.kind == "video" and (
            asset.width < project.settings.width * 0.6
            or asset.height < project.settings.height * 0.6):
        clip.transform.fit = "cover"
    project.add_clip(clip, track)
    return clip


def add_text_clip(project: Project, at: Optional[float] = None,
                  text: str = "Text",
                  duration: float = DEFAULT_TEXT_SECONDS) -> Clip:
    """Add a text clip on the text track with large, readable defaults."""
    duration = max(MIN_CLIP_SECONDS, float(duration))
    track = project.ensure_track_for("text")
    start = _place_without_overlap(
        track, project.duration if at is None else at, duration)
    height = max(1, project.settings.height)
    size = max(28.0, height * 0.085)
    clip = Clip(kind="text", track_id=track.id, start=start,
                duration=duration)
    clip.name = text.splitlines()[0][:24] or "Text"
    clip.text = TextProps(text=text, font="Bebas Neue", size=size,
                          color="#ffffff", outline_width=max(2.0, size * 0.07),
                          outline_color="#000000", shadow=2.0)
    clip.transform.x = 0.5
    clip.transform.y = 0.22
    project.add_clip(clip, track)
    return clip


# --------------------------------------------------------------------------- #
#  Split / duplicate / delete
# --------------------------------------------------------------------------- #
def split_clip_at(project: Project, clip: Clip, t: float) -> Optional[Tuple[Clip, Clip]]:
    """Split ``clip`` at timeline time ``t``. Returns (left, right) or None."""
    if not (clip.start + 0.02 < t < clip.end - 0.02):
        return None
    track = project.track(clip.track_id)
    if track is None:
        return None
    local = t - clip.start
    left = clip.clone()
    right = clip.clone()
    left.id, right.id = clip.id, Clip().id
    left.start = clip.start
    left.duration = local
    right.start = t
    right.duration = max(0.02, clip.duration - local)
    if not clip.freeze:
        if clip.speed_curve is not None:
            right.src_in = clip.src_time(t)
        else:
            right.src_in = clip.src_in + local * clip.speed
    # A transition belongs to one side of the cut only.
    right.transition_in = None
    left.transition_out = None
    # Keyframe times are clip-local: the right half keeps only the keys
    # after the cut, shifted back by the split point.
    for store_name in ("animations", "audio_animations"):
        for half, keep in ((left, lambda k: k.t <= local + 1e-9),
                           (right, lambda k: k.t >= local - 1e-9)):
            store = getattr(half, store_name)
            shift = 0.0 if half is left else -local
            for prop in list(store):
                keys = [engine.Keyframe(t=round(k.t + shift, 4),
                                        value=k.value, easing=k.easing)
                        for k in store[prop] if keep(k)]
                if keys:
                    store[prop] = keys
                else:
                    store.pop(prop, None)
    if clip.speed_curve is not None:
        left.speed_curve = type(clip.speed_curve)(
            list(clip.speed_curve.points), clip.speed_curve.preset)
        right.speed_curve = type(clip.speed_curve)(
            list(clip.speed_curve.points), clip.speed_curve.preset)
    project.remove_clip(clip.id)
    project.add_clip(left, track)
    project.add_clip(right, track)
    return left, right


def split_at(project: Project, t: float,
             clip_ids: Optional[Sequence[str]] = None) -> List[Clip]:
    """Split the given clips (or every unlocked clip) at time ``t``."""
    if clip_ids:
        targets = [c for cid in clip_ids if (c := project.clip(cid)) is not None]
    else:
        targets = [c for c in project.clips_at(t) if not c.locked]
    made: List[Clip] = []
    for clip in targets:
        pair = split_clip_at(project, clip, t)
        if pair:
            made.extend(pair)
    return made


def duplicate_clip(project: Project, clip: Clip) -> Clip:
    """Duplicate ``clip`` immediately after itself on the same track."""
    track = project.track(clip.track_id)
    if track is None:
        raise ValueError("Clip is not on any track")
    copy = clip.clone()
    copy.id = Clip().id
    copy.start = _place_without_overlap(track, clip.end, clip.duration)
    project.add_clip(copy, track)
    return copy


def delete_clips(project: Project, clip_ids: Sequence[str]) -> int:
    """Remove clips by id. Returns how many were removed."""
    removed = 0
    for clip_id in clip_ids:
        if project.remove_clip(clip_id) is not None:
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
#  Move / trim / speed
# --------------------------------------------------------------------------- #
def move_clip(project: Project, clip: Clip, new_start: float,
              track: Optional[Track] = None) -> float:
    """Move a clip in time (and optionally across tracks), without overlap.

    The clip is clamped between its neighbours on the destination track.
    Returns the start time actually applied.
    """
    target = track or project.track(clip.track_id)
    if target is None:
        return clip.start
    if target.id != clip.track_id:
        old = project.track(clip.track_id)
        if old is not None and clip in old.clips:
            old.clips.remove(clip)
        clip.track_id = target.id
        target.clips.append(clip)
    new_start = max(0.0, new_start)
    prev, nxt = _neighbours(target, clip)
    if prev is not None:
        new_start = max(new_start, prev.end)
    if nxt is not None and new_start + clip.duration > nxt.start:
        new_start = max(0.0, nxt.start - clip.duration)
        if prev is not None and new_start < prev.end:
            new_start = prev.end
    clip.start = new_start
    return new_start


def trim_clip(project: Project, clip: Clip, edge: str, to: float,
              respect_neighbours: bool = True) -> float:
    """Trim a clip's ``"left"`` or ``"right"`` edge to timeline time ``to``.

    Respects the source media bounds, and (unless ``respect_neighbours``
    is off, as ripple edits require) the neighbouring clips.  Returns the
    boundary time actually applied (the new start or new end).
    """
    track = project.track(clip.track_id)
    if track is None:
        return clip.start if edge == "left" else clip.end
    still = _is_still(project, clip)
    speed = max(clip.speed, 1e-6)
    prev, nxt = _neighbours(track, clip)
    if edge == "left":
        lo = prev.end if (prev is not None and respect_neighbours) else 0.0
        if not still:
            lo = max(lo, clip.start - clip.src_in / speed)
        hi = clip.end - MIN_CLIP_SECONDS
        new_start = _clamp(to, lo, max(lo, hi))
        delta = new_start - clip.start
        if not still:
            clip.src_in = max(0.0, clip.src_in + delta * speed)
        clip.start = new_start
        clip.duration -= delta
        return clip.start
    if edge == "right":
        hi = nxt.start if (nxt is not None and respect_neighbours) \
            else float("inf")
        if not still:
            src = _source_duration(project, clip)
            if src > 0:
                hi = min(hi, clip.start + max(0.0, src - clip.src_in) / speed)
        lo = clip.start + MIN_CLIP_SECONDS
        new_end = _clamp(to, lo, max(lo, hi))
        clip.duration = new_end - clip.start
        return clip.end
    raise ValueError(f"Unknown trim edge: {edge!r} (expected 'left' or 'right')")


def set_clip_speed(project: Project, clip: Clip, speed: float) -> float:
    """Set a clip's constant playback speed, preserving the source span.

    The timeline duration changes inversely with speed (2x = half as
    long).  Clips driven by a speed *curve* are refused — curves get
    their own editor in a later phase.  Returns the applied speed.
    """
    if clip.speed_curve is not None:
        raise ValueError("This clip uses a speed curve; constant speed "
                         "does not apply")
    speed = _clamp(float(speed), MIN_SPEED, MAX_SPEED)
    span = clip.duration * clip.speed
    new_duration = max(MIN_CLIP_SECONDS, span / speed)
    track = project.track(clip.track_id)
    if track is not None:
        _, nxt = _neighbours(track, clip)
        if nxt is not None:
            new_duration = max(MIN_CLIP_SECONDS,
                               min(new_duration, nxt.start - clip.start))
    clip.speed = speed
    clip.duration = new_duration
    return speed


# --------------------------------------------------------------------------- #
#  Phase 2 edit modes: ripple / roll / slip / slide / ripple delete
# --------------------------------------------------------------------------- #
def ripple_trim(project: Project, clip: Clip, edge: str, to: float) -> float:
    """Trim like :func:`trim_clip`, then shift everything downstream of the
    edit point on the same track by the duration change.

    The track's total length changes; no gap or overlap is created at the
    edit point.  Returns the applied boundary time.
    """
    track = project.track(clip.track_id)
    if track is None:
        return clip.start if edge == "left" else clip.end
    old_start, old_duration, old_end = clip.start, clip.duration, clip.end
    trim_clip(project, clip, edge, to, respect_neighbours=False)
    delta = clip.duration - old_duration
    if edge == "left":
        clip.start += delta            # the head stays glued to its neighbour
    if delta:
        for other in track.clips:
            if other.id != clip.id and other.start >= old_end - 1e-9:
                other.start = max(0.0, other.start + delta)
    return clip.start if edge == "left" else clip.end


def roll_edit(project: Project, clip: Clip, to: float) -> Optional[float]:
    """Move the edit point between ``clip`` and its next neighbour.

    Both clips keep their outer edges; the outgoing clip grows/shrinks
    while the incoming one's head moves through its source.  Returns the
    applied boundary time, or ``None`` when there is no adjacent neighbour.
    """
    track = project.track(clip.track_id)
    if track is None:
        return None
    _prev, nxt = _neighbours(track, clip)
    if nxt is None or abs(clip.end - nxt.start) > 0.02:
        return None
    boundary = clip.end
    lo = clip.start + MIN_CLIP_SECONDS
    hi = boundary + max(0.0, nxt.duration - MIN_CLIP_SECONDS)
    if not _is_still(project, clip):
        src = _source_duration(project, clip)
        if src > 0:
            span = clip.src_time(clip.end) - clip.src_in \
                if clip.speed_curve is not None else clip.duration * clip.speed
            hi = min(hi, boundary + max(0.0, src - clip.src_in - span)
                     / max(clip.speed, 1e-6))
    if not _is_still(project, nxt):
        lo = max(lo, boundary - nxt.src_in / max(nxt.speed, 1e-6))
    new_boundary = _clamp(to, lo, max(lo, hi))
    delta = new_boundary - boundary
    if abs(delta) < 1e-9:
        return boundary
    clip.duration += delta
    nxt.start += delta
    nxt.duration -= delta
    if not _is_still(project, nxt):
        nxt.src_in = max(0.0, nxt.src_in + delta * nxt.speed)
    return new_boundary


def slip_edit(project: Project, clip: Clip, delta: float) -> float:
    """Slip: keep the clip's timeline position, shift the source window.

    ``delta`` is in timeline seconds (converted through the clip speed).
    Returns the source shift actually applied, in timeline seconds.
    """
    if _is_still(project, clip):
        raise ValueError("Still images and text have no source to slip")
    src = _source_duration(project, clip)
    if src <= 0:
        raise ValueError("The source duration is unknown; cannot slip")
    consumed = clip.src_time(clip.end) - clip.src_in \
        if clip.speed_curve is not None else clip.duration * clip.speed
    speed = max(clip.speed, 1e-6)
    new_in = _clamp(clip.src_in + delta * speed, 0.0, max(0.0, src - consumed))
    applied = (new_in - clip.src_in) / speed
    clip.src_in = new_in
    return applied


def slide_edit(project: Project, clip: Clip, delta: float) -> float:
    """Slide: move the clip; its neighbours' touching edges follow.

    The previous clip grows/shrinks at its tail, the next clip at its
    head (through its source).  Needs both neighbours.  Returns the
    applied shift.
    """
    track = project.track(clip.track_id)
    if track is None:
        return 0.0
    prev, nxt = _neighbours(track, clip)
    if prev is None or nxt is None:
        return 0.0
    lo = -(prev.duration - MIN_CLIP_SECONDS)
    hi = nxt.duration - MIN_CLIP_SECONDS
    if _is_still(project, nxt):
        pass                                     # a still can always grow
    else:
        lo = max(lo, -nxt.src_in / max(nxt.speed, 1e-6))
    if not _is_still(project, prev):
        src = _source_duration(project, prev)
        if src > 0:
            span = prev.src_time(prev.end) - prev.src_in \
                if prev.speed_curve is not None else prev.duration * prev.speed
            hi = min(hi, max(0.0, src - prev.src_in - span)
                     / max(prev.speed, 1e-6))
    applied = _clamp(delta, lo, max(lo, hi))
    if abs(applied) < 1e-9:
        return 0.0
    clip.start += applied
    prev.duration += applied
    nxt.start += applied
    nxt.duration -= applied
    if not _is_still(project, nxt):
        nxt.src_in = max(0.0, nxt.src_in + applied * nxt.speed)
    return applied


def ripple_delete(project: Project, clip_ids: Sequence[str]) -> int:
    """Delete clips and pull everything after them (per track) closed.

    Assumes gapless sequencing on each affected track: followers shift
    left by the total duration removed before them.  Returns the number
    of clips removed.
    """
    ids = set(clip_ids)
    if not ids:
        return 0
    removed = 0
    for track in project.tracks:
        shift = 0.0
        for clip in _sorted_clips(track):
            if clip.id in ids:
                shift += clip.duration
            elif shift:
                clip.start = max(0.0, clip.start - shift)
    for clip_id in ids:
        if project.remove_clip(clip_id) is not None:
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
#  Keyframes (transform properties + audio volume)
# --------------------------------------------------------------------------- #
#: Properties the keyframe editor can animate.  Transform values use the
#: model's native units (x/y/opacity 0..1, scale multiplier, rotation in
#: degrees); ``volume`` lives in ``clip.audio_animations``.
KEYFRAME_PROPS = engine.ANIM_PROPS + ("volume",)


def _kf_store(clip: Clip, prop: str) -> dict:
    return clip.audio_animations if prop == "volume" else clip.animations


def _kf_default(clip: Clip, prop: str) -> float:
    if prop == "volume":
        return float(clip.audio.volume)
    return float(getattr(clip.transform, prop))


def keyframes(clip: Clip, prop: str) -> List[engine.Keyframe]:
    """The clip's keyframes for ``prop``, sorted by time (a copy)."""
    return sorted(_kf_store(clip, prop).get(prop, []),
                  key=lambda k: k.t)


def add_keyframe(clip: Clip, prop: str, t: float,
                 value: Optional[float] = None,
                 easing: str = "ease_in_out") -> engine.Keyframe:
    """Add (or replace) a keyframe at clip-local time ``t``.

    The default value is whatever the property evaluates to at ``t``
    right now, so adding a first keyframe never changes the picture.
    """
    if prop not in KEYFRAME_PROPS:
        raise ValueError(f"Cannot keyframe {prop!r}; choose one of "
                         f"{', '.join(KEYFRAME_PROPS)}")
    if easing not in engine.EASINGS:
        raise ValueError(f"Unknown easing {easing!r}")
    t = _clamp(float(t), 0.0, max(0.0, clip.duration))
    store = _kf_store(clip, prop)
    keys = [k for k in store.get(prop, []) if abs(k.t - t) > 1e-4]
    kf = engine.Keyframe(t=t,
                         value=float(value) if value is not None
                         else evaluate(clip, prop, t),
                         easing=easing)
    keys.append(kf)
    keys.sort(key=lambda k: k.t)
    store[prop] = keys
    return kf


def remove_keyframe(clip: Clip, prop: str, t: float) -> bool:
    store = _kf_store(clip, prop)
    keys = store.get(prop, [])
    kept = [k for k in keys if abs(k.t - t) > 1e-4]
    if len(kept) == len(keys):
        return False
    if kept:
        store[prop] = kept
    else:
        store.pop(prop, None)
    return True


def clear_keyframes(clip: Clip, prop: str) -> None:
    _kf_store(clip, prop).pop(prop, None)


def evaluate(clip: Clip, prop: str, t: float) -> float:
    """The property's value at clip-local time ``t`` (keyframes applied)."""
    keys = _kf_store(clip, prop).get(prop, [])
    default = _kf_default(clip, prop)
    if not keys:
        return default
    return float(engine.eval_keyframes(keys, float(t), default))


# --------------------------------------------------------------------------- #
#  Transitions
# --------------------------------------------------------------------------- #
def previous_clip(project: Project, clip: Clip) -> Optional[Clip]:
    track = project.track(clip.track_id)
    if track is None:
        return None
    prev, _nxt = _neighbours(track, clip)
    return prev


def effective_transition(project: Project, clip: Clip) -> Optional[object]:
    """The transition that will render at this clip's start boundary.

    Mirrors the engine's rule: the previous clip's ``transition_out``
    wins over this clip's ``transition_in``.
    """
    prev = previous_clip(project, clip)
    if prev is not None and prev.transition_out is not None \
            and prev.transition_out.kind != "none":
        return prev.transition_out
    if clip.transition_in is not None and clip.transition_in.kind != "none":
        return clip.transition_in
    return None


def set_transition(project: Project, clip: Clip, kind: str,
                   duration: Optional[float] = None) -> Optional[object]:
    """Set (or, with kind ``"none"``, clear) the transition into ``clip``.

    Stored on the incoming clip's ``transition_in``; any stale
    ``transition_out`` on the previous clip is cleared so there is one
    source of truth.  Duration is clamped to the shorter neighbour.
    """
    if kind in ("", "none"):
        clip.transition_in = None
        return None
    prev = previous_clip(project, clip)
    limit = min(clip.duration, prev.duration) if prev is not None \
        else clip.duration
    if duration is None:
        duration = engine.transition_default_duration(kind)
    duration = _clamp(float(duration), 0.05, max(0.05, limit))
    transition = engine.Transition(kind=kind, duration=duration, edge="in")
    clip.transition_in = transition
    if prev is not None:
        prev.transition_out = None
    return transition


def remove_transition(project: Project, clip: Clip) -> None:
    clip.transition_in = None
    prev = previous_clip(project, clip)
    if prev is not None:
        prev.transition_out = None


# --------------------------------------------------------------------------- #
#  Speed curves, reverse, fade curves, text animation
# --------------------------------------------------------------------------- #
def apply_speed_curve(clip: Clip, preset_label: str) -> None:
    """Apply a built-in speed-curve preset ('None' clears the curve).

    The clip's timeline duration is unchanged; the curve changes how
    much source is consumed across it (see ``Clip.src_time``).
    """
    points = engine.speed_curve_points(preset_label)
    if not points:
        clip.speed_curve = None
        return
    clip.speed_curve = engine.SpeedCurve(points=list(points),
                                         preset=preset_label)


def set_speed_curve_points(clip: Clip,
                           points: Sequence[Tuple[float, float]]) -> None:
    """Set custom curve points: (normalised progress 0..1, speed)."""
    cleaned: List[Tuple[float, float]] = []
    for progress, speed in sorted(points):
        progress = _clamp(float(progress), 0.0, 1.0)
        speed = _clamp(float(speed), 0.05, 10.0)
        if cleaned and abs(cleaned[-1][0] - progress) < 1e-4:
            cleaned[-1] = (progress, speed)
        else:
            cleaned.append((progress, speed))
    if len(cleaned) < 2:
        clip.speed_curve = None
        return
    clip.speed_curve = engine.SpeedCurve(points=cleaned, preset="Custom")


def clear_speed_curve(clip: Clip) -> None:
    clip.speed_curve = None


def set_reverse(clip: Clip, on: bool) -> None:
    """Play the clip backwards (rendered, not faked, by the engine)."""
    clip.reverse = bool(on)


def set_fade_type(clip: Clip, fade_type: str) -> None:
    if fade_type not in engine.FADE_TYPES:
        raise ValueError(f"Unknown fade type {fade_type!r}; choose one of "
                         f"{', '.join(engine.FADE_TYPES)}")
    clip.audio.fade_type = fade_type


def apply_text_animation(clip: Clip, name: str, anim_in: float = 0.4) -> None:
    """Apply a text-animation preset (converted to editable keyframes)."""
    if clip.text is None:
        raise ValueError("Only text clips can take a text animation")
    engine.apply_text_animation(clip, name, anim_in)


def clear_text_animation(clip: Clip) -> None:
    engine.clear_text_animation(clip)
