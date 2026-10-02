from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResultItem:
    url: str
    seed: int | None
    cost: float | None
    uuid: str | None
    nsfw: bool
    raw: dict


@dataclass
class TaskResult:
    items: list[ResultItem]
    task_uuid: str
    task_sent: dict
    dropped: list[dict] = field(default_factory=list)
    attempts: int = 1
    duration_ms: int | None = None  # the *successful* attempt alone, backoff excluded
    rows: list[dict] = field(default_factory=list)  # the raw reply; text tasks carry no URL


_MODEL_SUFFIXES = (".glb", ".fbx", ".gltf", ".obj", ".usdz", ".stl")


def _urls(node: object) -> list[str]:
    """Every http(s) string anywhere inside a reply fragment, in document order."""
    if isinstance(node, str):
        return [node] if node.startswith(("http://", "https://")) else []
    if isinstance(node, dict):
        return [u for v in node.values() for u in _urls(v)]
    if isinstance(node, (list, tuple)):
        return [u for v in node for u in _urls(v)]
    return []


def outputs_url(outputs: object) -> str | None:
    """The file URL inside a ``3dInference`` row's ``outputs`` object. Its inner shape is
    not documented (a ``files`` list, a key per format, …), so it is walked rather than
    indexed: the first URL whose path ends like a 3D model file wins, else the first URL
    of any kind (a preview image would be wrong, but nothing at all would be worse)."""
    urls = _urls(outputs)
    for u in urls:
        if u.split("?", 1)[0].lower().endswith(_MODEL_SUFFIXES):
            return u
    return urls[0] if urls else None


def parse_items(rows: list[dict]) -> list[ResultItem]:
    out: list[ResultItem] = []
    for r in rows or []:
        url = (
            r.get("imageURL")
            or r.get("videoURL")
            or r.get("audioURL")
            or r.get("url")
            or outputs_url(r.get("outputs"))
        )
        if not url:
            continue
        cost = r.get("cost")
        out.append(
            ResultItem(
                url=str(url),
                seed=r.get("seed"),
                cost=float(cost) if cost is not None else None,
                uuid=r.get("imageUUID") or r.get("videoUUID") or r.get("audioUUID"),
                nsfw=bool(r.get("NSFWContent", False)),
                raw=r,
            )
        )
    return out
