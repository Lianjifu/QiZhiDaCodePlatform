# 模型接入与 MCP 服务器

> 一句话：模型接入把「厂商差异」收敛成数据库里的一条条供应商配置，运行时挑一条构
> 造模型；MCP 服务器是外部能力的供给通道，通道上的工具要单独登记才进得了能力清单。
>
> 前端入口：模型接入页 `/providers`（`frontend/src/pages/ProvidersPage.tsx`）；
> MCP 在「能力清单」页的 MCP 标签页（`frontend/src/pages/CapabilitiesPage.tsx`）

## 1. 这个模块是什么

两块东西放在一起，因为它们回答的是同一个问题：**模型能碰到什么**。

- **模型接入**（`llm_provider` 表）—— 决定对话模型是谁。厂商的协议差异、密钥、
  模型名、上下文窗口、能不能看图，都落在这一条配置上。
- **MCP 服务器**（`mcp_server` 表）—— 决定 agent 能碰到哪些外部工具。服务器本身
  只是一个「通道」，通道上具体的工具是 `tool` 表里的另一行。

平台正式接模型走供应商配置，`.env` 里的 `LLM_*` 只是本地兜底；没有供应商配置
时对话退回环境变量路径（`get_chat_model()`）。

两块的共性是**都在配置层**，不在执行层：这里定义「是什么、能连哪、风险多大」，
真正跑起来分别落在 `app/agents/llm.py`（模型工厂）和 `app/capabilities/execution/`
（统一执行流水线）。

## 2. 接口清单

### 2.1 模型接入（`app/api/v1/llm_provider.py`，前缀 `/llm-providers`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/llm-providers/presets` | 支持的供应商预设列表，前端下拉用 |
| GET | `/llm-providers` | 分页查询供应商配置。参数 `page`(≥1) / `page_size`(1–100) / `provider` / `is_active` |
| GET | `/llm-providers/{provider_id}` | 查询详情 |
| POST | `/llm-providers` | 新增（201） |
| PUT | `/llm-providers/{provider_id}` | 更新，字段全部可选 |
| DELETE | `/llm-providers/{provider_id}` | 删除（204） |
| POST | `/llm-providers/{provider_id}/test` | 连通性测试，**真发一次最小请求**（`model.ainvoke("ping")`） |

响应体统一是 `ResponseModel[...]`；供应商配置的响应模型 `LLMProviderOut` **没有
`api_key` 字段**，只有 `api_key_masked` 与 `has_api_key`。

### 2.2 MCP 服务器（`app/api/v1/mcp_server.py`，前缀 `/mcp-servers`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/mcp-servers` | 列表，参数 `enabled`。内部固定 `page=1, page_size=100` |
| POST | `/mcp-servers` | 新增（201） |
| GET | `/mcp-servers/{server_id}` | 详情 |
| PUT | `/mcp-servers/{server_id}` | 更新，字段全部可选 |
| DELETE | `/mcp-servers/{server_id}` | 删除（204）。有工具引用则 ConflictError |
| GET | `/mcp-servers/{server_id}/tools` | 连上服务器，探测它提供的工具 |

两个路由都挂在 `/api/v1` 下（`settings.api_v1_prefix`，`app/main.py` 里
`app.include_router(api_router, prefix=settings.api_v1_prefix)`）。

### 2.3 工具登记（不属于本模块，但 MCP 工具靠它才生效）

MCP 工具不是自动进清单的，要在「能力清单 → 工具」里手工建一条 `tool` 行：
`type=mcp`、`source=<服务器名>`、`config={"tool_name": "<远端工具名>"}`。
`ToolService._check_mcp_source` 校验 `source` 指向的服务器存在，否则 ConflictError。

## 3. 实现要点

### 3.1 预设表：把厂商差异收敛到一张表

`app/agents/providers.py::PROVIDER_PRESETS` 是 frozen dataclass 组成的静态表，
每个预设的字段：

