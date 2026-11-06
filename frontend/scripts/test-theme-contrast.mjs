/**
 * 主题色的对比度回归测试 —— 纯计算，直接在 node 里跑。
 *
 * ```bash
 * npm run test
 * ```
 *
 * **为什么要有这个测试**：oklch 的 L 值差 0.02 在屏幕上几乎看不出来，
 * 但"底色比卡片还深"、"悬停看不出变化"、"弱文字飘着"这三类问题，
 * 只有把实际值算成对比度才知道。改 `tokens.css` 的时候光靠眼睛会漏。
 *
 * **测的是 `tokens.css` 里真正写的值** —— 不是抄一份到脚本里，
 * 那样脚本和主题就各说各话了，主题改了测试不会响。
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const 这里 = path.dirname(fileURLToPath(import.meta.url))
const tokens = fs.readFileSync(path.join(这里, '../src/theme/tokens.css'), 'utf8')

// ---- oklch → sRGB ----
const oklchToSrgb = (L, C, Hdeg) => {
  const h = (Hdeg * Math.PI) / 180
  const a = C * Math.cos(h)
  const b = C * Math.sin(h)
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b
  const s_ = L - 0.0894841775 * a - 1.291485548 * b
  const l = l_ ** 3
  const m = m_ ** 3
  const s = s_ ** 3
  const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s
  const enc = (u) => {
    const v = u <= 0.0031308 ? 12.92 * u : 1.055 * Math.pow(u, 1 / 2.4) - 0.055
    return Math.max(0, Math.min(255, Math.round(v * 255)))
  }
  return [enc(lr), enc(lg), enc(lb)]
}

// ---- 相对亮度与对比度（WCAG 2.1）----
const lum = ([r, g, b]) => {
  const f = (v) => {
    const s = v / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

const contrast = (a, b) => {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}

// ---- 从 tokens.css 里读出两套主题 ----
const 读主题 = (开头) => {
  const i = tokens.indexOf(开头)
  assert.notEqual(i, -1, `tokens.css 里找不到 ${开头}`)
  const 块 = tokens.slice(i, tokens.indexOf('}', i))
  const 值 = {}
  for (const m of 块.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    const raw = m[2].trim()
    let mm = raw.match(/^oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\/?\s*([\d.]*)\)$/)
    if (mm) {
      值[m[1]] = oklchToSrgb(+mm[1], +mm[2], +mm[3])
      continue
    }
    mm = raw.match(/^oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\/?\s*([\d.]*)\)$/)
    if (mm) {
      值[m[1]] = oklchToSrgb(+mm[1] / 100, +mm[2], +mm[3])
      continue
    }
    值[m[1]] = raw
  }
  return 值
}

const 浅 = 读主题(':root {')
const 深 = 读主题('.dark {')

let passed = 0

function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

// WCAG AA：正文 4.5:1，大字（≥18.66px 粗体 / 24px）3:1
const AA正文 = 4.5

const 验主题 = (名, t) => {
  // ---- 表面层次：相邻表面要能分辨 ----
  // 目标值不是抄 WCAG —— 那是给文字的。层次是"看得见"，
  // 取的是**能分辨但不发灰**的下限。
  const 层次 = [
    ['侧栏跟主区分得开', contrast(t['surface-base'], t['surface-sunken']), 1.13],
    ['卡片浮得起来', contrast(t['surface-base'], t['surface-raised']), 1.05],
    ['侧栏跟卡片分得开', contrast(t['surface-sunken'], t['surface-raised']), 1.15],
    ['悬停在侧栏上看得见', contrast(t['surface-hover'], t['surface-sunken']), 1.06],
    ['悬停在页面上看得见', contrast(t['surface-hover'], t['surface-base']), 1.05],
    ['边框撑得住结构', contrast(t['border-subtle'], t['surface-base']), 1.3],
    ['边框在侧栏上也撑得住', contrast(t['border-subtle'], t['surface-sunken']), 1.2],
  ]
  for (const [说明, 值, 目标] of 层次) {
    assert.ok(
      值 >= 目标,
      `${名}：${说明} ${值.toFixed(3)} < ${目标} —— 表面上分不出来`,
    )
  }

  // ---- 文字对比度：每一个「文字 × 表面」都要过 AA ----
  const 组合 = [
    ['text-primary', 'surface-base'],
    ['text-primary', 'surface-sunken'],
    ['text-primary', 'surface-raised'],
    ['text-secondary', 'surface-base'],
    ['text-secondary', 'surface-sunken'],
    ['text-secondary', 'surface-raised'],
    ['text-tertiary', 'surface-base'],
    ['text-tertiary', 'surface-sunken'],
    ['text-tertiary', 'surface-raised'],
    ['accent', 'surface-base'],
    ['accent', 'surface-sunken'],
    ['accent', 'surface-raised'],
    ['accent', 'accent-soft'],
    ['accent', 'accent-muted'],
  ]
  for (const [字, 底] of 组合) {
    const 值 = contrast(t[字], t[底])
    assert.ok(
      值 >= AA正文,
      `${名}：${字} 在 ${底} 上只有 ${值.toFixed(2)}:1（AA 要 ${AA正文}）`,
    )
  }
}

check('浅色：表面分得开 + 文字都过 WCAG AA', () => {
  验主题('浅色', 浅)
})

check('深色：表面分得开 + 文字都过 WCAG AA', () => {
  验主题('深色', 深)
})

check('浅色底色不是纯白 —— 纯白配纯白卡片就没有层次', () => {
  // 底色跟卡片必须有差距，否则卡片得靠描边撑着，看着像一堆空框
  const 差 = contrast(浅['surface-base'], 浅['surface-raised'])
  assert.ok(差 >= 1.05, `底色与卡片只差 ${差.toFixed(3)}:1 —— 卡片浮不起来`)
  // 但也不能压得太狠 —— 压狠了就发灰，不像纸
  assert.ok(差 <= 1.2, `底色与卡片差 ${差.toFixed(3)}:1 —— 底色压太狠了`)
})

check('悬停在各个底上的方向一致 —— 不能一半亮一半暗', () => {
  // **只看对比度比例会漏掉这一类问题**。之前浅色的 --surface-hover 是 0.96：
  // 比页面底（0.984）**暗**、比侧栏底（0.955）**亮** —— 同一个悬停色，
  // 在侧栏里看着"浮起来"、在页面里看着"压下去"，手感是打架的。
  //
  // 判据：hover 必须落在所有底色的**同一侧**（要么都更暗，要么都更亮）。
  const 验方向 = (名, t, 期望) => {
    const 底 = ['surface-base', 'surface-sunken', 'surface-raised']
    const 方向 = 底.map((b) => (lum(t['surface-hover']) < lum(t[b]) ? '暗' : '亮'))
    const 一致 = 方向.every((d) => d === 方向[0])
    assert.ok(一致, `${名}：hover 在 ${底.join(' / ')} 上的方向是 ${方向.join('/')} —— 不一致`)
    if (期望) {
      assert.equal(方向[0], 期望, `${名}：hover 应该一律比底色${期望}`)
    }
  }
  验方向('浅色', 浅, '暗')
  验方向('深色', 深, '亮')
})

check('深浅两套的强调色各自在自己的底上够读', () => {
  // 浅底上的朱砂橙要更深才读得清，深底上的要更亮
  assert.ok(
    contrast(浅['accent'], 浅['surface-base']) >= AA正文,
    '浅色的强调色在浅底上不够读',
  )
  assert.ok(
    contrast(深['accent'], 深['surface-base']) >= AA正文,
    '深色的强调色在深底上不够读',
  )
})

console.log(`\n${passed} 条通过`)
