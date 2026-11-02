# 文件模块与多模态对话输入

> 状态：**已实现**（2026-11-02）
>
> 关联文档：[implementation/backend-strategy.md](backend-strategy.md)（后端存储）、
> [implementation/context-module.md](context-module.md)（上下文模块）、
> [plans/file-storage.md](../plans/file-storage.md)（还没做的部分）

用户在对话里上传文件、给图片提问，agent 读得到、用得上；agent 做出来的东西，
用户下载得走。

---

## 0. 一句话

**上传件落进会话工作区的 `uploads/`，图片另外内联进用户消息。**

---

## 1. 三类文件，先分清

混在一起谈会得出错误的设计。三类的**存储位置、生命周期、谁看得见**都不同：

| | 上传件 | 工作产物 | 平台产物 |
| --- | --- | --- | --- |
| **谁产生** | 用户 | agent | 框架（deepAgents） |
| **例子** | 需求文档、报错截图 | 改好的代码、生成的报告 | 对话历史外置、大工具结果 |
| **存在哪** | 工作区 `uploads/` | 工作区（agent 写哪就在哪） | 工作区**外面**的 `.artifacts/` |
| **agent 看得见** | ✅ 读 | ✅ 读写 | ❌ 故意看不见 |
| **用户能下载** | ✅ | ✅ | ❌ |
| **生命周期** | 跟会话 | 跟会话 | 跟会话 |

### 1.1 为什么平台产物不给下载

`.artifacts/` 里有被压缩掉的**对话历史**（含用户说过的话）和太大的工具输出。
那是内部机制，不是给用户的产物 —— 暴露成下载接口等于开了个泄漏口。

这也是我们把 `artifacts_root` 指到工作区**外面**的原因（见
[backend-strategy.md](backend-strategy.md)）。

### 1.2 为什么上传件放工作区里

放工作区外（像 skills / artifacts 那样）的话，agent 的文件工具**看不见**它 ——
用户传了文件，agent 却读不到，这功能就没意义了。

放工作区里，agent 用现成的 `read_file` / `grep` / `glob` 直接就能处理，
**不需要新增任何 agent 能力**。这是最小改动。

代价：agent 能改用户的原始上传件。对 code agent 来说这通常无所谓（用户就是
让 agent 改它），所以上传接口同时返回原始文件名、大小与 sha256 —— 用户始终
知道自己传的是什么。

---

## 2. 接口

### 2.1 上传

```http
POST /api/v1/files?thread_id=t_20261102_xxx
Content-Type: multipart/form-data
```

响应：

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

返回的 `path` 是**虚拟路径**（`/uploads/...`），不是宿主路径 —— 前端拿它去跟
agent 说"看这个文件"，agent 用文件工具也能直接读。

### 2.2 列出工作区文件

```http
GET /api/v1/files?thread_id=t_20261102_xxx
```

返回工作区里的文件树（上传件 + 工作产物），带大小、修改时间、是否上传件。
**不含 `.artifacts/`**（它在工作区外面）。

### 2.3 下载

```http
GET /api/v1/files/download?thread_id=t_20261102_xxx&path=/uploads/xxx.pdf
```

按虚拟路径下载。路径解析走 `FilesystemBackend` 的 `virtual_mode` ——
`..` 穿越会被拒（实测抛 `ValueError: Path traversal not allowed`），接口层
把它转成 400 而不是 500。

响应头带 `Content-Disposition: attachment` —— 免得浏览器把用户上传的
HTML/SVG 当页面渲染（存储型 XSS）。

### 2.4 删除

```http
DELETE /api/v1/files?thread_id=t_20261102_xxx&path=/uploads/xxx.pdf
```

### 2.5 还没做的

打包下载（zip）与工作区容量上限见 [plans/file-storage.md](../plans/file-storage.md)。

---

## 3. 对话里的多模态输入

### 3.1 交互流程

```text
前端                          平台
 │  POST /api/v1/files (multipart)
 │ ─────────────────────────► │  存到 workspace/uploads/20261102_143000_图.png
 │ ◄───────────────────────── │  {path, filename, size, sha256, is_image}
 │
 │  POST /api/v1/agent/chat
 │  {message:"这张图里有什么问题?", thread_id, attachments:["/uploads/…png"]}
 │ ─────────────────────────► │  图片内联进用户消息；其它文件进上下文的「上传件」块
```

