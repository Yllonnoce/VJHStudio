"""Pure size math for model constraints: no project imports. Shared by
``vjhstudio/services/constraints.py`` and (later) the runner's size-correction retry."""

from __future__ import annotations

Size = tuple[int, int]


def snap_to_rule(w: int, h: int, rule: dict) -> Size:
    """Clamp into ``[min, max]`` then round to the nearest multiple of ``step`` counted
    from ``min`` (ties round up), matching RunWare's "between A and B, in multiples of
    N" rejection text."""
    lo, hi, step = (
        int(rule.get("min") or 1),
        int(rule.get("max") or 10**6),
        int(rule.get("step") or 1),
    )

    def one(v: int) -> int:
        v = max(lo, min(hi, int(v)))
        q, r = divmod(v - lo, step)
        if r * 2 >= step:
            q += 1
        return max(lo, min(hi, lo + q * step))

    return one(w), one(h)


def nearest_size_in(dims: dict, w: int, h: int) -> Size:
    d = dims or {}
    if d.get("mode") == "list" and d.get("list"):
        want = w / h if h else 1.0
        return min(
            ((int(a), int(b)) for a, b in d["list"]),
            key=lambda s: (abs((s[0] / s[1]) - want), abs(s[0] * s[1] - w * h)),
        )
    if d.get("mode") == "rule":
        return snap_to_rule(w, h, d)
    return int(w), int(h)
