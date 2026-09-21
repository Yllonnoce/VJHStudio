"""Deterministic prompt composition, plus the prompt library on top of it: dedupe by
content hash, auto-history for every submitted request, tags/favourites/search.

Mirrored byte-for-byte by static/js/app.js composePrompt (the ``compose`` function only)."""

from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Prompt, utcnow
from ..schemas.image import ImageRequest, PromptForm
from ..schemas.video import VideoRequest
from .assets import _like_escape, normalize_tags, tags_list

__all__ = [
    "FIELD_ORDER",
    "POLISH_JSON_MAX",
    "PROMPT_MAX",
    "NO_TEXT_SUFFIX",
    "NO_TEXT_NEGATIVE",
    "PER_PAGE",
    "clean",
    "compose",
    "build_negative",
    "cap",
    "apply_no_text",
    "final_prompt",
    "saved_texts",
    "parse_polish_json",
    "normalize_tags",
    "tags_list",
    "content_hash",
    "hash_of",
    "find_by_hash",
    "upsert",
    "for_request",
    "mark_used",
    "get",
    "list_prompts",
    "set_favourite",
    "set_tags",
    "set_title",
    "duplicate",
    "delete",
    "to_initial",
]

PER_PAGE = 30
POLISH_JSON_MAX = 8000

FIELD_ORDER = ("subject", "style", "mood", "lighting", "camera", "composition", "colour", "extras")
PROMPT_MAX = 2900
NO_TEXT_SUFFIX = (
    " Pure artwork only: absolutely no text, no words, no letters, no labels, no captions,"
    " no title, no watermark, no signature, no borders, no user interface elements."
)
NO_TEXT_NEGATIVE = (
    "text, words, letters, typography, writing, labels, captions, title, watermark,"
    " signature, logo, user interface, borders, frame, split image, collage"
)
_WS = re.compile(r"\s+")


