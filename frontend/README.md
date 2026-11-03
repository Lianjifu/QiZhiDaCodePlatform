# 企智搭·代码平台 —— 前端控制台

React + TypeScript + Vite 的单页应用，后端是仓库里的 `backend/`（FastAPI）。

## 起服务

```bash
cd frontend
npm install
npm run dev
```

打开 <http://localhost:6001>。

**端口 6001**，写死在 `vite.config.ts` 里（`strictPort: true`，端口被占就直接报错，
不静默换端口）。

## 连后端

前端只用相对路径 `/api`，由 **Vite dev server 转发**到后端：

```text
浏览器 → /api/v1/xxx → Vite proxy → http://127.0.0.1:<后端端口>/api/v1/xxx → FastAPI
```

走代理而不是直连的原因：省掉 CORS、SSE 不用特殊处理、生产同域部署时前端代码
一行都不用改。

**后端端口默认取后端 `.env` 里的 `PORT`**（当前是 8000），所以克隆下来两端
都能用默认配置跑起来。

用 `--port` 临时改了后端端口的，前端要跟着设环境变量：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8080 npm run dev
```

> ⚠️ **后端必须从 `backend/` 目录启动**。`.env` 在那儿，`Settings` 是按当前工作
> 目录找它的 —— 在仓库根目录启动会读不到配置（API Key 空、数据库连到另一个空库）。
>
> ```bash
> cd backend && ../.venv/bin/uvicorn app.main:app --reload
> ```

## 目录结构

```text
src/
  api/          后端客户端
    client.ts   fetch 封装：统一解包 ResponseModel、归一错误、留鉴权注入口
    sse.ts      SSE 帧解析（攒够一帧再处理）
    resources.ts 各资源的接口，按后端 router 分
  theme/
    tokens.css  语义色 token（浅色 / 深色两套）
    ThemeProvider.tsx  三档主题：跟随系统 / 浅色 / 深色
  types/api.ts  与后端 schema 一一对应的 TS 类型
  components/
    layout/AppShell.tsx    左侧栏 + 主区
    layout/ThemeToggle.tsx
  features/
    agent/         编码助手（唯一支持多轮对话的页面）
      ChatPage.tsx  MessageList.tsx  ToolCallBlock.tsx
      ApprovalCard.tsx  Composer.tsx  useChat.ts
      SlashCommandMenu.tsx  斜杠命令面板
      MentionPicker.tsx     @ 提及面板
    markdown/
      Markdown.tsx  Markdown 渲染（react-markdown + remark-gfm）
      CodeBlock.tsx 代码块（语言标签 + 复制按钮）
      markdown.css  `.md` 作用域下的正文样式
    sessions/
      store.tsx     会话元数据（localStorage）
      title.ts      从消息里取会话标题（纯函数，可单测）
  pages/
    StatusPage.tsx  FilesPage.tsx  ProvidersPage.tsx  CapabilitiesPage.tsx
  lib/          工具函数
