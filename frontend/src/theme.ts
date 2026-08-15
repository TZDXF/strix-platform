// 主题偏好：auto（跟随系统，默认）/ light / dark，存 localStorage，html.light 类生效
export type ThemePref = 'auto' | 'light' | 'dark'

const KEY = 'strix_theme'

export function getThemePref(): ThemePref {
  const v = localStorage.getItem(KEY)
  return v === 'light' || v === 'dark' || v === 'auto' ? v : 'auto'
}

export function effectiveTheme(pref: ThemePref = getThemePref()): 'light' | 'dark' {
  if (pref !== 'auto') return pref
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyTheme(pref: ThemePref = getThemePref()): void {
  document.documentElement.classList.toggle('light', effectiveTheme(pref) === 'light')
}

export function setThemePref(pref: ThemePref): void {
  localStorage.setItem(KEY, pref)
  applyTheme(pref)
}

export function cycleThemePref(current: ThemePref): ThemePref {
  return current === 'auto' ? 'light' : current === 'light' ? 'dark' : 'auto'
}

export const themeLabels: Record<ThemePref, string> = {
  auto: '跟随系统',
  light: '亮色',
  dark: '暗色',
}

/** 初始化：应用当前偏好，并在 auto 模式下监听系统切换。 */
export function initTheme(onChange?: (pref: ThemePref) => void): void {
  applyTheme()
  const mq = window.matchMedia('(prefers-color-scheme: light)')
  mq.addEventListener('change', () => {
    if (getThemePref() === 'auto') {
      applyTheme()
      onChange?.('auto')
    }
  })
}
