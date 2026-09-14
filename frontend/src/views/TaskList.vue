<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, type TaskSummary, type User } from '../api'
import { toast } from '../toast'
import {
  badge, btn, btnGhost, card, hint, h3, input, sevBarCls, sevDistTitle, sevOrder, statusBadgeClass, tableTd, tableTh, taskStatusLabel,
} from '../ui'

const props = defineProps<{ user: User | null }>()

const PAGE_SIZE = 50
const tasks = ref<TaskSummary[]>([])
const total = ref(0)
const initialLoading = ref(true)
const loadingMore = ref(false)
const searchInput = ref('')
const search = ref('') // 已应用的搜索词（回车/点搜索生效）
const cancelling = ref('')
const sentinel = ref<HTMLElement | null>(null)
let timer: number | undefined
let seq = 0 // 请求序号：搜索/刷新切换后丢弃过期响应
let observer: IntersectionObserver | undefined

const RUNNING = ['pending', 'fetching', 'scanning', 'parsing']
const hasMore = computed(() => tasks.value.length < total.value)

// append=false 时按已加载数量整窗刷新（新任务、状态变化都能看到）；append=true 追加下一页
async function fetchPage(offset: number, append: boolean) {
  const mySeq = ++seq
  try {
    const r = await api.listTasks({
      limit: append ? PAGE_SIZE : Math.max(tasks.value.length, PAGE_SIZE),
      offset, search: search.value,
    })
    if (mySeq !== seq) return
    tasks.value = append ? tasks.value.concat(r.items) : r.items
    total.value = r.total
    if (append && !r.items.length) total.value = tasks.value.length // 防御：空页时终止续拉
  } catch (e) {
    if (mySeq === seq) toast.error((e as Error).message)
  } finally {
    if (mySeq === seq) initialLoading.value = false
  }
}

async function refresh() {
  if (loadingMore.value) return
  await fetchPage(0, false)
}

function sentinelNear(): boolean {
  const el = sentinel.value
  return !!el && el.getBoundingClientRect().top < window.innerHeight + 300
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value || !sentinelNear()) return
  loadingMore.value = true
  try { await fetchPage(tasks.value.length, true) } finally { loadingMore.value = false }
  await loadMore() // 一页填不满视口时继续补，直到哨兵离开触发区
}

function applySearch() {
  search.value = searchInput.value.trim()
  fetchPage(0, false).then(loadMore)
}
function clearSearch() {
  searchInput.value = ''
  applySearch()
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
function open(id: string) { location.hash = `#/task/${id}` }

onMounted(async () => {
  await fetchPage(0, false)
  await loadMore() // 首页填不满视口时主动补页（IntersectionObserver 对持续相交不会再触发）
  timer = window.setInterval(refresh, 5000)
  observer = new IntersectionObserver(
    entries => { if (entries.some(e => e.isIntersecting)) loadMore() },
    { rootMargin: '300px' },
  )
  if (sentinel.value) observer.observe(sentinel.value)
})
onUnmounted(() => {
  clearInterval(timer)
  observer?.disconnect()
})
</script>

<template>
  <div :class="card">
    <div class="mb-3.5 flex flex-wrap items-center gap-2.5">
      <h3 class="mb-0 text-sm font-semibold text-muted">
        任务列表（已加载 {{ tasks.length }} / 共 {{ total }}，每 5 秒自动刷新；
        {{ props.user?.role === 'admin' ? '全部用户任务' : '我发起的及我项目下的任务' }}）
      </h3>
      <div class="flex-1"></div>
      <div class="flex items-center gap-1.5">
        <input
          v-model="searchInput" type="text" placeholder="搜索 ID / 项目 / 分支 / 模型 / 提交人"
          :class="[input, '!w-64']" @keyup.enter="applySearch"
        />
        <button v-if="search" :class="btnGhost" @click="clearSearch">清除</button>
        <button :class="btn" @click="applySearch">搜索</button>
      </div>
    </div>

    <table v-if="tasks.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">任务</th><th :class="tableTh">项目</th><th :class="tableTh">状态</th>
          <th :class="tableTh">分支/来源</th><th :class="tableTh">模式</th><th :class="tableTh">模型</th>
          <th :class="tableTh">漏洞严重度分布</th><th :class="tableTh">耗时</th>
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
            <span v-if="t.timed_out && t.status !== 'done'" :class="[statusBadgeClass('failed'), 'ml-1']" title="扫描达到档位默认时长被超时回收">超时终止</span>
            <span v-if="t.schedule_id" :class="badge" class="ml-1 bg-accent/15 text-accent" title="由定时计划自动发起">定时</span>
            <span v-if="t.zh_status === 'pending'" :class="badge" class="ml-1 bg-[#9b7bff]/15 text-[#a78bfa]">翻译中</span>
          </td>
          <td :class="[tableTd, 'max-w-[180px] truncate']">
            {{ t.source_type === 'git' ? (t.branch || '默认分支') : t.source_ref }}
          </td>
          <td :class="tableTd">{{ t.scan_mode }}</td>
          <td :class="tableTd">{{ t.model || '-' }}</td>
          <td :class="tableTd">
            <div v-if="t.findings_count > 0" class="flex items-center gap-2" :title="sevDistTitle(t.severity_counts)">
              <span class="text-[13px] font-semibold">{{ t.findings_count }}</span>
              <div class="flex h-2.5 w-24 overflow-hidden rounded-full bg-panel2">
                <div v-for="s in sevOrder.filter(k => t.severity_counts?.[k])" :key="s"
                  :class="sevBarCls[s]" :style="{ width: `${(t.severity_counts[s] / t.findings_count) * 100}%` }"></div>
              </div>
            </div>
            <span v-else class="text-xs text-muted">{{ t.status === 'done' ? '无' : '-' }}</span>
          </td>
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
      <tfoot>
        <tr>
          <td :colspan="12" class="py-2.5 text-center text-xs text-muted">
            <span v-if="loadingMore">加载中…</span>
            <span v-else-if="hasMore">下滑加载更多</span>
            <span v-else>已全部加载（{{ tasks.length }} 条{{ search ? '，含搜索过滤' : '' }}）</span>
          </td>
        </tr>
      </tfoot>
    </table>
    <p v-else :class="hint">
      {{ initialLoading ? '加载中…' : search ? '没有匹配的任务，换个关键词试试。' : '暂无任务。到「项目」页选择项目并发起扫描。' }}
    </p>
    <div ref="sentinel" class="h-px"></div>
  </div>
</template>
