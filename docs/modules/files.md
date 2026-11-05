# 文件模块

> 一句话：用户上传的材料落进会话工作区，agent 产物也留在工作区，用户能下载、
> 能改；平台自己产生的东西（被压缩掉的对话历史等）留在工作区外面，不给下载。
>
> 前端入口：`/files` 页面（`frontend/src/pages/FilesPage.tsx`）；输入框的
> 「@ 提及」面板读工作区文件树（`Composer.tsx`）。

## 1. 这个模块是什么

文件模块负责一件事：**会话里的文件从哪来、存在哪、谁能看见、谁能拿走**。

代码落在 `backend/app/files/`：

```
storage.py      存储布局、文件名清洗、上传件目录
service.py      文件服务：上传 / 列表 / 下载 / 删除 / 写入 / 重命名 / 打包
attachments.py  附件处理：图片内联 + 上传件清单
diffstat.py     改动量统计（工具块上那个 "+12 −3"）
```

工作区路径解析在 `backend/app/core/workspace.py`，文件接口与 agent 接口共用
同一份规则。

### 1.1 三类文件，先分清

混在一起谈会得出错误的设计。三类的**存储位置、谁看得见、生命周期**都不同：

| | 上传件 | 工作产物 | 平台产物 |
| --- | --- | --- | --- |
| **谁产生** | 用户 | agent | 框架（deepAgents） |
| **例子** | 需求文档、报错截图 | 改好的代码、生成的报告 | 对话历史外置、大工具结果 |
| **存在哪** | 工作区 `uploads/` | 工作区（agent 写哪就在哪） | 工作区**外面**的 `.artifacts/` |
| **agent 看得见** | ✅ 读 | ✅ 读写 | ❌ 故意看不见 |
| **用户能下载** | ✅ | ✅ | ❌ |
| **生命周期** | 跟会话 | 跟会话 | 跟会话 |

**平台产物不给下载**：`.artifacts/` 里有被压缩掉的**对话历史**（含用户说过的话）
和太大的工具输出。那是内部机制，不是给用户的产物 —— 暴露成下载接口等于开了个
泄漏口。这也是 `artifacts_root` 指到工作区外面的原因（见
[上下文模块](context.md)）。

**上传件放工作区里**：放外面的话 agent 的文件工具**看不见**它 —— 用户传了文件，
agent 却读不到，这功能就没意义了。放工作区里，agent 用现成的 `read_file` /
`grep` / `glob` 直接就能处理，**不需要新增任何 agent 能力**。

代价：agent 能改用户的原始上传件。对 code agent 来说这通常无所谓（用户就是
让 agent 改它），所以上传接口同时返回原始文件名、大小与 sha256 —— 用户始终
知道自己传的是什么。

### 1.2 路径一律是虚拟路径

对外暴露的都是以 `/` 开头、以工作区为根的虚拟路径（`/uploads/xxx.png`）。
宿主路径只在拼命令时用。

**路径安全由 deepAgents 的 `FilesystemBackend`（`virtual_mode=True`）保证** ——
`..` 穿越、`~` 展开、软链接逃逸都会被它挡掉，我们不另写一套。写操作另走一条
更严的路（见 §3.3）。

## 2. 接口清单