def clean(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip().rstrip(" ,;.").strip()


def compose(form: PromptForm | dict) -> str:
    data = form.model_dump() if isinstance(form, PromptForm) else dict(form)
    parts: list[str] = []
    seen: set[str] = set()
    for key in FIELD_ORDER:
        v = clean(data.get(key))
        if v and v.lower() not in seen:
            parts.append(v)
            seen.add(v.lower())
    return ", ".join(parts)


def _tokens(text: str) -> list[str]:
    return [t for t in (clean(x) for x in (text or "").split(",")) if t]


def build_negative(form: PromptForm, default_negative: str, no_text: bool) -> str:
    toks = _tokens(form.negative)
    if form.use_default_negative:
        toks += _tokens(default_negative)
    if no_text:
        toks += _tokens(NO_TEXT_NEGATIVE)
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t.lower() not in seen:
            out.append(t)
            seen.add(t.lower())
    return ", ".join(out)


def cap(prompt: str, suffix: str = "") -> str:
    body = (prompt or "")[: PROMPT_MAX - len(suffix)]
    return body + suffix


def apply_no_text(prompt: str) -> str:
    return cap(prompt, NO_TEXT_SUFFIX)


def _final_text(form: PromptForm, final_raw: str = "") -> str:
    """A typed final prompt wins over the composed one, but is cleaned and capped (and
    given the no-text suffix) exactly the same way -- one implementation, so a saved
    prompt and a submitted one can never hash differently over the same text."""
    base = clean(final_raw) if final_raw else compose(form)
    return cap(base, NO_TEXT_SUFFIX if form.no_text else "")


def final_prompt(req: ImageRequest | VideoRequest) -> str:
    return _final_text(req.form, req.final_prompt or "")


def saved_texts(
    kind: str, form: PromptForm, final_raw: str = "", default_negative: str = ""
) -> tuple[str, str, str]:
    """``(composed, final, negative)`` for one prompt, however it arrives: the Save-prompt
    route and ``generate.enqueue_image``/``enqueue_video`` both go through here, so the
    three strings a ``content_hash`` is built from are produced by one piece of code.
    Video carries no negative at all (the request omits ``negativePrompt``)."""
    negative = "" if kind == "video" else build_negative(form, default_negative, form.no_text)
    return compose(form), _final_text(form, final_raw), negative


def parse_polish_json(raw: str) -> dict | None:
    """The hidden ``polish_json`` field as the polish cards stash it. Anything that is not
    a JSON object of a sane size is dropped: neither a save nor a submit may fail over
    polish metadata."""
    raw = (raw or "").strip()
    if not raw or len(raw) > POLISH_JSON_MAX:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---- prompt library -------------------------------------------------------


def _form_dict(form: PromptForm | dict) -> dict:
    return form.model_dump() if isinstance(form, PromptForm) else dict(form)


def content_hash(
    kind: str, composed: str, final: str, negative: str, form: PromptForm | dict
) -> str:
    """Dedupe key for a (kind, composed, final, negative, form) tuple, scoped by the
    caller to a project. ``form`` is canonicalised (dumped, empty strings dropped) so
    e.g. an unset ``camera`` doesn't distinguish two otherwise-identical prompts."""
    data = _form_dict(form)
    canonical = {k: v for k, v in data.items() if v != ""}
    payload = json.dumps(
        [kind, composed, final, negative, canonical], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_by_hash(session: Session, project_id: int | None, hash_: str) -> Prompt | None:
    """Lowest-id row for ``(project_id, content_hash)``, or ``None``. ``ix_prompts_hash``
    is deliberately not unique: ``duplicate()`` writes a second row with the same hash on
    purpose, and this ordering is what keeps ``upsert`` (and any future merge, e.g.
    Phase 6's archive import) finding the original rather than a duplicate that would
    otherwise shadow it."""
    return (
        session.execute(
            select(Prompt)
            .where(Prompt.project_id == project_id, Prompt.content_hash == hash_)
            .order_by(Prompt.id.asc())
        )
        .scalars()
        .first()
    )


def hash_of(prompt: Prompt) -> str:
    """``content_hash`` recomputed from a saved row's own stored columns -- never from
    live settings (e.g. the current ``defaults.negative_prompt``), which can differ
    machine-to-machine and would silently break a Phase 6 archive merge's dedupe."""
    return content_hash(
        prompt.kind,
        prompt.composed_prompt,
        prompt.final_prompt,
        prompt.negative_prompt,
        prompt.form_json or {},
    )


def upsert(
    session: Session,
    *,
    project_id: int | None,
    kind: str,
    title: str,
    form: PromptForm | dict,
    final_prompt: str,
    negative_prompt: str = "",
    tags: str = "",
    polish_json: dict | None = None,
    final_edited: bool = False,
) -> tuple[Prompt, bool]:
    """Write a ``Prompt`` row, or return the existing one for the same
    ``(project_id, content_hash)`` (lowest id wins) with ``created=False``: its tags are
    merged in, its ``polish_json`` overwritten only when a new one is given, and
    ``use_count``/``last_used_at`` are left untouched."""
    composed = compose(form)
    hash_ = content_hash(kind, composed, final_prompt, negative_prompt, form)
    existing = find_by_hash(session, project_id, hash_)
    if existing is not None:
        existing.tags = normalize_tags((existing.tags or "") + "," + (tags or ""))
        if polish_json is not None:
            existing.polish_json = polish_json
        session.flush()
        return existing, False

    prompt = Prompt(
        project_id=project_id,
        title=title,
        kind=kind,
        form_json=_form_dict(form),
        composed_prompt=composed,
        final_prompt=final_prompt,
        negative_prompt=negative_prompt,
        tags=normalize_tags(tags),
        content_hash=hash_,
        polish_json=polish_json,
        final_edited=final_edited,
    )
    session.add(prompt)
    session.flush()
    return prompt, True


def _default_title(req: ImageRequest | VideoRequest, composed: str) -> str:
    return req.title or composed[:60] or "Untitled"


def for_request(
    session: Session,
    req: ImageRequest | VideoRequest,
    *,
    kind: str,
    negative: str = "",
    title: str | None = None,
    tags: str = "",
    polish_json: dict | None = None,
) -> Prompt:
    """Auto-history for a submitted request: reuse ``req.prompt_id``'s row when it exists
    and its content hash still matches the request as submitted, otherwise ``upsert`` a
    row for it. Either way, ``mark_used`` bumps the row that was actually used."""
    composed = compose(req.form)
    final = final_prompt(req)
    hash_ = content_hash(kind, composed, final, negative, req.form)

    prompt: Prompt | None = None
    if req.prompt_id is not None:
        candidate = session.get(Prompt, req.prompt_id)
        if (
            candidate is not None
            and candidate.content_hash == hash_
            and candidate.project_id == req.project_id
        ):
            prompt = candidate
            # the row is reused as-is, but a polish blob the submit carries is new
            # information about it -- ``upsert`` treats an existing row the same way
            if polish_json is not None:
                prompt.polish_json = polish_json

    if prompt is None:
        prompt, _ = upsert(
            session,
            project_id=req.project_id,
            kind=kind,
            title=title if title is not None else _default_title(req, composed),
            form=req.form,
            final_prompt=final,
            negative_prompt=negative,
            tags=tags,
            polish_json=polish_json,
        )

    mark_used(session, prompt.id)
    return prompt


def mark_used(session: Session, prompt_id: int) -> Prompt | None:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        return None
    prompt.use_count += 1
    prompt.last_used_at = utcnow()
    session.flush()
    return prompt


def get(session: Session, prompt_id: int) -> Prompt | None:
    return session.get(Prompt, prompt_id)


def list_prompts(
    session: Session,
    *,
    q: str | None = None,
    kind: str | None = None,
    favourite: bool | None = None,
    project_id: int | None = None,
    tag: str | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> tuple[list[Prompt], int]:
    """Newest first; ``q`` matches ``title``/``composed_prompt``/``final_prompt``
    (escaped LIKE), ``tag`` matches the ``,a,b,`` column with ``LIKE '%,tag,%'``."""
    filters = []
    if kind:
        filters.append(Prompt.kind == kind)
    if favourite is not None:
        filters.append(Prompt.is_favourite == favourite)
    if project_id is not None:
        filters.append(Prompt.project_id == project_id)
    if tag:
        pattern = f"%,{_like_escape(tag.strip().lower())},%"
        filters.append(Prompt.tags.like(pattern, escape="\\"))
    if q:
        pattern = f"%{_like_escape(q.strip())}%"
        filters.append(
            or_(
                Prompt.title.like(pattern, escape="\\"),
                Prompt.composed_prompt.like(pattern, escape="\\"),
                Prompt.final_prompt.like(pattern, escape="\\"),
            )
        )
    total = int(session.execute(select(func.count(Prompt.id)).where(*filters)).scalar() or 0)
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    rows = session.execute(
        select(Prompt)
        .where(*filters)
        .order_by(Prompt.created_at.desc(), Prompt.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).scalars()
    return list(rows), total


def set_favourite(session: Session, prompt_id: int, value: bool | None = None) -> Prompt:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise LookupError(prompt_id)
    prompt.is_favourite = (not prompt.is_favourite) if value is None else bool(value)
    session.flush()
    return prompt


def set_tags(session: Session, prompt_id: int, tags: str) -> Prompt:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise LookupError(prompt_id)
    prompt.tags = normalize_tags(tags)
    session.flush()
    return prompt


def set_title(session: Session, prompt_id: int, title: str) -> Prompt:
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        raise LookupError(prompt_id)
    prompt.title = title
    session.flush()
    return prompt


def duplicate(session: Session, prompt_id: int) -> Prompt:
    """Copy ``prompt_id`` into a new row with the same content and hash, deliberately
    bypassing dedupe: ``upsert`` of that same text keeps finding the lowest-id row (the
    original), never a duplicate that shadows it."""
    source = session.get(Prompt, prompt_id)
    if source is None:
        raise LookupError(prompt_id)
    copy = Prompt(
        project_id=source.project_id,
        title=f"{source.title} (copy)",
        kind=source.kind,
        form_json=dict(source.form_json or {}),
        composed_prompt=source.composed_prompt,
        final_prompt=source.final_prompt,
        negative_prompt=source.negative_prompt,
        tags=source.tags,
        content_hash=source.content_hash,
        polish_json=source.polish_json,
        final_edited=source.final_edited,
        use_count=0,
        last_used_at=None,
        is_favourite=False,
    )
    session.add(copy)
    session.flush()
    return copy


def delete(session: Session, prompt_id: int) -> bool:
    """Delete the prompt row. Jobs keep their history: ``jobs.prompt_id`` is an
    ``ON DELETE SET NULL`` foreign key, so a job that referenced this prompt survives."""
    prompt = session.get(Prompt, prompt_id)
    if prompt is None:
        return False
    session.delete(prompt)
    session.flush()
    return True


def to_initial(p: Prompt) -> dict:
    """The exact shape ``_page``/``generateForm`` already consume for a prefill."""
    return {
        "form": dict(p.form_json or {}),
        "final_prompt": p.final_prompt,
        "prompt_id": p.id,
        "project_id": p.project_id,
        "title": p.title,
        "mode": p.kind,
    }
