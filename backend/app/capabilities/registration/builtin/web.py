"""网络类内置能力：抓取网页 / 联网搜索。

对应 Claude Code 的 WebFetch / WebSearch。

`web_fetch` 直接抓 URL，不需要任何凭据；`web_search` 需要一个搜索服务商的
API Key（Tavily / Serper / Bing 三选一），没配就明确告诉用户怎么配。

两条都属 ``network`` 风险、都要人工确认（见 ``policy.approval_decision``）。
另外 ``web_fetch`` 的 URL 会过一遍参数级风险规则：指向内网地址的 URL 被判成
SSRF 尝试，风险顶到 ``destructive``（见 ``execution/risk.py``）。
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext

# 网页正文的最大字符数：长页面截断，避免撑爆模型上下文
_MAX_CONTENT = 50000
# 先去掉 script/style 整块（里面全是噪音），再去掉零散标签
_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
# 连续三个以上换行压成两个
_WHITESPACE = re.compile(r"\n{3,}")


def _html_to_text(raw: str) -> str:
    """把 HTML 粗略地转成纯文本。

    不是完整解析器（不引第三方依赖），但足够把网页正文交给模型看：
    去脚本、去标签、反转义、逐行去空白、压缩空行。
    """
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _WHITESPACE.sub("\n\n", text)
    return text.strip()


def make_web_fetch(context: ExecutionContext) -> Callable[..., Any]:
    """构造"抓取网页"能力。"""

    async def web_fetch(url: str) -> dict[str, Any]:
        """抓取一个 URL 的内容并转成纯文本返回。

        只支持 http/https；返回的是网页正文，不含脚本与样式。

        Args:
            url: 要抓取的地址。

        Returns:
            ``content`` 是"来源 + 正文"的文本（给模型看），``url`` / ``status`` /
            ``bytes`` 给程序看。

        Raises:
            CapabilityError: 协议不对（``INVALID_ARGUMENTS``）、抓取超时或失败
                （``NETWORK_ERROR``，可重试）、HTTP 4xx/5xx（``HTTP_ERROR``）。
        """
        if not url.startswith(("http://", "https://")):
            raise CapabilityError(
                f"只支持 http/https 链接: {url}", code=ErrorCode.INVALID_ARGUMENTS
            )
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "QiZhiDa-Agent/1.0"})
        except httpx.TimeoutException as exc:
            raise CapabilityError(
                f"抓取超时: {url}", code=ErrorCode.NETWORK_ERROR, retryable=True
            ) from exc
        except Exception as exc:
            raise CapabilityError(
                f"抓取失败: {exc}", code=ErrorCode.NETWORK_ERROR, retryable=True
            ) from exc

        if response.status_code >= 400:
            raise CapabilityError(
                f"抓取失败: HTTP {response.status_code} {url}",
                code=ErrorCode.HTTP_ERROR,
                retryable=True,
                status=response.status_code,
            )

        content_type = response.headers.get("content-type", "")
        raw = response.text
        # 有些站点不返回 content-type，只能靠开头是不是 doctype 猜
        if "html" in content_type.lower() or raw.lstrip()[:100].lower().startswith("<!doctype"):
            text = _html_to_text(raw)
        else:
            text = raw
        if len(text) > _MAX_CONTENT:
            text = text[:_MAX_CONTENT] + f"... (内容过长，已截断，共 {len(text)} 字符)"
        return {
            "content": f"来源: {url}\n\n{text}",
            "url": url,
            "status": response.status_code,
            "bytes": len(raw.encode("utf-8")),
        }

    return web_fetch


def make_web_search(context: ExecutionContext) -> Callable[..., Any]:
    """构造"联网搜索"能力。"""

    async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
        """在互联网上搜索，返回标题、链接与摘要。

        需要配置搜索服务商的 API Key（WEB_SEARCH_PROVIDER + WEB_SEARCH_API_KEY）。

        Args:
            query: 搜索关键词。
            max_results: 最多返回几条结果。

        Returns:
            ``content`` 是编号后的结果列表文本，``query`` / ``results`` 是结构化字段。

        Raises:
            CapabilityError: 没配搜索服务商（明确告诉用户怎么配，不静默失败）、
                搜索失败（``NETWORK_ERROR``，可重试）、或服务商不支持。
        """
        provider = (settings_web_provider() or "").strip().lower()
        api_key = settings_web_key()
        if not provider or not api_key:
            # 不静默失败：缺配置是环境问题，重试多少次都没用
            raise CapabilityError(
                "联网搜索没有配置。请在 .env 里设置 WEB_SEARCH_PROVIDER"
                "（tavily / serper / bing）和 WEB_SEARCH_API_KEY。",
                code=ErrorCode.EXECUTION_ERROR,
            )
        try:
            results = await _search(provider, api_key, query, max_results)
        except CapabilityError:
            raise
        except Exception as exc:
            raise CapabilityError(
                f"搜索失败: {exc}", code=ErrorCode.NETWORK_ERROR, retryable=True
            ) from exc
        if not results:
            return {
                "content": f"没有找到与 {query!r} 相关的结果",
                "query": query,
                "results": [],
            }
        lines = []
        for index, item in enumerate(results, start=1):
            lines.append(
                f"{index}. {item.get('title', '')}\n"
                f"   {item.get('url', '')}\n"
                f"   {item.get('snippet', '')}"
            )
        return {
            "content": "\n".join(lines),
            "query": query,
            "results": results,
        }

    return web_search


def settings_web_provider() -> str:
    """读搜索服务商配置（延迟导入，避免模块加载期就拉起配置）。"""
    from app.core.config import settings

    return settings.web_search_provider


def settings_web_key() -> str:
    """读搜索 API Key（延迟导入同上）。"""
    from app.core.config import settings

    return settings.web_search_api_key


async def _search(
    provider: str, api_key: str, query: str, max_results: int
) -> list[dict[str, Any]]:
    """调具体的搜索服务商，把各家不同的返回结构**归一成同一套**。

    归一之后上层（模型、程序）只认 title / url / snippet 三个字段，
    换服务商不用改调用方。

    Args:
        provider: 服务商名（tavily / serper / bing）。
        api_key: 服务商的 API Key。
        query: 搜索关键词。
        max_results: 最多返回几条。

    Returns:
        归一后的结果列表。

    Raises:
        CapabilityError: 服务商不支持，或请求失败（``raise_for_status`` 抛出）。
    """
    async with httpx.AsyncClient(timeout=20) as client:
        if provider == "tavily":
            # Tavily 用 body 传 key
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
            # Serper 用请求头传 key，字段名是 q / num
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
            # Bing 用订阅 key 请求头，字段名是 q / count
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
    raise CapabilityError(f"不支持的搜索服务商: {provider}", code=ErrorCode.INVALID_ARGUMENTS)