`backend/app/api/v1/files.py`，前缀 `/files`。全部接口都带 `thread_id` 查询
参数 —— 没有鉴权，`thread_id` 就是这个工作区的钥匙。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/files?thread_id=` | multipart 上传一个文件，落工作区 `uploads/` |
| GET | `/files?thread_id=&include_uploads=true` | 列工作区文件（上传件 + agent 产物），不含目录 |
| GET | `/files/tree?thread_id=` | 列工作区文件**与目录**（@ 提及面板用） |
| GET | `/files/archive?thread_id=` | 打包成 zip 下载 |
| GET | `/files/download?thread_id=&path=` | 按虚拟路径下载单文件 |
| DELETE | `/files?thread_id=&path=` | 删一个文件 |
| PUT | `/files?thread_id=&path=` | 写入（覆盖）文本文件内容 |
| PATCH | `/files?thread_id=&path=` | 重命名 / 移动 |

### 2.1 上传

```http
POST /api/v1/files?thread_id=t_20261102_xxx
Content-Type: multipart/form-data
```

响应（`UploadOut`）：

```json
{
  "code": 0,
  "data": {
    "path": "/uploads/20261102_143000_需求说明.pdf",
    "filename": "20261102_143000_需求说明.pdf",
    "original_name": "需求说明.pdf",
    "size": 245760,
    "sha256": "…",
    "mime_type": "application/pdf",
    "is_image": false,
    "uploaded_at": "2026-11-02T14:30:00Z"
  }
}
```

**用 `multipart/form-data` 而不是 JSON** —— base64 编码会膨胀 33%，大文件尤其
明显。`python-multipart` 已经是依赖了。

返回的 `path` 是**虚拟路径**，不是宿主路径 —— 前端拿它去跟 agent 说"看这个
文件"，agent 用文件工具也能直接读。

### 2.2 列文件

`GET /files` 返回工作区里的文件（上传件 + agent 的产物），带大小、修改时间、
`uploaded` 标记。`include_uploads=false` 可以把 `uploads/` 排除掉。
**不含平台产物** —— 那是工作区外面的 `.artifacts/`，里面有被压缩掉的对话历史，
不给用户看也不给下载。

`GET /files/tree` 额外把**目录**也列进来（`is_dir=true`）。@ 提及面板要能选
目录，光有文件不够。

### 2.3 下载单文件

```http
GET /api/v1/files/download?thread_id=t_20261102_xxx&path=/uploads/xxx.pdf
```

路径解析走 `FilesystemBackend` 的 `virtual_mode` —— `..` 穿越会被拒（实测抛
`ValueError: Path traversal not allowed`），接口层把它转成 400 而不是 500。

响应头带 `Content-Disposition: attachment` —— 免得浏览器把用户上传的 HTML/SVG
当页面渲染（存储型 XSS）。非 ASCII 文件名按 RFC 5987 编码（`filename*=UTF-8''…`），
直接塞进 header 会被某些客户端解析坏。

### 2.4 打包下载

```http
GET /api/v1/files/archive?thread_id=t_20261102_xxx
```

agent 改完一个项目有几十个文件，用户要的是"打包下载"，不是一个一个点。响应
是一个 zip 文件，文件名是 `{thread_id 清洗后}.zip`。

### 2.5 写入与重命名

`PUT /files` 覆盖写一个**文本**文件（二进制走上传接口）；`PATCH /files` 重命名
或移动到别的目录。两者都走写操作那条更严的路径解析（§3.3）。

### 2.6 会话删除会连带删掉工作区

这两个接口在 agent 那边，不在文件模块，但直接影响文件模块的生命周期：

| 接口 | 作用 |
| --- | --- |
| `DELETE /api/v1/agent/{thread_id}` | 只清对话记录，**文件留着** |
| `DELETE /api/v1/agent/{thread_id}/session` | **彻底删会话** —— 连工作区一起 |

`AgentService.delete_session` 会 `shutil.rmtree` 掉整个会话工作区：上传件、
agent 的产物、技能导出全没了，同时清掉 checkpoint、会话级权限模式、环境绑定
关系与会话内的短期记忆。

**为什么要有"彻底删"**：之前只有"清对话"，绑定文件与会话工作区永远没人回收 ——
既是垃圾，也是隐患（工作区里的文件一直能被文件接口读到）。平台没有鉴权，
知道 `thread_id` 就能读到别人工作区里的东西。

删的是**工作区目录**，不是绑定的仓库 —— 绑的是用户的仓库，只删绑定关系，
不删仓库。

## 3. 实现要点

### 3.1 工作区隔离：目录名带哈希

`app/core/workspace.py::workspace_dir_name`。每个会话一份工作区，agent 的文件
工具以 `/` 为根看它。

目录名 = **可读前缀**（最多 48 字符，只留 `isalnum()` 的字符与 `-_`） + `_` +
`thread_id` 的 **sha256 前 8 位**。

**为什么后面要跟哈希**：只做字符过滤是**不一一映射**的 —— `"a/b"` 与 `"ab"`
过滤后都是 `"ab"`，两个不同的会话落到同一个目录。平台又没有鉴权，`thread_id`
由客户端自己给，这种碰撞等于把两个会话的文件混在一起：一边能读到另一边的
上传件与产物。哈希后缀保证不同 `thread_id` 一定落在不同目录。

> 改目录名规则会让**已有的工作区目录换位置**（checkpoint 不受影响 —— 它的
> key 是 `thread_id`，不是路径）。

### 3.2 文件名清洗与不重名

`app/files/storage.py::sanitize_filename`：

1. 去目录成分（`a/b/c.png` → `c.png`，反斜杠也当分隔符）；
2. 控制字符与 `/ \ : * ? " < > |` 换成 `_`；
3. 连续的点压成一个（防 `..` 与 `...`）；
4. 去结尾的点；
5. 长度截到 180。

