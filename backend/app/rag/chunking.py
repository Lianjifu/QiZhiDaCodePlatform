"""分块：把一份文档切成检索单元。

切法随内容类型变，因为不同内容的"语义单元"不一样：

- **Markdown**：按标题切，标题路径写进 meta —— 检索到"配置"这一节时，
  能同时告诉模型它在《部署指南》的《数据库配置》下面；
- **代码**：按顶层定义切（函数 / 类），一个定义一块，装饰器和 docstring 跟着走；
- **纯文本**：固定窗口 + 重叠，按段落边界对齐。

分块是 RAG 里最影响效果的一步：切太大，检索不准且浪费上下文；切太小，
一句话的前后关系断了。三种切法都保证**不丢内容**（拼回去等于原文，忽略空白）。

块大小按**字符数**算而不是 token 数 —— 分块发生在调模型之前，用不着先分词，
省一次开销。中文和英文在"一个 token 多少字符"上差很多，所以 token 数是
估算值，只用于展示和截断兜底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150

# 拉丁词（含数字、下划线）
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# 一段连续的中文
_CJK_RUN_RE = re.compile("[㐀-䶿一-鿿]+")
# 单个中文字符
_CJK_CHAR_RE = re.compile("[㐀-䶿一-鿿]")
# Markdown 标题
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# 代码里的顶层定义
_CODE_DEF_RE = re.compile(
    r"^(?:@[A-Za-z_][\w.]*"
    r"|(?:async[ \t]+)?def[ \t]+"
    r"|class[ \t]+"
    r"|(?:export[ \t]+)?(?:async[ \t]+)?function[ \t]*\*)",
    re.MULTILINE,
)

FORMAT_TEXT = "text"
FORMAT_MARKDOWN = "markdown"
FORMAT_CODE = "code"


@dataclass(slots=True)
class Chunk:
    """一个检索单元。"""

    content: str
    seq: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "seq": self.seq,
            "meta": dict(self.meta),
            "token_count": self.token_count,
        }


def estimate_tokens(text: str) -> int:
    """估算 token 数：中文一字一 token，其余按 4 字符一 token。"""
    if not text:
        return 0
    cjk = len(_CJK_CHAR_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def detect_format(text: str, *, filename: str = "") -> str:
    """猜内容类型。

    显式给的文件后缀优先；否则按内容特征判断 —— 有围栏代码块或标题的多半是
    Markdown，出现顶层 def/class 的多半是代码。
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in {"md", "markdown", "mdx"}:
        return FORMAT_MARKDOWN
    if suffix in {"py", "js", "ts", "tsx", "jsx", "go", "java", "rs", "rb", "c", "cpp", "h"}:
        return FORMAT_CODE
    if suffix in {"txt", "log", "rst"}:
        return FORMAT_TEXT

    if _HEADING_RE.search(text) or "```" in text:
        return FORMAT_MARKDOWN
    if _CODE_DEF_RE.search(text):
        return FORMAT_CODE
    return FORMAT_TEXT


# ---------- 纯文本：固定窗口 + 重叠 ----------


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    meta: dict[str, Any] | None = None,
) -> list[Chunk]:
    """按固定窗口切，窗口之间留重叠。

    切点尽量落在段落边界上 —— 段落被拦腰截断的话，两半都读不懂。
    """
    text = (text or "").strip()
    if not text:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")

    base = dict(meta or {})
    paragraphs = _split_paragraphs(text)
    chunks: list[Chunk] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        body = buf.strip()
        if not body:
            buf = ""
            return
        chunks.append(
            Chunk(
                content=body,
                seq=len(chunks),
                meta={**base, "format": FORMAT_TEXT, "position": len(chunks)},
                token_count=estimate_tokens(body),
            )
        )
        buf = ""

    for para in paragraphs:
        # 单段就超长：按窗口硬切
        if len(para) > max_chars:
            flush()
            for piece in _hard_split(para, max_chars):
                chunks.append(
                    Chunk(
                        content=piece,
                        seq=len(chunks),
                        meta={
                            **base,
                            "format": FORMAT_TEXT,
                            "position": len(chunks),
                            "split": "hard",
                        },
                        token_count=estimate_tokens(piece),
                    )
                )
            continue
        if not buf:
            buf = para
            continue
        if len(buf) + len(para) + 1 > max_chars:
            flush()
            buf = para
        else:
            buf = f"{buf}\n\n{para}"

    flush()
    return _apply_overlap(chunks, max_chars, overlap, base)


