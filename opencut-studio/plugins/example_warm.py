"""Example plugin: adds a 'Temperature Warm' effect.

This is a minimal example of an EffectPlugin.  To create your own:

1. Create a .py file in the plugins/ directory.
2. Define a ``register()`` function that returns a Plugin instance.
3. Restart OpenCut Studio — the plugin will be discovered automatically.

See ``opencut/plugins.py`` for the full Plugin API.
"""
from __future__ import annotations

from opencut.plugins import EffectPlugin, PluginMeta


class WarmEffect(EffectPlugin):
    meta = PluginMeta(
        name="temperature-warm-example",
        version="1.0.0",
        author="OpenCut Studio",
        description="Example: warms the image colour temperature.",
    )

    def effect_kind(self) -> str:
        return "example_warm"

    def effect_label(self) -> str:
        return "Warm (example)"

    def effect_category(self) -> str:
        return "Plugins"

    def effect_params(self):
        return [
            {"key": "amount", "label": "Amount", "default": 0.5,
             "lo": 0.0, "hi": 1.0, "step": 0.01},
        ]

    def build_filter(self, params, duration):
        amount = params.get("amount", 0.5)
        # Use the engine's colourtemperature filter
        temp = amount * 0.5   # 0.0 to 0.5 warm shift
        return [f"colortemperature=temperature={6500 + temp * 2000}"]


def register():
    return WarmEffect()