/**
 * 工具调用的摘要规则：工具名 → 人话，以及参数里"最能说明问题"的那一项。
 *
 * 抽成纯函数是为了能单测 —— 组件里带 JSX，Node 跑不了（`npm test` 跑的是
 * `scripts/*.mjs`，直接跑 `.ts` 纯函数）。
 */

/** 工具名 → 人话。deepAgents 的工具名是 snake_case，我们自己的也是。 */
const TOOL_LABELS: Record<string, string> = {
  // deepAgents 的文件工具
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  delete: '删除文件',
  ls: '列出目录',
  glob: '按名字找文件',
  grep: '搜索内容',
  execute: '执行命令',
  // 我们的能力
  run_python: '运行 Python',
  todo_write: '更新任务清单',
  task: '调用子代理',
  memory_search: '召回记忆',
  memory_read: '读取记忆',
  memory_write: '写入记忆',
  web_fetch: '抓取网页',
  web_search: '联网搜索',
  // git 与协作
  git_status: '查看 git 状态',
  git_worktree_add: '创建 worktree',
  git_worktree_list: '列出 worktree',
  git_worktree_remove: '删除 worktree',
  git_commit: '提交改动',
  git_merge: '合并分支',
  delegate_worker: '派子 agent',
}

/** 工具名 → 人话；不认识就原样返回（别显示成空白）。 */
export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

/**
 * 参数里最能说明"做了什么"的那一项。
 *
 * 顺序是有讲究的：路径类最直观，命令其次，内容类最后（代码可能很长）。
 */
export function toolTarget(args: Record<string, unknown>): string {
  const pick = (key: string): string => {
    const value = args[key]
    return typeof value === 'string' ? value : ''
  }
  const detail =
    pick('file_path') ||
    pick('path') ||
    pick('pattern') ||
    pick('query') ||
    pick('branch') ||
    pick('url') ||
    pick('command') ||
    pick('code').split('\n')[0] ||
    pick('task') ||
    pick('id') ||
    ''
  return detail.trim()
}

/**
 * 工具结果是不是失败的。
 *
 * deepAgents 的文件工具失败时**不抛异常**，而是把 `Error: ...` 写进结果里
 * （`Error: File '...' not found`）。历史消息里没有 ok 字段，只能认这个前缀 ——
 * 不认的话，刷新之后的失败卡片会显示成成功的绿勾。
 */
export function isFailedResult(content: string | null | undefined): boolean {
  return (content ?? '').trimStart().startsWith('Error:')
}

/** 一行参数。 */
export interface ArgRow {
  key: string
  /** 值（字符串） */
  value: string
  /** 是不是结构化值（对象/数组）—— 那种要单独用代码块显示 */
  structured: boolean
}

/**
 * 参数 → 逐行展示。
 *
 * **不直接甩一段 JSON** —— 那是最难读的一种展示方式。标量一行一个，
 * 结构化的（对象/数组）单独标出来。
 */
export function toolArgRows(args: Record<string, unknown>): ArgRow[] {
  return Object.entries(args ?? {}).map(([key, value]) => {
    if (value === null || value === undefined) {
      return { key, value: '', structured: false }
    }
    if (typeof value === 'string') {
      return { key, value, structured: false }
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return { key, value: String(value), structured: false }
    }
    return { key, value: JSON.stringify(value), structured: true }
  })
}
