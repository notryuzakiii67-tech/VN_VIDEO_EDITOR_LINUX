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
                  text: str = "Text") -> Clip:
    """Add a text clip on the text track with large, readable defaults."""
    track = project.ensure_track_for("text")
    start = _place_without_overlap(
        track, project.duration if at is None else at, DEFAULT_TEXT_SECONDS)
    height = max(1, project.settings.height)
    size = max(28.0, height * 0.085)
    clip = Clip(kind="text", track_id=track.id, start=start,
                duration=DEFAULT_TEXT_SECONDS)
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


def trim_clip(project: Project, clip: Clip, edge: str, to: float) -> float:
    """Trim a clip's ``"left"`` or ``"right"`` edge to timeline time ``to``.

    Respects neighbour clips and the source media bounds.  Returns the
    boundary time actually applied (the new start or new end).
    """
    track = project.track(clip.track_id)
    if track is None:
        return clip.start if edge == "left" else clip.end
    still = _is_still(project, clip)
    speed = max(clip.speed, 1e-6)
    prev, nxt = _neighbours(track, clip)
    if edge == "left":
        lo = prev.end if prev is not None else 0.0
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
        hi = nxt.start if nxt is not None else float("inf")
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
