/**
 * 四叶草指示器。
 *
 * **为什么是四叶草，不是点阵**：点阵在深色底上就是省略号，而且跟"正在输出
 * 文字"的流式光标撞脸。四叶草一眼就是"好事正在发生"，形状本身也有记忆点。
 *
 * **为什么不用主色**：主色是朱砂橙（hue 38），14px 的小图标在深底上会读成
 * "红点"。所以另给了一个更橙一档的 `--clover`（见 `theme/tokens.css`）。
 */

/**
 * 一片花瓣的路径 —— Material 的 `favorite` 心形。
 *
 * 心形本来是"尖端在下、两瓣在上"，这里靠 transform 把它挪成"尖端朝中心、
 * 两瓣朝外"，再绕中心转四份拼成一朵四叶草。
 */
const HEART =
  'M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z'

/**
 * 把心形挪到"尖端在中心"的位置。
 *
 * transform 从右往左生效：先把尖端 (12, 21.35) 移到 (12, 12)，再缩到一半
 * 大小，最后才轮到外面的 rotate —— 所以旋转是绕着中心转的。
 */
const PETAL_TRANSFORM = 'translate(12,12) scale(0.46) translate(-12,-21.35)'

/** 四片花瓣，各绕中心转 90°。 */
const PETALS = [0, 90, 180, 270].map((deg) => (
  <path
    key={deg}
    d={HEART}
    transform={`rotate(${deg} 12 12) ${PETAL_TRANSFORM}`}
    fill="currentColor"
  />
))

export default function Clover({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className ?? ''}
      role="presentation"
      aria-hidden="true"
      focusable="false"
    >
      {PETALS}
    </svg>
  )
}
