<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { api, type TaskSummary, type User } from '../api'
import { toast } from '../toast'
import {
  badge, card, hint, h3, statusBadgeClass, tableTd, tableTh, taskStatusLabel,
} from '../ui'

const props = defineProps<{ user: User | null }>()
const tasks = ref<TaskSummary[]>([])
const cancelling = ref('')
let timer: number | undefined

const RUNNING = ['pending', 'fetching', 'scanning', 'parsing']

async function refresh() {
  try {
    tasks.value = (await api.listTasks()).items
  } catch (e) { toast.error((e as Error).message) }
}

async function cancelTask(t: TaskSummary, ev: Event) {
  ev.stopPropagation()
  if (!confirm(`确定取消任务 ${t.id.slice(0, 10)}…？正在运行的扫描引擎进程会被终止，已产生的部分结果不会入库。`)) return
  cancelling.value = t.id
  try {
    await api.cancelTask(t.id)
    toast.success('取消请求已下达，任务通常在几秒内终止')
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { cancelling.value = '' }
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
    <h3 :class="h3">任务列表（{{ props.user?.role === 'admin' ? '全部用户任务' : '我发起的及我项目下的任务' }}），每 5 秒自动刷新）</h3>
    <table v-if="tasks.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">任务</th><th :class="tableTh">项目</th><th :class="tableTh">状态</th>
          <th :class="tableTh">分支/来源</th><th :class="tableTh">模式</th><th :class="tableTh">模型</th>
          <th :class="tableTh">发现</th><th :class="tableTh">耗时</th>
          <th :class="tableTh">tokens</th><th :class="tableTh">提交人</th><th :class="tableTh">提交时间</th>
          <th :class="tableTh">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.id" class="cursor-pointer hover:bg-accent/5" @click="open(t.id)">
          <td :class="[tableTd, 'font-mono']">{{ t.id.slice(0, 10) }}…</td>
          <td :class="tableTd">{{ t.project_name }}</td>
          <td :class="tableTd">
            <span :class="statusBadgeClass(t.status)" :title="t.status">{{ taskStatusLabel[t.status] || t.status }}</span>
            <span v-if="t.schedule_id" :class="badge" class="ml-1 bg-accent/15 text-accent" title="由定时计划自动发起">定时</span>
            <span v-if="t.zh_status === 'pending'" :class="badge" class="ml-1 bg-[#9b7bff]/15 text-[#a78bfa]">翻译中</span>
          </td>
          <td :class="[tableTd, 'max-w-[180px] truncate']">
            {{ t.source_type === 'git' ? (t.branch || '默认分支') : t.source_ref }}
          </td>
          <td :class="tableTd">{{ t.scan_mode }}</td>
          <td :class="tableTd">{{ t.model || '-' }}</td>
          <td :class="tableTd">{{ t.findings_count > 0 ? sevCounts(t) : (t.status === 'done' ? '无' : '-') }}</td>
          <td :class="tableTd">{{ fmtDur(t.duration_sec) }}</td>
          <td :class="tableTd">{{ fmtTokens(t) }}</td>
          <td :class="tableTd">{{ t.created_by_name }}</td>
          <td :class="tableTd">{{ fmtTime(t.created_at) }}</td>
          <td :class="tableTd">
            <button
              v-if="RUNNING.includes(t.status)"
              class="cursor-pointer text-xs font-semibold text-crit hover:underline disabled:opacity-50"
              :disabled="cancelling === t.id"
              @click="cancelTask(t, $event)"
            >{{ cancelling === t.id ? '取消中…' : '取消' }}</button>
            <span v-else class="text-xs text-muted">-</span>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else :class="hint">暂无任务。到「项目」页选择项目并发起扫描。</p>
  </div>
</template>
