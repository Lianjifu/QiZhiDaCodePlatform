/**
 * 权限模式的清单与显示规则（纯数据 + 纯函数，可单测）。
 *
 * 语义对齐 Claude Code 的权限模式 —— 这一轮/这个会话「要不要人批」由它决定。
 */

/** 一个权限模式。 */
export interface PermissionModeOption {
  key: string
  label: string
  hint: string
  recommended?: boolean
}

/** 模式清单（顺序就是菜单里的顺序）。 */
export const PERMISSION_MODES: PermissionModeOption[] = [
  {
    key: 'auto',
    label: '自动',
    hint: '按风险档位：只读与写入直接放行，执行命令、联网、破坏性操作要人批',
    recommended: true,
  },
  {
    key: 'manual',
    label: '手动',
    hint: '任何会改东西的操作都要人批，只读不问',
  },
  {
    key: 'accept_edits',
    label: '接受编辑',
    hint: '文件编辑与命令都自动放行，只有破坏性操作要人批',
  },
  {
    key: 'plan',
    label: '只读',
    hint: '写文件与执行命令的工具不提供给模型，只做阅读与规划',
  },
  {
    key: 'bypass',
    label: '绕过权限',
    hint: '所有操作直接执行，不会有人工审批',
  },
]

/** 模式名 → 中文标签。 */
export function permissionLabel(mode: string): string {
  return PERMISSION_MODES.find((item) => item.key === mode)?.label ?? mode
}
