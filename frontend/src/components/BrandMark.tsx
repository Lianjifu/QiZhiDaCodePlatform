/**
 * 品牌标识。
 *
 * 与 `docs/images/brand/favicon.svg` 同一套设计（紫底方块 + 橙/淡紫叠方块）——
 * README 头图、浏览器标签页、侧栏产品头都用它，一个标记到底。
 *
 * **为什么不并进 `icons.tsx`** —— 那份是描边风格、跟着 `currentColor` 走的
 * 界面小图标；品牌标识是**填充**的固定配色，混进那套约定会拧着。
 */

interface BrandMarkProps {
  className?: string
}

export function BrandMark({ className }: BrandMarkProps) {
  return (
    <svg
      viewBox="0 0 80 80"
      className={className}
      role="img"
      aria-label="企智搭"
    >
      <rect width="80" height="80" rx="18" fill="#6828D8" />
      <g transform="translate(10 10) scale(0.75)">
        <rect x="6" y="26" width="44" height="44" rx="13" fill="#E4D4FF" />
        <rect x="26.5" y="2.5" width="51" height="51" rx="15.5" fill="#fff" />
        <rect x="30" y="6" width="44" height="44" rx="13" fill="#F87818" />
      </g>
    </svg>
  )
}