**为什么分两步而不是一个 multipart 的 chat 接口**：

- chat 接口要支持 SSE 流式。multipart + SSE 也能做，但前端得先把文件传完才能
  开始流式 —— 分两步反而更符合"边传边聊"；
- 上传件与对话解耦：用户可以先传三个文件，再一次提问；
- 审批恢复是另一次请求，附件信息在 checkpoint 里已经有了，不依赖请求体重传。

### 3.2 图片怎么进模型

**内联进用户消息**：

```python
HumanMessage(content=[
    {"type": "text", "text": "这张图里有什么问题？"},
    {"type": "image", "base64": "…", "mime_type": "image/png"},
])
```

LangChain 会把 `image` 块转成供应商侧的 `image_url`（data URL）。

**实测 DeepSeek 读得懂** —— 走完整链路（上传 → 落盘 → 内联 → 真实端点），
模型回答"红色，蓝色"（图是左红右蓝）。

**为什么不让 agent 用 `read_file` 去看图**：deepAgents 的 `read_file` 对图片
返回的是 **`tool` 角色消息里带图片块**，而 OpenAI 协议里 tool 消息只该有文本，
兼容端点会拒。内联是这一轮就生效，也不用多一次工具往返。

### 3.3 其它文件怎么进模型

模型看不见 pdf/docx。所以：文件存工作区，上下文里的「上传件」块告诉 agent
路径和大小，agent 用 `read_file` / `execute` 自己处理。

```xml
<uploaded_files>
工作区里有用户上传的材料。需要时用 read_file 读取（路径是虚拟路径，以 / 为工作区根）：
- /uploads/20261102_143000_需求说明.pdf （240.0 KB）
- /uploads/20261102_143000_截图.png （1.2 MB，已随本条消息附上，可直接查看）
</uploaded_files>
```

**列的是工作区里全部的上传件，不只是本轮带的** —— 用户上传完隔两轮再问"刚才
那个文件"，agent 照样找得到。

### 3.4 内联的三道闸

| 闸 | 不通过时 | 为什么 |
| --- | --- | --- |
| **模型能不能看图** | 只给路径，不内联 | 模型不认 `image_url` 会直接报错整轮对话挂掉。降级只是少一条能力 |
| **图够不够大**（默认 5MB） | 只给路径，不内联 | base64 内联会让 checkpoint 明显膨胀 |
| **它真的是图片吗** | 只给路径，不内联 | 扩展名会骗人 —— 按**文件头**判断，不按扩展名 |

第一道闸的判据是供应商配置上的 **`vision_enabled`** 列（迁移 `0011`）。默认
按厂商预设回填：OpenAI / Anthropic / DeepSeek / Gemini / Azure OpenAI 为真，
其余保守留假 —— 猜"能"会让对话直接报错，猜"不能"只是少一条能力。

标记最终挂在模型对象的 `profile["image_inputs"]`（langchain 的标准 profile
字段），跟 `max_input_tokens` 一个路子：模型有什么能力，就跟模型走。

---

## 4. 工作区隔离

**每个会话一份工作区**，agent 的文件工具以 `/` 为根看它。

目录名 = 可读前缀 + `_` + `thread_id` 的 sha256 前 8 位。

**为什么后面要跟哈希**：早先的实现只做字符过滤（去掉非字母数字与 `-_`），
那是**不一一映射**的 —— `"a/b"` 与 `"ab"` 过滤后都是 `"ab"`，两个不同的会话
落到同一个目录。平台又没有鉴权，`thread_id` 由客户端自己给，这种碰撞等于把
两个会话的文件混在一起。

哈希后缀保证不同 `thread_id` 一定落在不同目录。规则集中在
`app/core/workspace.py`，文件接口与 agent 接口共用同一份。

> ⚠️ 改目录名规则会让**已有的工作区目录换位置**（checkpoint 不受影响 ——
> 它的 key 是 `thread_id`，不是路径）。

---

## 5. 安全

**这一节的每一条都因为平台没有鉴权而更要紧。**

1. **没有鉴权** —— 任何人知道 `thread_id` 就能：
   - 上传文件到别人的会话（塞东西）
   - 下载别人会话里的**所有文件**（含用户的需求文档、agent 改的代码）
   - 删除别人会话的文件
   - **thread_id 泄露了就是完全访问** —— 现在的 `thread_id` 由客户端生成，
     不够不可猜测。
   - **上线前必须补**。这是比记忆模块那条更严重的版本 —— 那里泄露的是
     "用户偏好"，这里是"用户的文件"。

