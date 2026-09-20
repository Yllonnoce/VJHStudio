"""RunWare public content catalog (https://content.runware.ai). No API key needed."""

from __future__ import annotations

import httpx

DEFAULT_BASE_URL = "https://content.runware.ai"


class ContentAPIError(Exception):
    pass


class ContentAPI:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self._client_kwargs = {
            "base_url": base_url,
            "timeout": timeout,
            "transport": transport,
            "headers": {"User-Agent": "VJHStudio"},
        }

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response | None:
        try:
            async with httpx.AsyncClient(**self._client_kwargs) as c:
                r = await c.get(path, params=params)
        except httpx.HTTPError as e:
            raise ContentAPIError(f"content API unreachable: {e}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ContentAPIError(f"content API {r.status_code} for {path}")
        return r

    async def list_models(
        self, category: str, status: str = "live", page_size: int = 100
    ) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            r = await self._get(
                "/models",
                {
                    "category": category,
                    "status": status,
                    "limit": page_size,
                    "offset": offset,
                    "paginate": "true",
                },
            )
            if r is None:
                break
            data = r.json()
            page = data["items"] if isinstance(data, dict) else data
            items.extend(page)
            total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
            offset += len(page)
            if not page or offset >= total:
                break
        return items

    async def get_pricing(self, model_id: str) -> dict | None:
        r = await self._get(f"/models/{model_id}/pricing")
        return None if r is None else r.json()
