"""Example plugin: adds a title preset.

This demonstrates a TitlePlugin.  The preset is returned as a dict
matching the format used by ``opencut.timeline.titles``.
"""
from __future__ import annotations

from opencut.plugins import PluginMeta, TitlePlugin


class GamingTitle(TitlePlugin):
    meta = PluginMeta(
        name="gaming-title-example",
        version="1.0.0",
        author="OpenCut Studio",
        description="Example: a gaming-style title preset.",
    )

    def title_preset(self):
        return {
            "label": "GG (example plugin)",
            "description": "Big green 'GG' — gaming style",
            "text": "GG",
            "font": "Bungee",
            "size": 120,
            "color": "#39d98a",
            "outline_color": "#000000",
            "outline_width": 4.0,
            "shadow": 6.0,
            "animation": "pop_in",
        }


def register():
    return GamingTitle()