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


def parse_items(rows: list[dict]) -> list[ResultItem]:
    out: list[ResultItem] = []
    for r in rows or []:
        url = r.get("imageURL") or r.get("videoURL") or r.get("url")
        if not url:
            continue
        cost = r.get("cost")
        out.append(
            ResultItem(
                url=str(url),
                seed=r.get("seed"),
                cost=float(cost) if cost is not None else None,
                uuid=r.get("imageUUID") or r.get("videoUUID"),
                nsfw=bool(r.get("NSFWContent", False)),
                raw=r,
            )
        )
    return out