def _split_paragraphs(text: str) -> list[str]:
    """按空行切段；单段内部再按换行切一次（代码块里的多行）。"""
    parts: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip("\n")
        if not block.strip():
            continue
        parts.append(block)
    return parts


def _hard_split(text: str, max_chars: int) -> list[str]:
    """超长单段的硬切：按行切，还超长就按字符切。"""
    pieces: list[str] = []
    buf = ""
    for line in text.splitlines():
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) > max_chars and buf:
            pieces.append(buf)
            buf = line
        else:
            buf = candidate
        while len(buf) > max_chars:
            pieces.append(buf[:max_chars])
            buf = buf[max_chars:]
    if buf.strip():
        pieces.append(buf)
    return pieces


def _apply_overlap(
    chunks: list[Chunk],
    max_chars: int,
    overlap: int,
    base: dict[str, Any],
) -> list[Chunk]:
    """给相邻块补重叠内容。

    重叠是"上一块的尾巴"，用来保住跨块的句子。实现上直接按段落回填：
    从上一块末尾往前找段落，攒够 overlap 就停。
    """
    if overlap <= 0 or len(chunks) < 2:
        return chunks

    for index in range(1, len(chunks)):
        current = chunks[index].content
        if len(current) >= max_chars:
            continue
        previous = chunks[index - 1].content
        tail = _tail_overlap(previous, overlap)
        if not tail or current.startswith(tail):
            continue
        merged = f"{tail}\n\n{current}"
        if len(merged) > max_chars:
            continue
        chunks[index].content = merged
        chunks[index].token_count = estimate_tokens(merged)
        chunks[index].meta = {**base, **chunks[index].meta, "overlap": True}
    return chunks


def _tail_overlap(text: str, overlap: int) -> str:
    """取一段文本末尾约 overlap 字符，尽量按段落边界。"""
    if len(text) <= overlap:
        return text
    window = text[-overlap:]
    for sep in ("\n\n", "\n"):
        idx = window.find(sep)
        if idx >= 0 and idx + len(sep) < len(window):
            return window[idx + len(sep) :]
    return window


# ---------- Markdown：按标题切 ----------


def chunk_markdown(
    text: str,
    *,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    meta: dict[str, Any] | None = None,
) -> list[Chunk]:
    """按标题层级切块，标题路径进 meta。

    一节太长就再按段落切（不重叠 —— Markdown 的标题已经提供了定位线索，
    再叠一层反而让标题重复出现）。
    """
    text = (text or "").strip()
    if not text:
        return []

    base = dict(meta or {})
    chunks: list[Chunk] = []

    for heading, level, body, section_path in _markdown_sections(text):
        full_path = section_path or []
        section_meta = {
            **base,
            "format": FORMAT_MARKDOWN,
            "heading": heading,
            "level": level,
            "path": full_path,
            "position": len(chunks),
        }
        body = body.strip()
        if not body:
            # 只有标题没有正文：留一个短块，标题本身也是检索线索
            chunks.append(
                Chunk(
                    content="#" * level + " " + heading,
                    seq=len(chunks),
                    meta={**section_meta, "empty": True},
                    token_count=estimate_tokens(heading),
                )
            )
            continue
        if len(body) <= max_chars:
            chunks.append(
                Chunk(
                    content=body,
                    seq=len(chunks),
                    meta=section_meta,
                    token_count=estimate_tokens(body),
                )
            )
            continue
        for para in _split_paragraphs(body):
            if len(para) > max_chars:
                for piece in _hard_split(para, max_chars):
                    chunks.append(
                        Chunk(
                            content=piece,
                            seq=len(chunks),
                            meta={**section_meta, "position": len(chunks), "split": "hard"},
                            token_count=estimate_tokens(piece),
                        )
                    )
                continue
            chunks.append(
                Chunk(
                    content=para,
                    seq=len(chunks),
                    meta={**section_meta, "position": len(chunks)},
                    token_count=estimate_tokens(para),
                )
            )
    return chunks


def _markdown_sections(
    text: str,
) -> list[tuple[str, int, str, list[str]]]:
    """把 Markdown 拆成 ``(标题, 层级, 正文, 标题路径)`` 序列。"""
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, int, str, list[str]]] = []
    path: list[tuple[int, str]] = []

    if not matches or matches[0].start() > 0:
        preamble = text[: matches[0].start()] if matches else text
        if preamble.strip():
            sections.append(("", 0, preamble, []))

    for index, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        # 维护标题栈：同层或更高级的标题把旧的挤掉
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, heading))
        sections.append((heading, level, text[start:end], [t for _, t in path]))
    return sections


