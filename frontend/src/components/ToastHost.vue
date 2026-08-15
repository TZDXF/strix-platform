<script setup lang="ts">
// 全局 toast 容器：挂在 App.vue，固定在右上角，Teleport 到 body 避免被弹窗层级遮挡
import { dismissToast, toasts, type ToastKind } from '../toast'

const borderClass: Record<ToastKind, string> = {
  success: 'border-ok/40',
  error: 'border-crit/45',
  info: 'border-accent/40',
}
const iconClass: Record<ToastKind, string> = {
  success: 'text-ok',
  error: 'text-crit',
  info: 'text-accent',
}
const icon: Record<ToastKind, string> = { success: '✓', error: '✕', info: 'ℹ' }
</script>

<template>
  <Teleport to="body">
    <div class="pointer-events-none fixed top-4 right-4 z-[100] flex w-[360px] max-w-[94vw] flex-col gap-2.5">
      <TransitionGroup name="toast">
        <div
          v-for="t in toasts" :key="t.id"
          class="pointer-events-auto flex items-start gap-2.5 rounded-[10px] border bg-panel px-3.5 py-3 shadow-lg"
          :class="borderClass[t.kind]"
        >
          <span class="mt-0.5 text-[13px] leading-5 font-bold" :class="iconClass[t.kind]">{{ icon[t.kind] }}</span>
          <div class="flex-1 text-[13px] leading-5 break-all text-text">{{ t.text }}</div>
          <button class="cursor-pointer text-xs leading-5 text-muted hover:text-text" @click="dismissToast(t.id)">✕</button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active,
.toast-move {
  transition: all 0.25s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
