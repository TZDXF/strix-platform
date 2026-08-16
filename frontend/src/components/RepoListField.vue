<script setup lang="ts">
// 代码仓库列表：一个项目可绑定多个 Git 仓库，每个仓库可填「用途」说明；
// 每行可单独探测（git ls-remote，私有仓库需在外部选好凭据并传入）
// 注意：prop 名 label 与 ui.ts 的样式常量重名，样式常量须别名导入
import { ref } from 'vue'
import { api, type GitRepoRef } from '../api'
import { toast } from '../toast'
import { btnGhost, input, label as labelCls } from '../ui'

const props = withDefaults(defineProps<{
  label?: string
  placeholder?: string
  notePlaceholder?: string
  hint?: string
  max?: number
  authType?: string
  token?: string
}>(), {
  label: '',
  placeholder: 'https://git.company.internal/team/app-a.git',
  notePlaceholder: '该仓库的作用，如：前端 Web / 后端 API / 公共库（可选）',
  hint: '',
  max: 10,
  authType: '',
  token: '',
})

const rows = defineModel<GitRepoRef[]>({ default: () => [] })

const checking = ref(-1)

function add() {
  if (rows.value.length >= props.max) { toast.error(`最多绑定 ${props.max} 个仓库`); return }
  rows.value.push({ url: '', note: '' })
}

function remove(i: number) { rows.value.splice(i, 1) }

async function check(i: number) {
  const url = rows.value[i]?.url.trim() || ''
  if (!url) { toast.error('请先填写仓库地址'); return }
  checking.value = i
  try {
    const r = await api.checkGitRepo(url, props.authType, props.token)
    if (r.reachable) {
      toast.success(`✓ 可访问：共 ${r.branches.length} 个分支，默认分支 ${r.branches[0] || '-'}`)
    } else {
      toast.error(`✗ 无法访问：${r.detail}。请确认地址正确、私有仓库已配好凭据。`)
    }
  } catch (e) {
    toast.error(`探测失败：${(e as Error).message}`)
  } finally { checking.value = -1 }
}
</script>

<template>
  <label v-if="label" :class="labelCls">{{ label }}</label>
  <div v-if="!rows.length" class="rounded-lg border border-dashed border-border px-3 py-2.5 text-xs text-muted">
    暂无仓库，点击下方按钮可绑定多个（同一次扫描会包含全部仓库）。
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
  >+ 添加仓库</button>
  <div v-if="hint" class="mt-1 text-xs text-muted">{{ hint }}</div>
</template>
