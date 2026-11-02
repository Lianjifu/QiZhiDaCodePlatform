/**
 * 占位页。
 *
 * 后端接口都在，但页面按功能逐个加 —— 这一页标出"这个模块在第几阶段做"，
 * 免得进来的人以为页面坏了。
 */
export default function PlaceholderPage({ module, stage }: { module: string; stage: string }) {
  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <h1 className="text-xl font-semibold tracking-tight">{module}</h1>
      <p className="mt-2 text-sm text-slate-500">
        这个模块的接口后端已经就绪，页面还在排队 —— 计划在 <strong>{stage}</strong>。
      </p>
      <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-sm text-slate-400">
        尚未实现
      </div>
    </div>
  )
}
