"""网络类内置能力：抓取网页 / 联网搜索。

对应 Claude Code 的 WebFetch / WebSearch。

`web_fetch` 直接抓 URL，不需要任何凭据；`web_search` 需要一个搜索服务商的
API Key（Tavily / Serper / Bing 三选一），没配就明确告诉用户怎么配。
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.capabilities.context import ExecutionContext

_MAX_CONTENT = 50000
_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\n{3,}")


def _html_to_text(raw: str) -> str:
    """把 HTML 粗略地转成纯文本。"""
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _WHITESPACE.sub("\n\n", text)
    return text.strip()


def make_web_fetch(context: ExecutionContext) -> Callable[..., str]:
    """抓取网页内容。"""

    async def web_fetch(url: str) -> str:
        """抓取一个 URL 的内容并转成纯文本返回。

        只支持 http/https；返回的是网页正文，不含脚本与样式。
        """
        if not url.startswith(("http://", "https://")):
            return f"只支持 http/https 链接: {url}"
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "QiZhiDa-Agent/1.0"})
        except httpx.TimeoutException:
            return f"抓取超时: {url}"
        except Exception as exc:
            return f"抓取失败: {exc}"

        if response.status_code >= 400:
            return f"抓取失败: HTTP {response.status_code} {url}"

        content_type = response.headers.get("content-type", "")
        raw = response.text
        if "html" in content_type.lower() or raw.lstrip()[:100].lower().startswith("<!doctype"):
            text = _html_to_text(raw)
        else:
            text = raw
        if len(text) > _MAX_CONTENT:
            text = text[:_MAX_CONTENT] + f"... (内容过长，已截断，共 {len(text)} 字符)"
        return f"来源: {url}\n\n{text}"

    return web_fetch


def make_web_search(context: ExecutionContext) -> Callable[..., str]:
    """联网搜索。"""

    async def web_search(query: str, max_results: int = 5) -> str:
        """在互联网上搜索，返回标题、链接与摘要。

        需要配置搜索服务商的 API Key（WEB_SEARCH_PROVIDER + WEB_SEARCH_API_KEY）。
        """
        provider = (settings_web_provider() or "").strip().lower()
        api_key = settings_web_key()
        if not provider or not api_key:
            return (
                "联网搜索没有配置。请在 .env 里设置 WEB_SEARCH_PROVIDER"
                "（tavily / serper / bing）和 WEB_SEARCH_API_KEY。"
            )
        try:
            results = await _search(provider, api_key, query, max_results)
        except Exception as exc:
            return f"搜索失败: {exc}"
        if not results:
            return f"没有找到与 {query!r} 相关的结果"
        lines = []
        for index, item in enumerate(results, start=1):
            lines.append(
                f"{index}. {item.get('title', '')}\n"
                f"   {item.get('url', '')}\n"
                f"   {item.get('snippet', '')}"
            )
        return "\n".join(lines)

    return web_search


def settings_web_provider() -> str:
    from app.core.config import settings

    return settings.web_search_provider


def settings_web_key() -> str:
    from app.core.config import settings

    return settings.web_search_api_key


async def _search(
    provider: str, api_key: str, query: str, max_results: int
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20) as client:
        if provider == "tavily":
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                },
            )
            response.raise_for_status()
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
                for item in response.json().get("results", [])
            ]
        if provider == "serper":
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results},
            )
            response.raise_for_status()
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
                for item in response.json().get("organic", [])
            ]
        if provider == "bing":
            response = await client.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": api_key},
                params={"q": query, "count": max_results},
            )
            response.raise_for_status()
            pages = response.json().get("webPages", {}).get("value", [])
            return [
                {
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                }
                for item in pages
            ]
    raise ValueError(f"不支持的搜索服务商: {provider}")
