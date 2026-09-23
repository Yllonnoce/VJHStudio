"""The idea chips shipped with the app.

``data/prompt_ideas.json`` holds a short list of phrases for each builder field that
benefits from a nudge (Subject is the user's own idea, so it has none), once per mode:
stills want painting styles and lens choices, clips want camera moves, motion and
grading terms. The Generate page renders both sets and shows the active mode's row; it
also hands the whole mapping to Alpine as JSON. Toggling only ever rewrites the text of
a field, so nothing here reaches the API.

Read once and cached, exactly like ``catalog.CURATED_PATH``'s curated seed file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

IDEAS_PATH = Path(__file__).resolve().parent.parent / "data" / "prompt_ideas.json"

# The builder fields that get a chip row, in the order they appear on the page.
FIELDS = ("style", "mood", "lighting", "camera", "composition", "colour", "extras")

# The two generate modes, in the order the page renders their rows.
MODES = ("image", "video")


@lru_cache(maxsize=1)
def load_ideas() -> dict[str, dict[str, list[str]]]:
    """The shipped phrases, keyed by mode and then by builder field, in page order.

    Cached, so callers share one mapping and must treat it as read-only.
    """
    data = json.loads(IDEAS_PATH.read_text(encoding="utf-8"))
    return {mode: {k: list(data[mode][k]) for k in FIELDS} for mode in MODES}


def load_ideas_for(mode: str) -> dict[str, list[str]]:
    """Just one mode's phrases; anything but ``"video"`` reads as the image set."""
    return load_ideas()["video" if mode == "video" else "image"]