`unique_filename` 再加一个 `YYYYMMDD_HHMMSS_` 前缀 —— 同一个会话里用户可能传
两次同名文件，不加前缀会互相覆盖，agent 也可能读到"上次那个"。时间戳只到秒，
**同一秒内传两次同名文件还是会撞**，所以给了 `exists` 回调时会在名字后面补
序号（`…_1`、`…_2`），保证不覆盖。

### 3.3 路径安全：两层

**读与删**走 deepAgents 的 `FilesystemBackend(root_dir=workspace, virtual_mode=True)`
—— 它挡 `..`、`~` 展开与软链接逃逸。

**写操作**（`write_text` / `rename`）另走 `FileService._safe_write_path`，比读
更严，两步：

1. **词法**挡 `..`（`_host_path`）—— 拼出来一定在工作区里面；
2. `resolve()` 之后**比对是否落在工作区内** —— 中间某一段是软链接指向外面的话，
   这时才看得出来。

打开文件用 `O_NOFOLLOW` —— 路径最后一段是软链就写失败，免得把工作区外面的
文件改了。`read_head` / `stat` 同样用 `O_NOFOLLOW` / `lstat`，不跟随软链接。

### 3.4 打包下载

`FileService.build_archive(target, max_file_bytes=100MB)`。

- **只打工作区里的东西** —— 平台产物（`.artifacts/`）在工作区外面，自然不会
  被打进去（里面有对话历史）。
- **zip 先写到临时文件再由 `FileResponse` 回传**，`BackgroundTask` 传完删 ——
  不边生成边吐，避免整包驻留内存（工作区可能几百 MB）。
- **跳过软链接**（软链接可能指向工作区外面）。
- **单文件超 100MB 跳过并记日志，不让整包失败** —— 用户要的是"能拿走的那些"，
  不是"一个都别给我"。
- 打完是 0 个文件 → 404（`NotFoundError`），并删掉临时文件。
- 下载文件名用 `_safe_name(thread_id)` —— 只留 `isalnum()` 的字符与 `-_`，截到
  40 字符，空则退化成 `workspace`。**不把用户输入原样塞进 header**。

### 3.5 多模态：图片怎么进模型

`app/files/attachments.py::build_attachments`。附件分两种处理：

| 附件 | 怎么处理 | 为什么 |
| --- | --- | --- |
| 图片 | **内联**进用户消息（`image_url`），这一轮就生效 | 模型看得见 |
| 其它文件 | 只给**路径**，写进上下文的「上传件」块 | 模型看不见 pdf/docx，只能让 agent 自己读 |

**内联进用户消息**：

```python
HumanMessage(content=[
    {"type": "text", "text": "这张图里有什么问题？"},
    {"type": "image", "base64": "…", "mime_type": "image/png"},
])
```

LangChain 会把 `image` 块转成供应商侧的 `image_url`（data URL）。

**为什么不让 agent 用 `read_file` 去看图**：deepAgents 的 `read_file` 对图片
返回的是 **`tool` 角色消息里带图片块**，而 OpenAI 协议里 tool 消息只该有文本，
兼容端点会拒。内联是这一轮就生效，也不用多一次工具往返。

**图片为什么还要落盘**：模型这一轮看得见它，用户也下载得到，agent 想做像素级
处理（裁剪、转格式、OCR）时还能用 `execute`。

### 3.6 不整份读文件

判断"是不是图片"只看**文件头**（32 字节），大小看 `stat`。上传的可能是几百 MB
的日志或数据集 —— 为了判个类型把整份读进内存不值得。

`sniff_image_mime` 按 **magic bytes** 判定（PNG / JPEG / GIF / BMP，WebP 走 RIFF
判断），**不信扩展名** —— 用户把文本改名成 `.png`，内联过去模型会直接报错。

### 3.7 内联的三道闸