2. **路径穿越** —— 下载/删除接口的 `path` 参数走 `virtual_mode` 的解析，
   `..` 要被拒。实测 `FilesystemBackend` 会抛 `ValueError: Path traversal not
   allowed`，接口层要把它转成 400 而不是 500。

3. **软链接逃逸** —— `virtual_mode` 的 `resolve()` 会顺带抓出软链逃逸，
   下载时用的是 `O_NOFOLLOW`。

4. **文件名清洗** —— 上传时的文件名会被洗掉目录成分、路径分隔符与连续的点。
   `../../etc/passwd` 只会变成 `passwd`，落在 `uploads/` 里。

5. **下载要设 `Content-Disposition: attachment`** —— 别让浏览器把 HTML/SVG
   当页面渲染（存储型 XSS）。

6. **大小限制** —— 上传限 50MB，否则一个人能把磁盘写满。

---

## 6. 已知限制

1. **base64 驻留 checkpoint** —— 内联的图片会进 `state["messages"]`，
   checkpointer（文件型 sqlite）把它持久化。多轮对话里每张图都会占一份。

   deepAgents 有 `offload_binary_content` 能把内联媒体换成 blob 引用，但
   `create_deep_agent` 不暴露这个参数 —— 要开就得自己构造一个
   `FilesystemMiddleware` 传进去，漏传 `permissions` 会**破坏审批链路**。
   风险比收益大，先不开。

   缓解：限制单图 ≤ 5MB、一轮 ≤ 10 个附件。

2. **打包下载没做** —— agent 改完一个项目有几十个文件时，用户得一个个下。
   见 [plans/file-storage.md](../plans/file-storage.md)。

3. **工作区无限增长** —— agent 生成的产物会一直堆着。靠容量上限兜底（也没做）。

4. **没有会话管理模块** —— 文件的生命周期挂在会话上，但平台还没有"删除会话"
   这个动作，所以清理暂时挂不上。

5. **上传件 agent 能改** —— 用户拿不回原件。上传接口返回 sha256 与原始
   文件名，让用户知道传的是什么。

---

## 7. 文件清单

```
app/files/__init__.py
app/files/storage.py       存储布局：uploads 目录、文件名清洗、大小限制
app/files/service.py       文件服务：上传 / 列表 / 下载 / 删除
app/files/attachments.py   附件处理：图片内联 + 上传件清单
app/core/workspace.py      工作区路径解析（thread_id → 目录名）
app/schemas/files.py       请求响应模型
app/api/v1/files.py        上传 / 列表 / 下载 / 删除
```

改动的既有文件：

| 文件 | 改什么 |
| --- | --- |
| `app/agents/runner.py` | 内联图片进用户消息；组装上下文时带「上传件」块 |
| `app/agents/llm.py` | 模型对象挂 `profile["image_inputs"]` |
| `app/agents/providers.py` | 供应商预设加 `vision_enabled` |
| `app/models/llm_provider.py` | 加 `vision_enabled` 列 |
| `app/schemas/llm_provider.py` | 加 `vision_enabled` 字段 |
| `app/services/llm_provider.py` | 创建/更新时按预设回填 |
| `app/context/service.py` | 加「上传件」块 |
| `app/core/config.py` | 文件相关上限 |
| `app/api/router.py` | 挂载文件路由 |

**不动的**：记忆模块、RAG 层、能力层的执行流水线、上下文模块的压缩逻辑、
后端存储的选型。

---

## 8. 已知会踩的坑

1. **别用 JSON 传文件** —— base64 膨胀 33%。用 `multipart/form-data`。

2. **别让下载接口直接返回宿主路径的文件** —— 必须走 `virtual_mode` 解析，
   否则 `path=/../../etc/passwd` 就能读系统文件。

3. **别把 `.artifacts/` 暴露给下载接口** —— 里面有对话历史。

4. **zip 要流式输出** —— 工作区可能几百 MB，整个读进内存会 OOM（还没做）。

5. **别忘了 `glob` 的重复** —— 默认后端与 route `"/"` 指向同一个目录，
   框架的聚合逻辑不会去重（已在 `PlatformBackend` 里修）。

6. **文件名时间戳只到秒** —— 同一秒内传两次同名文件会撞，要补序号。
