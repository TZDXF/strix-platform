<script setup lang="ts">
// 黑盒测试地址列表：可添加多个地址，每个地址可填「作用」说明；每行可单独探测可达性
// 注意：prop 名 label 与 ui.ts 的样式常量重名，样式常量须别名导入，否则会把类名字符串当文字渲染
import { ref } from 'vue'
import { api, type TestTarget } from '../api'
import { toast } from '../toast'
import { btnGhost, input, label as labelCls } from '../ui'

const props = withDefaults(defineProps<{
  label?: string
  placeholder?: string
  notePlaceholder?: string
  hint?: string
  max?: number
}>(), {
  label: '',
  placeholder: 'https://app-a.test.company.internal',
  notePlaceholder: '该地址的作用，如：主站前台 / API 网关 / 管理后台（可选）',
  hint: '',
  max: 10,
})

const rows = defineModel<TestTarget[]>({ default: () => [] })

const checking = ref(-1)

function add() {
  if (rows.value.length >= props.max) { toast.error(`最多添加 ${props.max} 个测试地址`); return }
  rows.value.push({ url: '', note: '' })
}

function remove(i: number) { rows.value.splice(i, 1) }

async function check(i: number) {
  const url = rows.value[i]?.url.trim() || ''
  if (!url) { toast.error('请先填写黑盒测试地址'); return }
  checking.value = i
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
  } finally { checking.value = -1 }
}
</script>

<template>
  <label v-if="label" :class="labelCls">{{ label }}</label>
  <div v-if="!rows.length" class="rounded-lg border border-dashed border-border px-3 py-2.5 text-xs text-muted">
    暂无测试地址（不添加则仅做白盒源码扫描），点击下方按钮可添加多个。
  </div>
  <div v-else class="flex flex-col gap-2.5">
    <div v-for="(row, i) in rows" :key="i" class="rounded-lg border border-border bg-panel2/40 px-2.5 py-2">
      <div class="flex items-center gap-2">
        <span class="shrink-0 text-xs font-bold text-muted">{{ i + 1 }}</span>
        <input v-model="row.url" type="text" :placeholder="placeholder" :class="input" />
        <button
          :class="btnGhost" class="shrink-0 whitespace-nowrap !px-3 !py-1.5 !text-xs"
          :disabled="checking === i" @click="check(i)"
        >
          {{ checking === i ? '测试中…' : '测试访问' }}
        </button>
        <button
          class="shrink-0 cursor-pointer rounded-md border border-border px-2.5 py-1.5 text-xs font-semibold text-crit transition-colors hover:bg-crit/10"
          @click="remove(i)"
        >删除</button>
      </div>
      <input v-model="row.note" type="text" :placeholder="notePlaceholder" :class="input" class="mt-1.5 !text-[12.5px]" />
    </div>
  </div>
  <button
    class="mt-2 cursor-pointer rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-semibold text-muted transition-colors hover:border-accent hover:text-accent"
    @click="add"
  >+ 添加测试地址</button>
  <div v-if="hint" class="mt-1 text-xs text-muted">{{ hint }}</div>
</template>