| 字段 | 含义 |
| --- | --- |
| `key` | **我们自己的枚举值**，存进 `llm_provider.provider` 列的就是它 |
| `display_name` | 界面展示名 |
| `langchain_provider` | `init_chat_model()` 认识的 provider 名 |
| `default_base_url` | 默认接口地址，用户可覆盖 |
| `api_key_env` | 该厂商惯用的环境变量名，仅作提示，不强制 |
| `api_key_required` | 是否必须配 Key（Ollama / vLLM / custom 为 False） |
| `openai_compatible` | 是否 OpenAI 兼容协议。False 的是原生协议（anthropic / azure_openai） |
| `docs_url` | 文档地址 |
| `context_window` | 该厂商的上下文窗口（token），0 = 未知 |
| `vision_enabled` | 该厂商的模型默认能不能看图 |

共 18 个预设：`openai` / `anthropic` / `deepseek` / `moonshot`（Kimi）/
`qwen`（百炼）/ `zhipu`（智谱）/ `doubao`（豆包）/ `siliconflow` / `openrouter` /
`groq` / `mistral` / `together` / `xai`（Grok）/ `gemini` / `azure_openai` /
`ollama` / `vllm` / `custom`。除 `openai`、`anthropic`、`azure_openai` 外都是
OpenAI 兼容，靠 `base_url` 区分。

**`key` 与 `langchain_provider` 不是一回事**，这是这块最容易踩的坑：
`init_chat_model("deepseek:xxx")` 会去找 `langchain-deepseek` 这个包 —— 没装就
直接报 "Initializing ChatDeepSeek requires the langchain-deepseek package"。
国产模型一律 `openai` + 自定义 `base_url`。`azure_openai` 走自己的分支，
参数名不一样，要 `azure_endpoint` + `api_version`。

`base_url` 照抄厂商官方文档，不凭印象加 `/v1` 后缀 —— DeepSeek 官方写的是
`https://api.deepseek.com`，OpenAI 是 `https://api.openai.com/v1`。

### 3.2 模型是怎么选出来的

`AgentService._resolve_model(provider_id)`（`app/services/agent.py`）：

1. 给了 `provider_id` → 用那一条；查不到抛 NotFoundError。
2. 没给 → `repo.get_default()`：`is_default=True` **且** `is_active=True` 的那条；
   一条都没标默认时取**第一条启用的**（按 id 升序）。
3. 都没有 → 返回 `None`，退回环境变量配置（`get_chat_model()`）。
4. 选中的配置 `is_active=False` → ValidationError（配置停用了就是不能用，
   不是静默换一条）。

模型名固定用 `provider.default_model`。**`available_models` 只是元数据**，
代码里没有任何按它选模型的逻辑。

构造走 `app/agents/llm.py::build_model_for_provider`：
`init_chat_model(f"{preset.langchain_provider}:{provider.default_model}", **kwargs)`，
kwargs 含 `temperature` / `max_tokens` / `timeout`，有密钥才放 `api_key`、
有地址才放 `base_url`。Azure 特判：把 `base_url` 换成 `azure_endpoint`，
并塞上 `settings.azure_openai_api_version`。

`init_chat_model()` 的异常**不吞**。缺 Key、集成包没装、模型名写错都是配置错误，
当场炸出来比漂移到「agent 什么都不干」好查得多。

### 3.3 模型对象缓存与「换模型要重建图」

`app/agents/llm.py` 里两个全局表：

- `_MODEL_CACHE: dict[str, BaseChatModel]` —— 身份字符串 → 模型对象；
- `_MODEL_IDENTITIES: dict[int, tuple[BaseChatModel, str]]` —— id(对象) → (对象, 身份)。

身份由配置内容拼出来（供应商配置这一侧）：
`id | provider | base_url | api_key_encrypted | default_model | temperature |
max_tokens | timeout | context_window | vision_enabled`。

