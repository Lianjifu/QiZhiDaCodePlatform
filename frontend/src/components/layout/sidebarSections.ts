/**
 * 侧栏分区的折叠状态。
 *
 * **为什么单独拎出来**：折叠是个纯状态问题 —— 读、改、存，没有副作用。
 * 放在组件里就没法用 Node 直接测（这个仓库的惯例是纯函数都有脚本测试）。
 *
 * **为什么不跟侧栏的折叠放一起**：那是两件事。侧栏收起是"屏幕窄了，
 * 整条栏让位"；分区收起是"我暂时不想看会话列表"。各自的粒度不同。
 */

/** 分区 id —— 改名字的话，localStorage 里旧的值就认不出来了。 */
export type SectionId = 'workspace' | 'pinned' | 'sessions'

/** 折叠状态存在哪儿 —— 刷新不该把用户的布局弹回去。 */
export const SECTIONS_KEY = 'qzd.sidebar.sections'

/** 分区 id → 是否折叠。 */
export type SectionState = Partial<Record<SectionId, boolean>>

/**
 * 解析 localStorage 里存的那串。
 *
 * 存坏的东西（手改过、别的版本写的、截断的）都当"没折过" ——
 * 侧栏少显示几行不该让页面白屏。
 */
export function parseSections(raw: string | null): SectionState {
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const state: SectionState = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === 'boolean') state[key as SectionId] = value
    }
    return state
  } catch {
    return {}
  }
}

/** 翻一个分区的折叠状态。 */
export function toggleSection(state: SectionState, id: SectionId): SectionState {
  return { ...state, [id]: !state[id] }
}

/** 存回 localStorage 的那串。 */
export function serializeSections(state: SectionState): string {
  const clean: Record<string, boolean> = {}
  for (const [key, value] of Object.entries(state)) {
    if (typeof value === 'boolean') clean[key] = value
  }
  return JSON.stringify(clean)
}
