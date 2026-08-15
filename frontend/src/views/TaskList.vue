<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { api, type TaskSummary, type User } from '../api'
import {
  badge, card, err as errCls, hint, h3, statusBadgeClass, tableTd, tableTh,
} from '../ui'

const props = defineProps<{ user: User | null }>()
const tasks = ref<TaskSummary[]>([])
const error = ref('')
let timer: number | undefined

async function refresh() {
  try {
    tasks.value = (await api.listTasks()).items
    error.value = ''
  } catch (e) { error.value = (e as Error).message }
}

function fmtDur(s: number | null) {
  if (s == null) return '-'
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m`
}
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }
function fmtTokens(t: TaskSummary) {
  if (t.total_tokens == null) return '-'
  return t.total_tokens > 10000 ? Math.round(t.total_tokens / 10000) / 100 + 'M' : String(t.total_tokens)
}
function sevCounts(t: TaskSummary) {
  const c = t.severity_counts || {}
  return ['critical', 'high', 'medium', 'low', 'info'].filter(k => c[k]).map(k => `${k[0].toUpperCase()}:${c[k]}`).join(' ') || '-'
}
function open(id: string) { location.hash = `#/task/${id}` }

onMounted(() => { refresh(); timer = window.setInterval(refresh, 5000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div :class="card">
    <h3 :class="h3">任务列表（{{ props.user?.role === 'admin' ? '全部用户任务' : '我的任务' }}，每 5 秒自动刷新）</h3>
    <div v-if="error" :class="errCls">{{ error }}</div>
    <table v-if="tasks.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">任务</th><th :class="tableTh">项目</th><th :class="tableTh">状态</th>
          <th :class="tableTh">分支/来源</th><th :class="tableTh">模式</th><th :class="tableTh">模型</th>
          <th :class="tableTh">语言</th><th :class="tableTh">发现</th><th :class="tableTh">耗时</th>
          <th :class="tableTh">tokens</th><th :class="tableTh">提交人</th><th :class="tableTh">提交时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.id" class="cursor-pointer hover:bg-accent/5" @click="open(t.id)">
          <td :class="[tableTd, 'font-mono']">{{ t.id.slice(0, 10) }}…</td>
          <td :class="tableTd">{{ t.project_name }}</td>
          <td :class="tableTd">
            <span :class="statusBadgeClass(t.status)">{{ t.status }}</span>
            <span v-if="t.zh_status === 'pending'" :class="badge" class="ml-1 bg-[#9b7bff]/15 text-[#a78bfa]">翻译中</span>
          </td>
          <td :class="[tableTd, 'max-w-[180px] truncate']">
            {{ t.source_type === 'git' ? (t.branch || '默认分支') : t.source_ref }}
          </td>
          <td :class="tableTd">{{ t.scan_mode }}</td>
          <td :class="tableTd">{{ t.model || '-' }}</td>
          <td :class="tableTd">{{ t.report_lang === 'zh' ? '中文' : 'EN' }}</td>
          <td :class="tableTd">{{ t.findings_count > 0 ? sevCounts(t) : (t.status === 'done' ? '无' : '-') }}</td>
          <td :class="tableTd">{{ fmtDur(t.duration_sec) }}</td>
          <td :class="tableTd">{{ fmtTokens(t) }}</td>
          <td :class="tableTd">{{ t.created_by_name }}</td>
          <td :class="tableTd">{{ fmtTime(t.created_at) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else :class="hint">暂无任务。到「项目」页选择项目并发起扫描。</p>
  </div>
</template>