**身份是「这份配置」而不是「这个对象」** —— 改了模型名、换了 key、调了温度就是
另一个身份，缓存自然失效，配置没变就还是同一个模型对象。`model_identity(model)`
对外暴露这串身份，`app/agents/runner.py` 用它判断图要不要重建：模型对象被编进
了图里（模型调用、摘要阈值都指着它），不重建就等于拿着旧模型跑新配置。

`_MODEL_IDENTITIES` 之所以连对象一起存，是因为只存 id 的话对象被回收后 id 可能
被复用，换个模型却拿到旧身份。

### 3.4 API Key 怎么存的

`app/core/crypto.py`，Fernet（对称：AES-128-CBC + HMAC-SHA256 认证），主密钥来自
环境变量 `API_KEY_ENCRYPTION_KEY`，**不进数据库**。三个函数：
`encrypt_secret` / `decrypt_secret` / `mask_secret`。

- 脱敏规则：留前 3 位 + 后 4 位，中间 `***`，如 `sk-***cdef`；长度 ≤ 8 全部打 `*`。
- 库里存两份：`api_key_encrypted`（Text 密文）与 `api_key_masked`（String(32) 脱敏串），
  **写入时就算好**。所以列表接口不需要解密、不需要主密钥也能展示。
- 明文只在两处解密：进 SDK，以及更新时为探 embedding 维度复用旧 key。
- 主密钥没配 → `EncryptionError`（AppError，500），提示用
  `scripts/gen_encryption_key.py` 生成。密钥不合法也走同一个错。
- 解密失败（`InvalidToken`）→ EncryptionError，消息是「API_KEY_ENCRYPTION_KEY
  可能已被更换」。

### 3.5 更新语义：传空串 = 清空，不传 = 不动

`LLMProviderService.update_provider` 用 `payload.model_dump(exclude_unset=True)`
区分「没传这个字段」和「传了空」：

- `api_key` **不传** → 保持原样；
- `api_key` 传空串 → 清空已配置的 key（密文置 NULL、脱敏串置 `""`）；
- `api_key` 传非空 → 重新加密写入。

前端「API Key 只有写没有读」：后端只回脱敏串，编辑时输入框留空就是「不修改」，
填了才换（`ProviderForm.tsx` 里 `if (form.api_key.trim()) payload.api_key = ...`）。

其余更新规则：

- `name` 重复 → ConflictError；`provider` 必须在预设表里（schema 校验 + 服务层
  `resolve_preset` 双查）。
- **`context_window` 不传 → 取预设的**；**`vision_enabled` 不传 → 取预设的**。
- **换了 `provider` 而没显式给 `context_window` / `vision_enabled` → 自动跟着新
  预设走**。旧厂商的窗口对不上新模型（压缩阈值会算错），旧厂商的视觉判断也
  对不上新模型（有的厂商同家的文本模型看不了图）。
- `is_default=True` → `clear_default_except()`，全局唯一默认。

### 3.6 上下文窗口与视觉能力挂在模型 profile 上

不是另开一条参数通道 —— **模型有哪些能力，就跟模型走**：

- `_apply_context_window` 设 `profile["max_input_tokens"]`；
- `_apply_vision` 设 `profile["image_inputs"]`（langchain 的**标准** profile 字段，
  不是自造的）。

为什么必须显式设：deepAgents 的摘要中间件按 `model.profile["max_input_tokens"]`
算触发阈值，而 OpenAI 兼容端点挂上去的模型对象**没有 profile** —— 不设就退回
固定的「170000 token 触发、保留 6 条消息」，对 1M 窗口的模型等于只用了不到
两成窗口就开始压缩，压完只剩 6 条消息（一轮工具调用就好几条，等于只留当前这轮）。

窗口为 0（未知）时**不设** —— 让框架用保守默认值，比瞎猜一个大窗口安全。

`vision_enabled` 同理：对话里上传的图片是**直接内联进消息**发给模型的，
模型不认 `image_url` 就直接报错，所以内联之前要先看这个标记；不支持就只把
文件路径告诉 agent。

### 3.7 向量化配置复用同一条供应商配置

