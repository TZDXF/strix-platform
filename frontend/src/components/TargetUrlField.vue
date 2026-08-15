<script setup lang="ts">
// 黑盒测试地址输入：带「测试访问」按钮，调用后端 /api/targets/check 探测，结果以 toast 提示
// 注意：prop 名 label 与 ui.ts 的样式常量重名，样式常量须别名导入，否则会把类名字符串当文字渲染
import { ref } from 'vue'
import { api } from '../api'
import { toast } from '../toast'
import { btnGhost, input, label as labelCls } from '../ui'

defineProps<{
  label?: string
  placeholder?: string
  hint?: string
}>()

const model = defineModel<string>({ default: '' })

const checking = ref(false)

async function check() {
  const url = model.value.trim()
  if (!url) { toast.error('请先填写黑盒测试地址'); return }
  checking.value = true
  try {
    const r = await api.checkTarget(url)
    if (!r.allowed) {
      toast.error(`不允许该地址：${r.reason}`)
    } else if (r.reachable) {
      toast.success(`✓ 可访问：HTTP ${r.status_code}，耗时 ${r.latency_ms}ms`)
    } else {
      toast.error(`✗ 无法访问：${r.detail}。请确认目标服务已启动、地址正确后再发起扫描。`)
    }
  } catch (e) {
    toast.error(`探测失败：${(e as Error).message}`)
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <label v-if="label" :class="labelCls">{{ label }}</label>
  <div class="flex gap-2">
    <input v-model="model" type="text" :placeholder="placeholder" :class="input" />
    <button :class="btnGhost" class="shrink-0 whitespace-nowrap" :disabled="checking" @click="check">
      {{ checking ? '测试中…' : '测试访问' }}
    </button>
  </div>
  <div v-if="hint" class="mt-1 text-xs text-muted">{{ hint }}</div>
</template>
