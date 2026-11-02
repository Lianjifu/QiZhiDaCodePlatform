/**
 * 斜杠命令面板。
 *
 * 浮在输入框上方 —— 输入框本身就是过滤器（用户敲的 ``/fi`` 里的 ``fi`` 就是
 * 过滤词），所以这里只负责列与高亮。
 */
import { SlashIcon } from '../../components/icons'
import type { SlashCommand } from '../../types/api'

export default function SlashCommandMenu({
  commands,
  activeIndex,
  onHover,
  onSelect,
}: {
  commands: SlashCommand[]
  activeIndex: number
  onHover: (index: number) => void
  onSelect: (command: SlashCommand) => void
}) {
  return (
    <div className="absolute bottom-full left-0 z-20 mb-2 w-[min(26rem,90vw)] overflow-hidden rounded-xl border border-line bg-overlay shadow-xl">
      <div className="max-h-72 overflow-y-auto py-1">
        {commands.length === 0 ? (
          <div className="px-3 py-2 text-xs text-ink-faint">没有匹配的命令</div>
        ) : (
          commands.map((command, index) => (
            <button
              key={command.name}
              type="button"
              onMouseEnter={() => onHover(index)}
              onClick={() => onSelect(command)}
              className={[
                'flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors',
                index === activeIndex ? 'bg-hover' : '',
              ].join(' ')}
            >
              <SlashIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">
                  /{command.name}
                  {command.kind === 'action' ? (
                    <span className="ml-1.5 text-[11px] text-ink-faint">本地动作</span>
                  ) : null}
                </span>
                <span className="block truncate text-xs text-ink-muted">
                  {command.description}
                </span>
              </span>
            </button>
          ))
        )}
      </div>
      <div className="border-t border-line px-3 py-1.5 text-[11px] text-ink-faint">
        ↑↓ 选择 · Enter 确认 · Esc 关闭
      </div>
    </div>
  )
}
