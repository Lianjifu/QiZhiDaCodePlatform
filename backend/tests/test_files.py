"""文件上传与对话里的多模态输入。

覆盖这几条最容易出问题的地方：

- **上传件落在工作区里** —— agent 用现成的文件工具就能读，不新增能力；
- **文件名清洗** —— 用户传 ``../../etc/passwd`` 不能写到工作区外面；
- **路径穿越被拒** —— 下载/删除接口的 ``path`` 参数不能逃出工作区；
- **图片内联的三道闸** —— 模型不支持看图、图片太大、文件其实不是图片，
  都不能内联（内联错了整轮对话会直接报错）；
- **图片进了消息** —— 模型这一轮就能"看见"，不用多一次工具往返。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.agents.runner import _input_messages, _message_text
from app.core.exceptions import NotFoundError, ValidationError
from app.core.workspace import resolve_workspace
from app.files.attachments import build_attachments, build_uploads_note, sniff_image_mime
from app.files.service import FileService
from app.files.storage import sanitize_filename

# 1×1 的红色 PNG
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


# ---------- 上传 ----------


def test_upload_lands_in_uploads_dir(workspace: Path) -> None:
    """上传件落进工作区的 uploads/，并返回虚拟路径。"""
    service = FileService(workspace)
    result = service.upload("需求.pdf", b"%PDF-1.4 hello")

    assert result.path.startswith("/uploads/")
    stored = workspace / "uploads" / result.filename
    assert stored.read_bytes() == b"%PDF-1.4 hello"
    assert result.size == len(b"%PDF-1.4 hello")
    assert result.sha256


def test_upload_sanitizes_filename(workspace: Path) -> None:
    """文件名里的路径成分要被洗掉 —— 不能写到工作区外面。"""
    service = FileService(workspace)
    result = service.upload("../../etc/passwd", b"x")

    stored = workspace / "uploads" / result.filename
    assert stored.exists()
    assert ".." not in result.filename
    assert stored.resolve().is_relative_to(workspace.resolve())


def test_sanitize_filename_strips_paths() -> None:
    """各种脏文件名都要洗成安全的 basename。"""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("a/b/c.png") == "c.png"
    assert sanitize_filename("..") == ""
    assert sanitize_filename("a\x00b.txt") == "a_b.txt"


def test_unique_filename_avoids_collision(workspace: Path) -> None:
    """同名文件传两次不能互相覆盖 —— 时间戳只到秒，同一秒内也要能区分。"""
    service = FileService(workspace)
    first = service.upload("a.txt", b"first")
    second = service.upload("a.txt", b"second")

    assert first.filename != second.filename
    assert (workspace / "uploads" / first.filename).read_bytes() == b"first"
    assert (workspace / "uploads" / second.filename).read_bytes() == b"second"


def test_upload_rejects_empty_and_oversized(workspace: Path) -> None:
    """空文件与超限文件都要明确拒绝。"""
    service = FileService(workspace, max_upload_bytes=10)
    with pytest.raises(ValidationError):
        service.upload("a.txt", b"")
    with pytest.raises(ValidationError):
        service.upload("a.txt", b"x" * 11)


# ---------- 读取 / 删除 ----------


def test_read_and_delete(workspace: Path) -> None:
    """下载拿得到内容，删了就没了。"""
    service = FileService(workspace)
    result = service.upload("a.txt", b"hello")

    assert service.read(result.path) == b"hello"
    content, name, mime = service.download(result.path)
    assert content == b"hello"
    assert name == result.filename
    assert mime.startswith("text/")

    service.delete(result.path)
    with pytest.raises(NotFoundError):
        service.read(result.path)


def test_path_traversal_rejected(workspace: Path) -> None:
    """越界路径必须被拒 —— 不能读到系统文件。"""
    service = FileService(workspace)
    with pytest.raises(ValidationError):
        service.read("/../../etc/passwd")


def test_list_includes_uploads_and_outputs(workspace: Path) -> None:
    """列表里有上传件，也有 agent 写出来的产物。"""
    service = FileService(workspace)
    service.upload("a.txt", b"hello")
    (workspace / "out.py").write_text("x", encoding="utf-8")

    paths = {entry.path for entry in service.list_files()}
    assert any(p.startswith("/uploads/") for p in paths)
    assert "/out.py" in paths


# ---------- 图片内联的三道闸 ----------


def test_image_inlined_when_vision_enabled(workspace: Path) -> None:
    """模型能看图 → 图片内联进消息。"""
    service = FileService(workspace)
    uploaded = service.upload("a.png", PNG_BYTES)

    blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=True,
        max_inline_bytes=1024 * 1024,
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "image"
    assert blocks[0]["mime_type"] == "image/png"
    assert base64.b64decode(blocks[0]["base64"]) == PNG_BYTES
    assert parsed[0].inlined is True


def test_image_not_inlined_when_vision_disabled(workspace: Path) -> None:
    """模型看不了图 → 不内联，降级成只给路径。

    内联过去模型会直接报错 —— 降级只是少一条能力，代价小得多。
    """
    service = FileService(workspace)
    uploaded = service.upload("a.png", PNG_BYTES)

    blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=False,
        max_inline_bytes=1024 * 1024,
    )

    assert blocks == []
    assert parsed[0].inlined is False
    assert "看图" in parsed[0].reason


def test_oversized_image_not_inlined(workspace: Path) -> None:
    """图片太大 → 不内联。base64 内联会让 checkpoint 明显膨胀。"""
    service = FileService(workspace)
    uploaded = service.upload("a.png", PNG_BYTES)

    blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=True,
        max_inline_bytes=4,
    )

    assert blocks == []
    assert parsed[0].inlined is False
    assert "超过" in parsed[0].reason


def test_fake_image_not_inlined(workspace: Path) -> None:
    """扩展名会骗人 —— 按文件头判断，不是图片就不内联。"""
    service = FileService(workspace)
    uploaded = service.upload("fake.png", "这不是图片，是一段文字".encode())

    blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=True,
        max_inline_bytes=1024 * 1024,
    )

    assert blocks == []
    assert parsed[0].is_image is False


def test_non_image_file_is_not_inlined(workspace: Path) -> None:
    """pdf 这类文件模型看不见，只给路径让 agent 自己读。"""
    service = FileService(workspace)
    uploaded = service.upload("doc.pdf", b"%PDF-1.4 x")

    blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=True,
        max_inline_bytes=1024 * 1024,
    )

    assert blocks == []
    assert parsed[0].is_image is False


def test_missing_attachment_is_skipped(workspace: Path) -> None:
    """附件读不到不该让整轮对话失败。"""
    blocks, parsed = build_attachments(
        workspace,
        ["/uploads/nope.png"],
        vision_enabled=True,
        max_inline_bytes=1024 * 1024,
    )

    assert blocks == []
    assert parsed == []


def test_sniff_image_mime() -> None:
    """文件头识别。"""
    assert sniff_image_mime(PNG_BYTES) == "image/png"
    assert sniff_image_mime(b"plain text") is None


# ---------- 只读文件头 ----------


def test_read_head_returns_only_a_few_bytes(workspace: Path) -> None:
    """判断是不是图片只看文件头 —— 不用把整个文件读进内存。"""
    service = FileService(workspace)
    uploaded = service.upload("big.bin", b"x" * 5000)

    assert len(service.read_head(uploaded.path, 32)) == 32


def test_stat_reports_size(workspace: Path) -> None:
    """stat 给大小、是否目录、修改时间。"""
    service = FileService(workspace)
    uploaded = service.upload("a.txt", b"hello")

    info = service.stat(uploaded.path)
    assert info.size == 5
    assert info.is_dir is False


def test_stat_rejects_traversal(workspace: Path) -> None:
    """词法挡掉 ``..``。"""
    service = FileService(workspace)
    with pytest.raises(ValidationError):
        service.stat("/../../etc/passwd")
    with pytest.raises(ValidationError):
        service.read_head("/../../etc/passwd")


def test_symlink_is_not_followed(workspace: Path) -> None:
    """软链接不能被当成普通文件读 —— 会把工作区外的文件带出来。"""
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    service = FileService(workspace)
    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises((ValidationError, NotFoundError)):
        service.read_head("/link.txt")


# ---------- 上传件块 ----------


def test_uploads_note_lists_workspace_uploads(workspace: Path) -> None:
    """上下文里的「上传件」块要列出工作区里的上传件。"""
    service = FileService(workspace)
    service.upload("需求.pdf", b"%PDF-1.4 x")

    note = build_uploads_note(workspace)
    assert "<uploaded_files>" in note
    assert "/uploads/" in note
    assert "需求.pdf" in note


def test_uploads_note_marks_inlined(workspace: Path) -> None:
    """内联过的图片要标注出来 —— 模型已经看得见了。"""
    service = FileService(workspace)
    uploaded = service.upload("a.png", PNG_BYTES)
    _blocks, parsed = build_attachments(
        workspace,
        [uploaded.path],
        vision_enabled=True,
        max_inline_bytes=1024 * 1024,
    )

    note = build_uploads_note(workspace, attachments=parsed)
    assert "可直接查看" in note


def test_uploads_note_empty_without_uploads(workspace: Path) -> None:
    """没有上传件就不出块。"""
    assert build_uploads_note(workspace) == ""


# ---------- 消息构造 ----------


def test_input_messages_plain_text_unchanged() -> None:
    """没有图片时行为与从前完全一致 —— 纯字符串内容。"""
    messages = _input_messages("你好")
    assert messages[0].content == "你好"


def test_input_messages_with_image_uses_list_content() -> None:
    """有图片时用列表内容：text 块 + image 块。"""
    blocks = [{"type": "image", "base64": "AAA", "mime_type": "image/png"}]
    messages = _input_messages("看图", blocks)

    content = messages[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看图"}
    assert content[1]["type"] == "image"


def test_message_text_from_image_turn() -> None:
    """带图的消息里取文本（记忆检索词）—— 只能取 text 块。"""
    content = [
        {"type": "text", "text": "这张图有问题"},
        {"type": "image", "base64": "AAA", "mime_type": "image/png"},
    ]
    assert _message_text(content) == "这张图有问题"
    assert _message_text("纯文本") == "纯文本"


# ---------- 工作区隔离 ----------


def test_thread_ids_map_to_distinct_workspaces() -> None:
    """不同的 thread_id 必须落在不同的工作区。

    过滤字符不是一一映射 —— "a/b" 与 "ab" 会撞在一起。平台没有鉴权，
    这种碰撞等于把两个会话的文件混在一起。
    """
    from app.core.workspace import workspace_dir_name

    assert workspace_dir_name("a/b") != workspace_dir_name("ab")
    assert workspace_dir_name("t-1") != workspace_dir_name("t-2")


def test_workspace_dir_name_has_no_separator() -> None:
    """目录名不能带路径分隔符。"""
    from app.core.workspace import workspace_dir_name

    name = workspace_dir_name("../../etc")
    assert "/" not in name
    assert ".." not in name


def test_resolve_workspace_creates_dir() -> None:
    """工作区不存在就建。"""
    workspace = resolve_workspace("t-ws-test")
    assert workspace.is_dir()


# ---------- 端到端 ----------


@pytest.mark.asyncio
async def test_chat_with_image_reaches_model(app, client, session) -> None:
    """端到端：上传图片 → 带附件对话 → 模型这一轮就收到了图。"""
    from app.agents.runner import AgentRunner
    from app.api.deps import get_agent_service
    from app.services.agent import AgentService
    from tests.test_agent_capabilities import RecordingModel

    thread_id = "t-upload"
    model = RecordingModel(reply="看到了")
    model.profile = {"image_inputs": True}
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )

    upload = await client.post(
        "/api/v1/files",
        params={"thread_id": thread_id},
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    path = upload.json()["data"]["path"]
    assert path.startswith("/uploads/")

    resp = await client.post(
        "/api/v1/agent/chat",
        json={"message": "这张图里是什么", "thread_id": thread_id, "attachments": [path]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["content"] == "看到了"

    # 模型这一轮收到的用户消息里带着图片块
    seen = model.seen[0]
    human = next(m for m in seen if getattr(m, "type", "") == "human")
    blocks = [b for b in human.content if isinstance(b, dict) and b.get("type") == "image"]
    assert blocks, f"模型没收到图片：{human.content!r}"
    assert base64.b64decode(blocks[0]["base64"]) == PNG_BYTES


@pytest.mark.asyncio
async def test_upload_endpoint_rejects_empty_file(app, client) -> None:
    """空文件要明确拒绝。"""
    resp = await client.post(
        "/api/v1/files",
        params={"thread_id": "t-empty"},
        files={"file": ("a.txt", b"", "text/plain")},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_download_rejects_traversal(app, client) -> None:
    """下载接口不能被路径穿越。"""
    resp = await client.get(
        "/api/v1/files/download",
        params={"thread_id": "t-trav", "path": "/../../etc/passwd"},
    )
    assert resp.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_list_endpoint_returns_files(app, client) -> None:
    """列表接口能看到上传件。"""
    await client.post(
        "/api/v1/files",
        params={"thread_id": "t-list"},
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    resp = await client.get("/api/v1/files", params={"thread_id": "t-list"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] >= 1
    assert data["files"][0]["uploaded"] is True


@pytest.mark.asyncio
async def test_archive_contains_workspace_files(app, client) -> None:
    """打包下载：整个工作区打成一个 zip。

    agent 改完一个项目有几十个文件，用户要的是"打包下载"，不是一个一个点。
    """
    import io
    import zipfile

    await client.post(
        "/api/v1/files",
        params={"thread_id": "t-archive"},
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    resp = await client.get("/api/v1/files/archive", params={"thread_id": "t-archive"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = archive.namelist()
    assert any(name.endswith("a.txt") for name in names), f"包里没有上传的文件：{names}"


@pytest.mark.asyncio
async def test_archive_of_empty_workspace_is_404(app, client) -> None:
    """工作区是空的 —— 没有可打包的东西就明说，别给一个空包。"""
    resp = await client.get("/api/v1/files/archive", params={"thread_id": "t-empty-archive"})
    assert resp.status_code == 404, resp.text


# ---------- 写入与重命名 ----------


def test_write_text_creates_file(workspace: Path) -> None:
    service = FileService(workspace)
    size = service.write_text("/notes.md", "# 标题\n\n正文")
    assert size == len("# 标题\n\n正文".encode("utf-8"))
    assert (workspace / "notes.md").read_text(encoding="utf-8") == "# 标题\n\n正文"


def test_write_text_overwrites(workspace: Path) -> None:
    service = FileService(workspace)
    service.write_text("/a.txt", "旧")
    service.write_text("/a.txt", "新")
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "新"


def test_write_rejects_traversal(workspace: Path) -> None:
    service = FileService(workspace)
    with pytest.raises(ValidationError):
        service.write_text("/../outside.txt", "越界")


def test_write_rejects_symlink_escape(workspace: Path, tmp_path: Path) -> None:
    """最后一段是软链接就拒掉 —— 免得把工作区外面的文件改了。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("不能动", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)

    service = FileService(workspace)
    with pytest.raises((ValidationError, OSError)):
        service.write_text("/link.txt", "改了")
    assert outside.read_text(encoding="utf-8") == "不能动"


def test_rename_moves_file(workspace: Path) -> None:
    service = FileService(workspace)
    service.write_text("/old.txt", "内容")
    new_path = service.rename("/old.txt", "/sub/new.txt")
    assert new_path == "/sub/new.txt"
    assert (workspace / "sub" / "new.txt").read_text(encoding="utf-8") == "内容"
    assert not (workspace / "old.txt").exists()


def test_rename_missing_file(workspace: Path) -> None:
    service = FileService(workspace)
    with pytest.raises(NotFoundError):
        service.rename("/nope.txt", "/other.txt")


def test_rename_existing_target(workspace: Path) -> None:
    service = FileService(workspace)
    service.write_text("/a.txt", "A")
    service.write_text("/b.txt", "B")
    with pytest.raises(ValidationError):
        service.rename("/a.txt", "/b.txt")


def test_rename_rejects_traversal(workspace: Path) -> None:
    service = FileService(workspace)
    service.write_text("/a.txt", "A")
    with pytest.raises(ValidationError):
        service.rename("/a.txt", "/../escaped.txt")
