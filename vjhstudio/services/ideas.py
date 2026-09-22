"""The idea chips shipped with the app.

``data/prompt_ideas.json`` holds a short list of phrases for each builder field that
benefits from a nudge (Subject is the user's own idea, so it has none). The Generate page
renders the phrases as toggle buttons and also hands the whole mapping to Alpine as JSON;
toggling only ever rewrites the text of a field, so nothing here reaches the API.

Read once and cached, exactly like ``catalog.CURATED_PATH``'s curated seed file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

IDEAS_PATH = Path(__file__).resolve().parent.parent / "data" / "prompt_ideas.json"

# The builder fields that get a chip row, in the order they appear on the page.
FIELDS = ("style", "mood", "lighting", "camera", "composition", "colour", "extras")


@lru_cache(maxsize=1)
def load_ideas() -> dict[str, list[str]]:
    """The shipped phrases, keyed by builder field name, in page order.

    Cached, so callers share one mapping and must treat it as read-only.
    """
    data = json.loads(IDEAS_PATH.read_text(encoding="utf-8"))
    return {k: list(data[k]) for k in FIELDS}
