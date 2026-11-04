/**
 * Markdown 渲染。
 *
 * **能渲染什么**（全量）：
 *
 * | 语法 | 说明 |
 * | --- | --- |
 * | 标题 | `#` ~ `######` |
 * | 强调 | `**粗**` `*斜*` `~~删除线~~` |
 * | 列表 | 无序、有序、**嵌套**、任务列表 `- [x]` |
 * | 表格 | GFM 表格，带列对齐 |
 * | 代码 | 行内代码、围栏代码块（带语言标签 + 复制按钮 + 语法高亮） |
 * | 其他 | 引用块、分隔线、链接、图片、自动链接（`https://…`） |
 *
 * **原始 HTML 不渲染** —— 不挂 `rehype-raw`，模型输出的 `<script>` 只会变成
 * 一段文字。这是刻意的：助手消息的内容来自模型，而模型会被诱导输出 HTML。
 */
import { isValidElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

import CodeBlock from './CodeBlock'
import './markdown.css'

/** 从 ` ```lang ` 的 className 里取出语言名。 */
function languageOf(node: ReactNode): string {
  if (!isValidElement(node)) return ''
  const props = node.props as { className?: string }
  const match = /language-([\w+-]+)/.exec(props.className ?? '')
  return match ? match[1] : ''
}

/**
 * 各种块的自定义渲染。
 *
 * **只改外观，不改语义** —— 标签还是那些标签，样式走 `.md` 里的类。
 */
const components: Components = {
  h1: ({ children }) => <h1 className="md-h1">{children}</h1>,
  h2: ({ children }) => <h2 className="md-h2">{children}</h2>,
  h3: ({ children }) => <h3 className="md-h3">{children}</h3>,
  h4: ({ children }) => <h4 className="md-h4">{children}</h4>,
  h5: ({ children }) => <h5 className="md-h5">{children}</h5>,
  h6: ({ children }) => <h6 className="md-h6">{children}</h6>,

  p: ({ children }) => <p className="md-p">{children}</p>,

  a: ({ children, href }) => (
    <a className="md-a" href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),

  ul: ({ children }) => <ul className="md-ul">{children}</ul>,
  ol: ({ children }) => <ol className="md-ol">{children}</ol>,
  li: ({ children }) => <li className="md-li">{children}</li>,

  blockquote: ({ children }) => <blockquote className="md-quote">{children}</blockquote>,

  hr: () => <hr className="md-hr" />,

  /** 表格外面套一层 —— 宽表格在窄屏上要能横向滚。 */
  table: ({ children }) => (
    <div className="md-table-wrap">
      <table className="md-table">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="md-thead">{children}</thead>,
  tbody: ({ children }) => <tbody className="md-tbody">{children}</tbody>,
  tr: ({ children }) => <tr className="md-tr">{children}</tr>,
  th: ({ children }) => <th className="md-th">{children}</th>,
  td: ({ children }) => <td className="md-td">{children}</td>,

  img: ({ src, alt }) => (
    <img className="md-img" src={src} alt={alt ?? ''} loading="lazy" />
  ),

  /** 行内代码 —— 围栏代码块从 `pre` 走，不走这里。 */
  code: ({ children, className }) => {
    const isBlock = /language-/.test(className ?? '')
    if (isBlock) return <code className={className}>{children}</code>
    return <code className="md-code-inline">{children}</code>
  },

  /** 围栏代码块 —— 上窄条（语言 + 复制）+ 下高亮体。 */
  pre: ({ children }) => {
    const language = languageOf(children)
    return <CodeBlock language={language}>{children}</CodeBlock>
  },

  /** 任务列表的勾选框 —— GFM 的 `- [x]` 变成这个。 */
  input: (props) => <input {...props} className="md-checkbox" disabled />,
}

export default function Markdown({ children }: { children: string }) {
  return <div className="md">{renderMarkdown(children)}</div>
}

/**
 * 解析结果缓存。
 *
 * **Markdown 解析是这条链路上最贵的一步** —— 解析 + 语法高亮，一段长代码
 * 能到几十毫秒。历史消息的内容**永远不变**，每次重渲染都重解析一遍纯属浪费。
 *
 * 缓存的是**渲染好的 React 元素**（不是 HTML 字符串）：元素是不可变的描述符，
 * 复用同一棵树在不同位置渲染完全安全 —— 组件自己的状态（复制按钮那个"已复制"）
 * 仍然按位置各自独立。
 *
 * 缓存按内容做键 —— 内容一样就是同一棵树。流式输出时每来一个字都是新内容，
 * 所以要**限量**：满了就丢最旧的（流式的中间版本最没用）。
 */
const CACHE_LIMIT = 80
const cache = new Map<string, ReactNode>()

function renderMarkdown(content: string): ReactNode {
  const cached = cache.get(content)
  if (cached) {
    // 命中就挪到最新（LRU）—— 正在看的内容别被挤掉
    cache.delete(content)
    cache.set(content, cached)
    return cached
  }
  const tree = (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[
        [
          rehypeHighlight,
          {
            // 常见的别名 —— `tsx` 当成 typescript 高亮，`vue` 当成 xml
            aliases: {
              tsx: 'typescript',
              jsx: 'javascript',
              vue: 'xml',
              svelte: 'xml',
              py: 'python',
              sh: 'bash',
              zsh: 'bash',
              yml: 'yaml',
              md: 'markdown',
            },
            plainText: ['plaintext', 'text', 'txt'],
          },
        ],
      ]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  )
  cache.set(content, tree)
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest === undefined) break
    cache.delete(oldest)
  }
  return tree
}
