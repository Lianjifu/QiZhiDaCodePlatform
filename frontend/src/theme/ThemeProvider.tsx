/**
 * 主题：跟随系统 / 浅色 / 深色 三档。
 *
 * **为什么不是开关而是三档**：只做「浅/深」两档的话，用户手动设了深色之后，
 * 系统切到浅色他不会跟着变 —— 而「跟随系统」那档能解决。
 *
 * 深浅的切换靠给 `<html>` 挂 `.dark` 类（Tailwind v4 的 `dark:` 变体已改成
 * class 策略，见 `index.css`）。
 */
/*
 * provider 与 hook 放同一个文件是 React 的常见写法（hook 要用同一个
 * Context 对象）。这条规则只影响热更新时的局部刷新，关掉。
 */
/* oxlint-disable react/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

export type ThemeMode = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'qzd.theme'

interface ThemeContextValue {
  /** 用户选的档位 */
  mode: ThemeMode
  /** 实际生效的深浅 */
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // 隐私模式等场景下 localStorage 可能不可用
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode)
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  // 跟随系统那档：系统变了要跟着变
  useEffect(() => {
    if (typeof matchMedia === 'undefined') return
    const query = matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const resolved: 'light' | 'dark' =
    mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark')
  }, [resolved])

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 存不了就算了，本次会话内仍然生效
    }
  }, [])

  const value = useMemo(
    () => ({ mode, resolved, setMode }),
    [mode, resolved, setMode],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

/** 取主题。必须在 {@link ThemeProvider} 里面用。 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme 必须在 ThemeProvider 里用')
  }
  return context
}