不引入任何新基础设施 —— `llm_provider` 表加了两列：

- `embedding_model`（String(128)）：留空 = 这条配置不参与向量化；
- `embedding_dim`（Integer）：探测得来的维度。

绝大多数厂商的对话与向量化接口在同一个 `base_url` 上，差别只是模型名，所以
端点形态是 OpenAI 兼容的 `{base_url}/embeddings`，请求体
`{"model": ..., "input": [...]}`。

维度探测走 `app/rag/embedding.py::probe_dimension`，**真发一次请求**：
`LLMProviderService._probe_embedding_dim` 调它，**探不到不阻塞配置** —— 只记
warning，维度留 0，报错推迟到建集合时（那时会重新探一次并明确报错）。理由是
向量化是可选能力，配错了也不该挡住对话模型的接入。

改 `embedding_model` 时会自动重探；没传新 key 就沿用旧 key（要解密），
解不开（主密钥没配/换过）就跳过，维度留 0。

**没有向量化也能用**：集合没配 embedding_model → `NullEmbeddingProvider`，
检索退化成纯关键词。环境变量 `RAG_EMBEDDING_MODEL` / `RAG_EMBEDDING_BASE_URL` /
`RAG_EMBEDDING_API_KEY` / `RAG_EMBEDDING_DIM` / `RAG_EMBEDDING_TIMEOUT` /
`RAG_EMBEDDING_BATCH_SIZE` 是明文兜底，仅供本地开发。

### 3.8 MCP 服务器是供给通道，不是能力

这是这块最关键的边界，写在 `app/capabilities/registration/registry.py::_mcp_spec`
的注释里：

> MCP server 是**供给通道**，不是能力本身 —— 它不执行，通道上的工具才执行。

对应的三件事：

1. 注册表把 `mcp_server` 表的一行转成 `CapabilitySpec`，`kind=KIND_MCP`，
   `permission="confirm"` —— **权限写死**。理由：走外部 server 意味着平台
   管不到那侧的副作用，声明里的风险等级拦不住远端发生的事。
2. 意图层 `find_spec`（`app/capabilities/intent/resolver.py`）**只认
   `kind == tool` 的成员** —— mcp 是通道、skill 是组合，都不能被模型当工具直接调。
   模型能调的只有通道上单独登记的工具。
3. `AgentService._build_context` 把本次用到的 MCP 服务器塞进
   `ExecutionContext.mcp_servers`，只收 `server.enabled` 的。

### 3.9 一个 MCP 工具怎么变成模型可调的工具

```
建 mcp_server 记录  →  手工建 tool 记录（type=mcp, source=服务器名,
                       config={"tool_name": 远端工具名}）
  →  注册表 _tool_spec 转成 CapabilitySpec
     （locator={"server":..., "tool_name":...}, risk=tool.risk,
       annotations=Annotations.from_dict(tool.annotations)）
  →  AgentService._build_context 把用到的服务器塞进 ExecutionContext.mcp_servers
  →  execution/tooling.py::_build_mcp_tool 连上服务器取到远端工具，
     构造 StructuredTool（**入参 schema 以 MCP server 侧声明为准**）
  →  执行走统一七阶段流水线（execution/pipeline.py）
```

入参 schema 以 server 为准：MCP 工具的参数契约由 server 说了算，两边对不上时
以 server 为准，不然调过去一定失败。

执行阶段的适配器是 `McpAdapter`（`execution/adapters.py`）：连不上 → `NETWORK_ERROR`
（可重试），工具找不到 → `CAPABILITY_NOT_FOUND`。

连接参数由 `app/capabilities/mcp_client.py::build_connection` 生成，按 transport
分三支：stdio 要 `command`（可选 `args` / `env`），sse / streamable_http 要
`url`（可选 `headers` / `timeout`）。**参数不全或 transport 不支持 → 抛
MCPError**：缺参数就明确报错，静默默认值会连到一个错的地址上，排查起来比直接
报错难得多。