```

## 主题：跟随系统 / 浅色 / 深色

三档，不只是开关 —— 只做「浅/深」两档的话，用户手动设了深色之后，系统切到浅色
他不会跟着变，而「跟随系统」那档能解决。

深浅的切换靠给 `<html>` 挂 `.dark` 类（Tailwind v4 的 `dark:` 变体已从
`prefers-color-scheme` 改成 class 策略）。

**组件里不许写具体色值** —— 一律用语义类（`bg-base` / `text-ink` /
`border-subtle`），具体值只在 `theme/tokens.css` 里改。语义 token 而不是色板：
`slate-500` 只说了颜色，深色下得整体重映射一遍；语义 token 直接表达「这是次要
文字」，深浅两套各给一个值，组件不用动。

## 输入框的三项能力

输入框（`features/agent/Composer.tsx`）里能用的三样东西：

| 能力 | 怎么用 | 落到哪 |
| --- | --- | --- |
| **添加文件或图片** | `+` 菜单 → 添加文件或图片 | 上传到会话工作区的 `uploads/`；图片**内联**进这一轮的消息，模型当场看得见 |
| **斜杠命令** | 敲 `/`，或 `+` 菜单 → 斜杠命令 | `prompt` 类填一段提示词（改完再发）；`action` 类触发本地动作（换模型、开新会话…） |
| **@ 提及** | 敲 `@`，或 `+` 菜单 → 提及文件或目录 | 路径写进正文（`@/uploads/a.png`）；**被提及的图片自动带成附件**，模型这一轮看得见 |

**命令清单在后端**（`GET /api/v1/agent/commands`）—— 前端只渲染，不硬编码；
后端加了命令前端自动就有。

**触发符的识别规则**在 `Composer.tsx` 的 `detectTrigger`：触发符前面必须是空白
或行首，所以 `/uploads/x.png` 这种路径不会被当成斜杠命令，`@` 后面可以直接跟路径。

`@` 面板列的是**工作区的文件与目录**（`GET /api/v1/files/tree`）—— agent 建的
文件也能提到。每次打开面板会重新读一次，因为 agent 可能刚建了新东西。

## 对话正文：全量 Markdown

**助手和用户的正文都按 Markdown 渲染**（`features/markdown/Markdown.tsx`）。
模型输出的 `**粗体**` 得是粗体，不是把星号露给用户看。

支持的语法：

| 语法 | 说明 |
| --- | --- |
| 标题 | `#` ~ `######` |
| 强调 | `**粗**` `*斜*` `~~删除线~~` |
| 列表 | 无序、有序、**嵌套**、任务列表 `- [x]` |
| 表格 | GFM 表格，带列对齐（`\| --- \| :---: \|`） |
| 代码 | 行内代码、围栏代码块（语言标签 + **复制按钮** + 语法高亮） |
| 其他 | 引用块、分隔线、链接、图片、自动链接 |

实现是 `react-markdown` + `remark-gfm` + `rehype-highlight`：

- **原始 HTML 不渲染** —— 不挂 `rehype-raw`。助手消息来自模型，而模型会被诱导
  输出 HTML；不渲染就只是显示成一段文字，渲染了就是 XSS。
- **语法高亮认别名** —— `tsx` 当 typescript、`vue` 当 xml；认不出来的语言就
  不高亮（`rehype-highlight` 对未知语言只记一条 message，不抛错）。
- **样式全在 `.md` 作用域里**（`markdown.css`），不污染页面其余部分；颜色只用
  语义 token，代码高亮那组是 `--code-*`（深浅各一份）。

`CodeBlock` 里有个细节：**渲染用的是高亮后的节点树，复制用的是另抽的纯文本**。
自己抽文本会把颜色丢了。

## 侧栏

侧栏从上到下：**产品头**、**动作**（新建 / 搜索）、**模块入口**、**会话列表**
（已置顶 + 对话和任务）。

| 能力 | 说明 |
| --- | --- |
| 新建 | 立刻建一个会话进列表（不是等用户发了第一条消息才进），跳到 `/chat/<id>` |
| 搜索 | 按标题过滤会话列表 |
| 删除 | 从侧栏列表里删掉这条会话记录（**后端的对话记录还在** —— 见下面的已知缺口） |
| 置顶 | 把会话挪到「已置顶」组 |

**发消息的一瞬间就建会话、跳路由**，不等流跑完 —— 流式期间侧栏看不到这个
会话，用户这时刷新页面，draft id 会重新随机生成，后端的 checkpoint 就成了
孤儿，再也进不来了。

**改过的标题不会被消息内容冲掉** —— 只有默认的「新会话」才让消息标题顶掉它。

