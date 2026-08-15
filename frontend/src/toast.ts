// 全局轻量 toast：success / error / info 三种，相同文案展示期间自动去重
// （任务页每 5 秒轮询，避免同一报错反复弹出刷屏）
import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}

export const toasts = reactive<ToastItem[]>([])

let seq = 0

function push(kind: ToastKind, text: string, durationMs: number): void {
  if (toasts.some((t) => t.text === text)) return
  const id = ++seq
  toasts.push({ id, kind, text })
  window.setTimeout(() => dismissToast(id), durationMs)
}

export function dismissToast(id: number): void {
  const i = toasts.findIndex((t) => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export const toast = {
  success: (text: string) => push('success', text, 3500),
  error: (text: string) => push('error', text, 6000),
  info: (text: string) => push('info', text, 4000),
}