任一不过就降级成"只给路径"，而不是让整轮对话挂掉：

| 闸 | 判据 | 不通过时 |
| --- | --- | --- |
| **模型能不能看图** | 供应商配置上的 `vision_enabled` | 只给路径，不内联。模型不认 `image_url` 会直接报错整轮对话挂掉 |
| **图够不够大** | `max_inline_image_bytes`，默认 5MB | 只给路径。base64 内联会让 checkpoint 明显膨胀 |
| **它真的是图片吗** | 文件头 magic bytes | 只给路径。扩展名会骗人 |

可内联的 MIME 白名单（`INLINE_IMAGE_MIME_TYPES`）：png / jpeg / jpg / gif /
webp / bmp。**SVG 明确不在内** —— 它是脚本载体，模型端普遍不收。

附件读不到时**跳过**，不让整轮对话失败（agent 那边路径清单里也不会有它）。
一轮最多 10 个附件（`max_attachments`）。

### 3.8 上传件清单

`build_uploads_note(workspace, attachments=None, max_entries=20)` 渲染上下文的
`<uploaded_files>` 块：

```xml
<uploaded_files>
工作区里有用户上传的材料。需要时用 read_file 读取（路径是虚拟路径，以 / 为工作区根）：
- /uploads/20261102_143000_需求说明.pdf （240.0 KB）
- /uploads/20261102_143000_截图.png （1.2 MB，已随本条消息附上，可直接查看）
</uploaded_files>
```

- **列的是工作区里全部的上传件**，不只是本轮带的 —— 用户上传完隔两轮再问
  "刚才那个文件"，agent 照样找得到。
- 按 mtime 倒序，最多 20 条。
- 已内联的那张标注「已随本条消息附上，可直接查看」。

### 3.9 大小上限

| 项 | 配置 | 默认 |
| --- | --- | --- |
| 单个上传文件 | `max_upload_bytes` | 50MB |
| 单个下载文件 | `max_download_bytes` | 50MB |
| 单文件进 zip | `build_archive(max_file_bytes=...)` | 100MB |
| 单图内联 | `max_inline_image_bytes` | 5MB |
| 上下文里列上传件 | `uploads_block_max_entries` | 20 条 |

上传限 50MB —— 否则一个人能把磁盘写满。

### 3.10 改动量统计

`app/files/diffstat.py::line_diff`。deepAgents 的 `write_file` / `edit_file` 都不
返回 diff —— 成功时只说 "Updated file xxx"。要显示改动量，就得执行前后各读一次
文件，中间差多少行。

按行做 diff（`difflib`），新增一段算新增、删掉一段算删除、改掉一段算「删旧 +
增新」。**不是字符数、不是 token 数** —— 用户看到的是"我改了十几行"。

### 3.11 不变量：改错了会出事的地方

这一节是本文档里最值钱的部分 —— 这些坑是**实测复现过的**，不是设想。

| 不变量 | 改错了会怎样 |
| --- | --- |
| **`artifacts_root` 指到工作区外面** | 平台产物（被压缩掉的对话历史，含用户说过的话）落在用户的工作区里 —— 用户能看见，还可能被 `git commit` 提交上去。理由与修法见[上下文模块](context.md) |
| `.artifacts/` 不出现在任何列表或下载接口里 | 里面有对话历史 —— 暴露成下载接口等于开了个泄漏口 |
| 打包只打工作区内的东西，且**跳过软链接** | 软链接可能指向工作区外面 —— 打进 zip 等于把工作区外的内容交给用户 |
| 上传件必须放在**工作区里** | 移到工作区外的话 agent 的文件工具看不见它，"用户传文件给 agent"这个功能就没意义了 |
| 写操作走 `_safe_write_path`（词法挡 `..` + resolve 后比对是否落在工作区内）+ `O_NOFOLLOW` | 只靠 `FilesystemBackend` 的 `virtual_mode` 不够 —— 写比读多一个"最后一段是软链、指向工作区外面"的场景，会把工作区外的文件改了 |
| 下载响应带 `Content-Disposition: attachment` | 浏览器把用户上传的 HTML/SVG 当页面渲染（存储型 XSS） |
| zip 先落临时文件再由 `FileResponse` 回传 | 边生成边吐会让整包驻留内存（工作区可能几百 MB） |
| 工作区目录名带 `thread_id` 的 sha256 前 8 位 | 只做字符过滤是**不一一映射**的 —— `"a/b"` 与 `"ab"` 过滤后同名，两个会话落到同一目录，文件混在一起 |
| 上传文件名带 `YYYYMMDD_HHMMSS_` 前缀 | 同一会话传两次同名文件会互相覆盖，agent 可能读到"上次那个" |