**标题里不带 Markdown 记号** —— 正文渲染成 Markdown 了，标题却是裸的
`# 标题` 看着像坏了一样。`titleFromMessage` 会把 `#`、`**`、`` ` ``、引用、
列表记号去掉。

### 上下文占用条 与 会话状态面板

**输入框正上方有一根「上下文占用」条** —— 一行摘要（占比 + 用了多少 / 窗口多大）
加一根进度条。点一下**展开明细**（各占多少：对话消息 / 系统提示词 / 工具定义 /
MCP 工具 / 技能 / 记忆文件 / 压缩缓冲），再点收起。

这根条**放在输入框上方，不放侧栏底部** —— 它是"这一轮对话"的事，该待在对话
旁边，而不是跟别的会话列表混在一起。

展开后有个「在右侧打开完整面板」——**右侧拉开一整列**（参考 Claude 桌面端：面板
占右边，中间是对话区）。面板里每一段是一张独立的卡片：

- **环境** —— 工作区路径、模型、供应商、信任模式；
- **上下文** —— 占了窗口的百分之多少，分别被对话消息 / 系统提示词 / 工具定义 /
  MCP 工具 / 技能 / 记忆文件占掉多少；**压缩缓冲**是留给摘要的那段（窗口用到
  85% 就触发压缩，那 15% 得一直空着）；记忆文件和 MCP 工具可以展开看明细；
- **连接** —— 接进来的模型供应商与 MCP 服务器；
- **产出** —— 工作区里的文件与上传件；
- **改动** —— 这个会话一共改了多少行（`+N −M`）；
- **后台任务** —— 现在在跑的工具调用（沙箱里跑命令那些），以及跑完了多少个。

占用条和右栏的「上下文」卡片**共用同一份明细渲染**
（`features/sessions/ContextBreakdown.tsx`），两处不会算出不一样的数。

**上下文占用是估算**（4 字符约 1 token），面板上写明了 —— 别拿它当精确的
token 数看。后端在 `app/agents/status.py`；在跑的任务在 `app/agents/tasks.py`
（**进程内存里**，重启就清空 —— 它是实时视图，不是审计记录）。

## 页面

| 路由 | 内容 |
| --- | --- |
| `/chat` `/chat/:threadId` | **编码助手** —— 唯一支持多轮对话的页面 |
| `/files` | 工作区文件（上传件与产物）。**看的是当前会话的工作区**（`?thread=`，侧栏点进来会带上）；右上角有「打包下载」，整个工作区打成一个 zip |
| `/providers` | 模型接入配置 |
| `/capabilities` | 统一能力清单 |
| `/status` | 前后端联通检查（真实调接口并显示响应体） |

## 聊天页的事件流

后端 `/agent/stream` 推的**一帧一种事件**（`type` 字段决定）：

| 事件 | 前端动作 |
| --- | --- |
| `text` | 追加到最后一条助手消息（打字机） |
| `tool_call` | 加一个工具折叠块（进行中） |
| `tool_result` | 按 tool_call_id 找到那个块，补上结果 |
| `pending_approval` | 弹审批卡片 |
| `error` | 这一轮炸了 —— 显示原因（**不能少**，少了一帧界面就停在「思考中…」） |
| `done` | 收尾 |

多轮靠 `thread_id` 串 —— 后端用 checkpoint 存历史，前端一直带着同一个 id。
刷新页面后从 `GET /agent/{thread_id}/history` 拉回来。

**会话列表在 localStorage**（标题、置顶）—— 后端没有会话表，这是已知缺口。
消息在 checkpoint 里，清了浏览器数据只是列表没了，消息还在。

**删会话只删侧栏的记录** —— 后端的 checkpoint 与工作区文件都还在。
`/clear` 斜杠命令才是真删（`DELETE /agent/{thread_id}`，删 checkpoint，
工作区文件留着）。

## 约定

**类型要跟后端 schema 对齐**。前端没有代码生成，改了后端 schema 要手工同步
`src/types/api.ts`。

**页面不直接调 `api.get`**，走 `src/api/resources.ts` —— 路径与参数收在一处。

**鉴权留了口子**：`client.ts` 的 `setHeaderProvider` 现在返回空对象。平台目前
没有鉴权（后端所有接口都是裸的），将来加登录/令牌只改这一处。

**`/api/v1/health` 返回的是裸对象**，没有 `ResponseModel` 那层 —— 所以
它走 `api.getRaw`，不是 `api.get`。

## 命令

```bash
npm run dev      # 开发服务器（6001）
npm run build    # 类型检查 + 生产构建
npm run preview  # 预览生产构建
npm run lint     # oxlint
npm test         # 纯函数的单测（触发符、会话标题）
```

`npm test` 跑的是 `scripts/*.mjs` —— Node 24 能直接跑 `.ts`（类型擦除），
所以纯函数不用先构建。**只有纯函数能这么测**：`.tsx` 里带 JSX，Node 跑不了
（`titleFromMessage` 之所以单独放在 `features/sessions/title.ts` 就是这个原因）。