`MCPManager` **每次调用都新建 client**。`MultiServerMCPClient` 构造时不建连，
真正建立会话发生在 `get_tools()` / 调用工具时，所以不持有长期连接 ——
一个连不上的 server 会把整个 agent 拖死。

### 3.10 注解继承为什么没打通

**MCP 协议自带 tool annotations**（`readOnlyHint` / `destructiveHint` /
`idempotentHint` / `openWorldHint`），是服务器对自己工具最权威的描述。平台里
`Annotations.from_mcp(annotations)`（`app/capabilities/registration/policy.py:435`）
已经实现好了：

- 驼峰提示逐个映射到平台的蛇形字段：`readOnlyHint` → `read_only`、
  `destructiveHint` → `destructive`、`idempotentHint` → `idempotent`、
  `openWorldHint` → `open_world`；
- 出口维度推断：`open_world` → `egress_target=model_controlled`，否则 `fixed`；
  `egress_data = open_world or not read_only`；
- 拿不到提示时返回全 False 的空注解 —— 宁可是「没信息」，也不瞎猜一个。

**但全仓 grep，`from_mcp` 只有一个定义处，没有任何调用点。**

调用链断在**探测这一环**：`MCPServerService.list_remote_tools` 返回的只有
`{"name", "description"}`（description 截 500 字符）—— **探测时压根没取
annotations**。注册表读注解走的是 `Annotations.from_dict(tool.annotations)`，
从数据库 `tool.annotations` 列读，也就是**人工填的值**。

所以现状是：**MCP 工具的风险等级与注解全靠人工填，填错就绕过治理** ——
把一个能删库的远端工具登记成 `risk=read`，它就走的是只读自动放行。
`from_mcp` 是一个已经写好但没有接线口的转换函数。

## 4. 已知限制

### 4.1 模型接入

- **无鉴权。** 供应商配置接口裸奔：知道 id 就能改 `base_url`、换 key、把某条设成
  默认、删掉别人的配置。响应体不含明文 key 是唯一的保护。（平台级的既有决策：
  所有接口都不校验身份。）
- **主密钥未配置时整条加密链不可用。** `encrypt_secret` / `decrypt_secret` 直接抛
  EncryptionError（500）。不影响列表接口，也不影响不配 key 的新增 —— 只有真正写入
  或解密密钥时才需要主密钥。
- **换主密钥 = 存量密文解不开**，没有轮换/重加密流程，密钥只能重录。
- **`list_providers` 是内存分页**：`await repo.list_all()` 全量取回来再切片，
  `page_size` 上限 100，条数多时不是数据库分页。
- **`available_models` 是死字段**：只存不读，没有「列出该供应商可用模型」的接口，
  `/presets` 也不带模型列表。
- **embedding 维度探测失败只留 0**：报错推迟到建集合时才发现。
- **`/presets` 是静态硬编码表**：不含各家的模型列表、价格、能力明细。
- **`context_window` 是厂商级预设**：同一厂商不同模型窗口不同（DeepSeek 预设给
  1048576 是实测它家 `/models` 的返回值，不适用于所有模型），只能手填覆盖。
- **`vision_enabled` 是配置级而非模型级**：同一厂商不同模型视觉能力不同
  （Kimi 的 VL 系列能看图，主力文本模型不能），用户须自行改配置。
- **前端编辑表单会把「看图」一律写成 true**：`ProviderForm.tsx` 的
  `initialState` 在编辑模式下把 `vision` 硬编码成 `'yes'`，提交时
  `vision_enabled` 恒为 `true` —— 编辑任何一条不支持看图的配置，都会把它改成支持。
- **前端编辑表单总是显式提交 `context_window`**（值取自详情，已经是解析后的值），
  所以后端「换厂商自动跟新预设走」这条规则在界面路径上不会生效。
- **默认供应商的前后端兜底顺序不完全一致**：后端 `get_default` 兜底取第一条
  `is_active=True` 的，前端 `Composer.tsx` 兜底取列表第一条（不过滤停用）——
  第一条被停用时，界面选的模型和后端实际用的模型会不是同一个。

