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
import { isValidElement, useEffect, useRef, useState, type ReactNode } from 'react'
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
  // **流式时别每帧都重解析** —— 解析 + 语法高亮是这条链路上最贵的一步，
  // 长回答流式输出时每帧重解析一遍，光解析就够主线程忙上好几秒。
  const text = useThrottledText(children, STREAM_PARSE_INTERVAL_MS)
  return <div className="md">{renderMarkdown(text)}</div>
}

/** 流式期间重解析的最小间隔（毫秒）。 */
const STREAM_PARSE_INTERVAL_MS = 120

/**
 * 节流后的文本 —— 变化快时不必每次都跟着变。
 *
 * **是节流不是防抖**：防抖要等"安静下来"才更新，流式输出一直有增量，
 * 防抖会让提示词一直停在旧内容上。节流是"最多每 N 毫秒更新一次"，
 * 末尾一定还会补一次（内容不再变时定时器会触发）。
 */
function useThrottledText(text: string, intervalMs: number): string {
  const [value, setValue] = useState(text)
  /** 上一次真正更新是什么时候 */
  const lastRunRef = useRef(0)
  /** 还没触发的定时器 */
  const timerRef = useRef<number | null>(null)
  /** 最新要显示的内容（定时器触发时用它） */
  const pendingRef = useRef(text)

  useEffect(() => {
    pendingRef.current = text
    if (text === value) return
    const wait = Math.max(0, intervalMs - (Date.now() - lastRunRef.current))
    if (wait === 0) {
      lastRunRef.current = Date.now()
      setValue(text)
      return
    }
    if (timerRef.current !== null) return
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      lastRunRef.current = Date.now()
      setValue(pendingRef.current)
    }, wait)
    // **故意不在 cleanup 里清定时器** —— 清了就少了"最后一帧"，
    // 流结束时的最后几个字会留在旧内容上。
  }, [text, value, intervalMs])

  // 组件没了就别再更新了
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    },
    [],
  )

  return value
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
