// 共享 Tailwind 类名：保持各视图样式一致（亮色为 Calendly 风格：白卡片 + 蓝色调阴影 + 8px 基准圆角）
export const card = 'mb-[18px] rounded-xl border border-border bg-panel p-[18px] shadow-soft'
// 更强的悬浮卡片（Hero / KPI 等需要突出的区域）
export const cardLifted = 'rounded-2xl border border-border bg-panel shadow-card'
export const h3 = 'mb-3.5 text-sm font-semibold text-muted'
export const btn =
  'cursor-pointer rounded-lg bg-accent px-5.5 py-2 text-sm font-semibold text-white shadow-soft transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50'
export const btnDark =
  'cursor-pointer rounded-lg bg-text px-5.5 py-2 text-sm font-semibold text-panel disabled:cursor-not-allowed disabled:opacity-50'
export const btnGhost =
  'cursor-pointer rounded-md border border-border bg-transparent px-5 py-2 text-sm font-semibold text-text disabled:cursor-not-allowed disabled:opacity-50'
export const btnDanger =
  'cursor-pointer rounded-md border border-crit bg-transparent px-5 py-2 text-sm font-semibold text-crit disabled:cursor-not-allowed disabled:opacity-50'
export const input =
  'w-full rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent'
export const textarea =
  'w-full rounded-md border border-border bg-panel2 px-2.5 py-2 font-mono text-[12.5px] text-text outline-none focus:border-accent'
export const label = 'mb-1.5 block text-[12.5px] text-muted'
export const hint = 'mt-1 text-xs text-muted'
export const err = 'mt-2.5 text-[13px] text-crit'
export const tableTh = 'border-b border-border px-2.5 py-2 text-left text-[12.5px] font-semibold text-muted'
export const tableTd = 'border-b border-border px-2.5 py-2.5 text-[13.5px]'
export const logPre =
  'max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-[#0b101b] p-3 text-xs leading-relaxed text-[#9fb0cd]'

export const badge = 'inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold'

export function sevBadgeClass(sev: string): string {
  switch (sev) {
    case 'critical': return `${badge} bg-crit/15 text-crit`
    case 'high': return `${badge} bg-high/15 text-high`
    case 'medium': return `${badge} bg-med/15 text-med`
    case 'low': case 'info': return `${badge} bg-low/15 text-low`
    default: return `${badge} bg-border/30 text-muted`
  }
}

export function statusBadgeClass(status: string): string {
  switch (status) {
    case 'pending': return `${badge} bg-border/30 text-muted`
    case 'fetching': case 'scanning': return `${badge} bg-accent/15 text-accent`
    case 'parsing': return `${badge} bg-[#9b7bff]/15 text-[#a78bfa]`
    case 'done': return `${badge} bg-ok/15 text-ok`
    case 'failed': return `${badge} bg-crit/15 text-crit`
    default: return `${badge} bg-border/30 text-muted`
  }
}

export const sevLabel: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危', info: '提示' }

export function sevCellClass(sev: string): string {
  switch (sev) {
    case 'critical': return 'border border-crit/35 bg-crit/[.14] text-crit'
    case 'high': return 'border border-high/30 bg-high/[.12] text-high'
    case 'medium': return 'border border-med/25 bg-med/[.08] text-med'
    case 'low': return 'border border-accent/28 bg-accent/10 text-accent'
    default: return 'border border-low/28 bg-low/10 text-low'
  }
}

export function findingBarClass(sev: string): string {
  switch (sev) {
    case 'critical': return 'border-l-crit'
    case 'high': return 'border-l-high'
    case 'medium': return 'border-l-med'
    default: return 'border-l-low'
  }
}
