from typing import Any

import httpx

from app import config


class OpenNotebookError(Exception):
    """Raised when an OpenNotebook API call fails."""


class OpenNotebookClient:
    """Thin async client for the OpenNotebook REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or config.ON_BASE_URL).rstrip("/")
        self.timeout = timeout or config.ON_TIMEOUT
        self.headers: dict[str, str] = {}
        token = token if token is not None else config.ON_API_TOKEN
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=self.headers
            ) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPError as e:
            raise OpenNotebookError(f"OpenNotebook API call failed ({url}): {e}") from e

    async def list_sources(self, limit: int = 100) -> list[dict[str, Any]]:
        """List sources (id/title/type) for intent resolution in chat."""
        response = await self._request(
            "GET", "/api/sources", params={"limit": limit, "sort_by": "updated"}
        )
        return response.json()

    async def get_source(self, source_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/api/sources/{source_id}")
        return response.json()

    async def download_source(self, source_id: str) -> bytes:
        response = await self._request("GET", f"/api/sources/{source_id}/download")
        return response.content

    async def create_insight(
        self, source_id: str, insight_type: str, content: str
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/insights",
            json={
                "source_id": source_id,
                "insight_type": insight_type,
                "content": content,
            },
        )
        return response.json()
