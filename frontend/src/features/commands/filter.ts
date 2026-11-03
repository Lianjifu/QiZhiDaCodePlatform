/**
 * 命令面板的过滤规则 —— 纯函数，单独一个文件才能在 node 里测（.tsx 跑不了）。
 *
 * **匹配三处**：标题、副标题、额外关键词。副标题也要能搜 —— 用户常常记得
 * "那个管文件的命令"而记不住它叫什么。
 */
export interface FilterableCommand {
  title: string
  hint?: string
  keywords?: string
}

export function filterCommands<T extends FilterableCommand>(
  commands: T[],
  query: string,
): T[] {
  const keyword = query.trim().toLowerCase()
  if (!keyword) return commands
  return commands.filter((command) =>
    [command.title, command.hint, command.keywords]
      .filter((field): field is string => Boolean(field))
      .some((field) => field.toLowerCase().includes(keyword)),
  )
}