### 4.2 MCP 服务器

- **注解继承链路没通。** 风险等级与注解全靠人工填，填错就绕过治理（见 3.10）。
- **MCP 工具的登记完全靠手工**：必须先建服务器、再建 `tool` 行、风险人工填，
  没有「探测后一键登记」—— `/mcp-servers/{id}/tools` 只回名字和描述。
- **MCP server 的 `env` / `headers` 明文入库**（JSON 列），不走 Fernet 加密 ——
  远端服务的 `Authorization` 头、stdio 的 API key 都是明文。
- **`MCPManager` 每次调用都新建 client**，没有连接池（换来的是不会持有连不上的
  server 把 agent 拖死）。
- **`delete_server` 只检查 `tool.source == server.name` 的引用**（全量扫表），
  有引用则 ConflictError，不会清理别的关联。
- **`list_servers` 固定 `page_size=100`**，超过 100 个会被截断，分页参数没有暴露。
- **无鉴权。** 能建 MCP 服务器本身就是极高风险的接口：**stdio 形态等于在宿主机上
  起一个任意命令进程**，`command` / `args` / `env` 都由调用方指定。
- **`timeout` 只对 sse / http 形态生效**：stdio 形态存了这个字段，但
  `build_connection` 的 stdio 分支没有传它。
- **`tags` 存了但没有基于 MCP server 的检索**：它进了 `CapabilitySpec.tags`，但
  意图层只认 `kind == tool` 的成员，没有按 MCP server 的标签收敛。

## 5. 相关文件

| 文件 | 作用 |
| --- | --- |
| `backend/app/api/v1/llm_provider.py` | 供应商配置接口 |
| `backend/app/api/v1/mcp_server.py` | MCP 服务器接口 |
| `backend/app/services/llm_provider.py` | 供应商 CRUD + 加密 + 连通性测试 + 维度探测 |
| `backend/app/services/mcp_server.py` | MCP 服务器 CRUD + 远端工具探测 |
| `backend/app/models/llm_provider.py` | `llm_provider` 表 |
| `backend/app/models/mcp_server.py` | `mcp_server` 表 |
| `backend/app/schemas/llm_provider.py` | 请求/响应模型（`LLMProviderOut` 无 `api_key`） |
| `backend/app/schemas/mcp_server.py` | 请求/响应模型（`_check_transport_fields` 校验） |
| `backend/app/agents/providers.py` | `PROVIDER_PRESETS` 预设表 |
| `backend/app/agents/llm.py` | 模型工厂、模型对象缓存、`model_identity` |
| `backend/app/core/crypto.py` | Fernet 加解密与脱敏 |
| `backend/app/capabilities/mcp_client.py` | `build_connection` / `MCPManager` |
| `backend/app/capabilities/registration/policy.py` | `Annotations`（含 `from_mcp`）、风险与审批判定 |
| `backend/app/capabilities/registration/registry.py` | `_tool_spec` / `_mcp_spec`，通道与能力的边界 |
| `backend/app/capabilities/execution/tooling.py` | `_build_mcp_tool`，构造 MCP 工具 |
| `backend/app/capabilities/execution/adapters.py` | `McpAdapter`，MCP 侧的实际调用 |
| `backend/app/rag/embedding.py` | 向量化端点与维度探测 |
| `backend/scripts/gen_encryption_key.py` | 生成 `API_KEY_ENCRYPTION_KEY` |
| `frontend/src/pages/ProvidersPage.tsx` | 模型接入页 |
| `frontend/src/features/providers/ProviderForm.tsx` | 供应商配置表单 |
| `frontend/src/pages/CapabilitiesPage.tsx` | 能力清单页（MCP 标签页） |
| `frontend/src/features/capabilities/MCPForm.tsx` | MCP 服务器表单 |
| `frontend/src/features/capabilities/ToolForm.tsx` | 工具表单（MCP 工具在这里登记） |
