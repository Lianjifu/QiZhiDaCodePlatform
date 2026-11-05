/**
 * 长期记忆的状态与初始化入口。
 *
 * 长期记忆是复盘改进的**落点之一** —— 集合不在的话，学到的东西只能记在
 * 会话内，下次开新会话就找不回来了。所以这里把"能不能用"摆在明面上，
 * 并且给一个一键就能修好的按钮。
 *
 * **为什么不让它自动建**：RAG 集合的向量化配置（模型 + 维度）是建集合时
 * 锁死的，建错了只能重建 —— 静默替人建一个没配 embedding 的集合，等于把人
 * 锁死在"纯关键词检索"上。让人点一下，顺便看清建的是什么。
 */
import Badge from '../../components/ui/Badge'
import Button from '../../components/ui/Button'
import type { LongTermStatus } from '../../types/api'
import { longTermDetail, longTermReadyLabel } from './longTerm'

export default function LongTermMemoryCard({
  status,
  onInitialize,
  initializing,
}: {
  status: LongTermStatus | null
  onInitialize: () => void
  initializing: boolean
}) {
  if (!status) return null

  return (
    <section className="mt-4 rounded-xl border border-line bg-raised p-4 shadow-1">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-ink">长期记忆</span>
          <Badge tone={status.ready ? 'success' : 'warning'}>
            {longTermReadyLabel(status)}
          </Badge>
          {status.collection_name ? <Badge>{status.collection_name}</Badge> : null}
          {status.ready && status.keyword_only ? <Badge tone="warning">仅关键词</Badge> : null}
        </div>
        {status.ready ? null : (
          <Button size="sm" variant="primary" loading={initializing} onClick={onInitialize}>
            一键初始化
          </Button>
        )}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-ink-muted">
        {longTermDetail(status)}
      </p>
    </section>
  )
}