### 3.12 无鉴权

**平台目前没有鉴权**（用户已明确推迟）。这一节的每一条都因此更要紧。

知道 `thread_id` 就能：

- 下载这个会话里的**所有文件**（含用户的需求文档、agent 改的代码）；
- 往别人的会话里塞文件；
- 删掉别人会话里的文件；
- **覆盖和重命名**别人会话里的文件；
- `DELETE /api/v1/agent/{thread_id}/session` —— 连整个工作区一起删掉。

`thread_id` 由客户端生成，不够不可猜测。**泄露 `thread_id` 就等于交出整个
工作区。** 这是"记忆接口无鉴权"那条风险的更严重版本 —— 那里泄露的是用户
偏好，这里是用户的文件。

## 4. 已知限制

1. **工作区容量上限没做。** agent 的产物无限堆积，没有按体积或 mtime 的兜底
   回收。只能靠"彻底删除会话"（`DELETE /agent/{thread_id}/session`）清掉。

2. **base64 驻留 checkpoint。** 内联的图片会进 `state["messages"]`，checkpointer
   （文件型 sqlite）把它持久化 —— 多轮对话里每张图都占一份。

   deepAgents 有 `offload_binary_content` 能把内联媒体换成 blob 引用，但
   `create_deep_agent` 不暴露这个参数 —— 要开就得自己构造一个
   `FilesystemMiddleware` 传进去，漏传 `permissions` 会**破坏审批链路**。
   风险比收益大。

   缓解：单图 ≤ 5MB、一轮 ≤ 10 个附件。

3. **上传件 agent 能改，用户拿不回原件。** 上传接口返回 sha256 与原始文件名，
   让用户知道自己传的是什么。

4. **无鉴权。** 见 §3.12。

5. **整个 zip 没有总体积上限。** 只有单文件 100MB 的限制，几十个 90MB 的文件
   打出来就是几个 GB。`/files/archive` 也不受 `max_download_bytes` 约束
   （那个上限只管单文件下载）。

6. **`uploads/` 下没有子目录概念。** `build_uploads_note` 用 `iterdir()` 只取
   一层 —— 上传到 `uploads/` 里的文件不会再往下分目录。`/files/tree` 能列出
   目录，但那是工作区里 agent 自己建的目录结构。

7. **上传接口整份读进内存。** `await file.read()` 之后才判大小上限 —— 一个
   超出上限的大文件会先占满内存再被拒。

## 5. 相关文件

```
app/files/__init__.py
app/files/storage.py       存储布局：uploads 目录、文件名清洗、类型判断
app/files/service.py       文件服务：上传 / 列表 / 下载 / 删除 / 写入 / 打包
app/files/attachments.py   附件处理：图片内联 + 上传件清单
app/files/diffstat.py      改动量统计
app/core/workspace.py      工作区路径解析（thread_id → 目录名）
app/schemas/files.py       请求响应模型
app/api/v1/files.py        上传 / 列表 / 树 / 下载 / 打包 / 删除 / 写入 / 重命名
```

接入点（既有文件）：

| 文件 | 改什么 |
| --- | --- |
| `app/agents/runner.py` | 内联图片进用户消息；组装上下文时带「上传件」块 |
| `app/capabilities/intent/middleware.py` | 用 `line_diff` 算工具块上的改动量 |
| `app/capabilities/backends/assembly.py` | `uploads/` 与 `.artifacts/` 的路由挂载 |
| `app/services/agent.py` | `delete_session` 里 `shutil.rmtree` 掉工作区 |
| `app/core/config.py` | 各类大小与条数上限 |
| `app/api/router.py` | 挂载文件路由 |

前端：

```
frontend/src/pages/FilesPage.tsx        文件页：列表、上传、预览、编辑、重命名、删除、打包下载
frontend/src/api/resources.ts            文件相关的请求封装
frontend/src/features/agent/Composer.tsx @ 提及面板读工作区文件树
```