# ---------- 代码：按顶层定义切 ----------


def chunk_code(
    text: str,
    *,
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    meta: dict[str, Any] | None = None,
) -> list[Chunk]:
    """按顶层定义切块。

    切点落在"新定义开始"的位置上，所以上一块的结尾是完整的定义边界，
    不会把函数体劈成两半。文件头（import、模块注释）单独成块。
    """
    text = (text or "").strip()
    if not text:
        return []

    base = dict(meta or {})
    chunks: list[Chunk] = []
    boundaries = _code_boundaries(text)
    if not boundaries:
        return chunk_text(text, max_chars=max_chars, overlap=overlap, meta=base)

    head = text[: boundaries[0].start()].strip()
    if head:
        chunks.append(
            Chunk(
                content=head,
                seq=len(chunks),
                meta={**base, "format": FORMAT_CODE, "kind": "header", "position": len(chunks)},
                token_count=estimate_tokens(head),
            )
        )

    for index, match in enumerate(boundaries):
        start = match.start()
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        name = _definition_name(text, match.start())
        pieces = [body] if len(body) <= max_chars else _hard_split(body, max_chars)
        for piece in pieces:
            chunks.append(
                Chunk(
                    content=piece,
                    seq=len(chunks),
                    meta={
                        **base,
                        "format": FORMAT_CODE,
                        "kind": "definition",
                        "symbol": name,
                        "position": len(chunks),
                    },
                    token_count=estimate_tokens(piece),
                )
            )
    return chunks


def _code_boundaries(text: str) -> list[re.Match]:
    """代码的切点。

    装饰器和它后面的定义算**同一块** —— 装饰器单独成块的话，
    检索到装饰器却看不到被装饰的函数，等于没用。
    """
    matches = list(_CODE_DEF_RE.finditer(text))
    boundaries: list[re.Match] = []
    skip_next = False
    for index, match in enumerate(matches):
        if skip_next:
            skip_next = False
            continue
        if _line_at(text, match.start()).lstrip().startswith("@"):
            following = matches[index + 1] if index + 1 < len(matches) else None
            if following is not None and _line_at(text, following.start()).lstrip().startswith(
                ("def ", "async def ", "class ")
            ):
                skip_next = True
        boundaries.append(match)
    return boundaries


def _line_at(text: str, start: int) -> str:
    """取 start 所在的那一行。"""
    end = text.find("\n", start)
    return text[start:] if end < 0 else text[start:end]


def _definition_name(text: str, start: int) -> str:
    """从定义所在的那一行里取出符号名。

    装饰器单独成块时名字在它后面的定义上，所以往后找第一个 def / class。
    """
    line = _line_at(text, start).lstrip()
    if line.startswith("@"):
        found = re.search(
            r"^(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)|^[ \t]*class[ \t]+([A-Za-z_]\w*)",
            text[start:],
            re.MULTILINE,
        )
        if found:
            return found.group(1) or found.group(2)
        return line[:40]
    found = re.match(r"(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)|class[ \t]+([A-Za-z_]\w*)", line)
    if found:
        return found.group(1) or found.group(2)
    return line[:40]


# ---------- 入口 ----------


def chunk_document(
    text: str,
    *,
    fmt: str = "auto",
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    filename: str = "",
    meta: dict[str, Any] | None = None,
) -> list[Chunk]:
    """按内容类型分块。``fmt="auto"`` 时自动判断类型。"""
    resolved = detect_format(text, filename=filename) if fmt == "auto" else fmt
    if resolved == FORMAT_MARKDOWN:
        chunks = chunk_markdown(text, max_chars=max_chars, overlap=overlap, meta=meta)
    elif resolved == FORMAT_CODE:
        chunks = chunk_code(text, max_chars=max_chars, overlap=overlap, meta=meta)
    else:
        chunks = chunk_text(text, max_chars=max_chars, overlap=overlap, meta=meta)

    for index, chunk in enumerate(chunks):
        chunk.seq = index
        chunk.meta.setdefault("format", resolved)
        chunk.meta.setdefault("position", index)
        chunk.token_count = chunk.token_count or estimate_tokens(chunk.content)
    return chunks


__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "FORMAT_CODE",
    "FORMAT_MARKDOWN",
    "FORMAT_TEXT",
    "Chunk",
    "chunk_code",
    "chunk_document",
    "chunk_markdown",
    "chunk_text",
    "detect_format",
    "estimate_tokens",
]
