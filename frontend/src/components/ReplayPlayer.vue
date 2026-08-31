<script setup lang="ts">
import { ref, computed, onUnmounted, watch, nextTick } from 'vue'
import {
  api, type ReplayData, type ReplayEvent, type ReplayEventKind,
  type TranscriptData, type TranscriptEvent,
} from '../api'

/**
 * 执行过程回放（时间轴 + 执行步骤一体）：
 * - 时间轴：解析 strix.log 得到密度图 / 智能体泳道 / 里程碑标记，虚拟时钟重演；
 * - 执行步骤：解析引擎会话库（.state/agents.db）的 chat/tool 事件流，按虚拟时钟
 *   跟随展示——播放到哪一时刻，下方就列出该时刻之前智能体的思考、命令与工具调用。
 * 两者以绝对时间对齐（回放 started_at 为基准）；仅有其一也可工作：
 *   无步骤数据时退回引擎日志事件流；无日志数据时用步骤自身的时间轴播放。
 * live（任务运行中）时轮询重拉两侧数据，播到当前尾部后驻留等待新事件（实时跟拍）。
 */
const props = defineProps<{ taskId: string; live?: boolean }>()

// ===================== 数据加载 =====================
const replay = ref<ReplayData | null>(null)
const replayMiss = ref('')
const replayLoading = ref(false)
let autoPlayed = false // 直播模式首次拿到时间线自动开播；续扫开启新一轮时重置

async function loadReplay() {
  if (replayLoading.value) return
  replayLoading.value = true
  try {
    const res = await api.getReplay(props.taskId)
    if (res.available && res.data) {
      replay.value = res.data
      wallCache.clear() // 续扫可能换 run 目录（started_at 变化），挂钟时间缓存整体作废
      replayMiss.value = ''
      if (props.live && !autoPlayed) {
        autoPlayed = true
        play() // 直播首拉自动开播
      }
    } else replayMiss.value = res.reason || '该任务没有可用于回放的执行日志'
  } catch (e) {
    replayMiss.value = (e as Error).message
  } finally {
    replayLoading.value = false
  }
}

const transcript = ref<TranscriptData | null>(null)
const transLoading = ref(false)
const selected = ref('') // 步骤筛选的智能体 id / 'all'；'' = 后端默认（根智能体）

async function loadTranscript(agent = selected.value) {
  if (transLoading.value) return
  transLoading.value = true
  try {
    const res = await api.getTranscript(props.taskId, agent)
    if (res.available) {
      transcript.value = res
      selected.value = res.selected
    } else transcript.value = null
  } catch {
    transcript.value = null // 步骤缺失不影响回放本体
  } finally {
    transLoading.value = false
  }
}

function pick(id: string) {
  if (id === selected.value) return
  selected.value = id
  loadTranscript(id)
}

// ---- 直播轮询 ----
const POLL_REPLAY = 10_000
const POLL_STEPS = 8_000
let pollReplay: number | undefined
let pollSteps: number | undefined
watch(
  () => props.live,
  (lv) => {
    if (lv) {
      if (replay.value) { stop(); vt.value = 0; autoPlayed = false } // 续扫开启新一轮：回到新起点重新跟拍
      loadReplay(); loadTranscript()
      if (pollReplay === undefined) pollReplay = window.setInterval(loadReplay, POLL_REPLAY)
      if (pollSteps === undefined) pollSteps = window.setInterval(() => loadTranscript(selected.value), POLL_STEPS)
    } else {
      if (pollReplay !== undefined) { clearInterval(pollReplay); pollReplay = undefined }
      if (pollSteps !== undefined) { clearInterval(pollSteps); pollSteps = undefined }
      loadReplay(); loadTranscript() // 初始即完成态，或直播刚收尾：拉一次全量补齐（loading 互斥天然串行）
    }
  },
  { immediate: true },
)

// ===================== 虚拟时钟与播放控制 =====================
const TICK_MS = 200
const SPEEDS = [1, 8, 30, 120, 600]
const vt = ref(0) // 当前回放位置（秒，相对起点）
const playing = ref(false)
const speedIdx = ref(2)
const speed = computed(() => SPEEDS[speedIdx.value])

// 步骤与回放的绝对时间基准（epoch 秒）：优先回放 started_at，回退步骤自身起点
const timeBase = computed(() => {
  const r = replay.value
  if (r?.started_at) {
    const e = Date.parse(r.started_at) / 1000
    if (!Number.isNaN(e) && e > 0) return e
  }
  return transcript.value?.started_at || 0
})
// 步骤事件相对起点的时间（秒）
const stepsSpan = computed(() => {
  const t = transcript.value
  if (!t || !t.events.length) return 0
  const t0 = timeBase.value || t.started_at
  return Math.max(0, t.events[t.events.length - 1].ts - t0)
})
const dur = computed(() => replay.value ? Math.max(replay.value.duration_sec, stepsSpan.value) : stepsSpan.value)

let timer: number | undefined
function stepTick() {
  if (!dur.value) return stop()
  if (vt.value >= dur.value) {
    if (props.live) return // 直播已到当前尾部：驻留等待轮询拉到新事件后续播
    stop()
    return
  }
  vt.value = Math.min(vt.value + (speed.value * TICK_MS) / 1000, dur.value)
  if (vt.value >= dur.value && !props.live) stop()
}
function play() {
  if (dur.value <= 0 || playing.value) return
  if (vt.value >= dur.value && !props.live) vt.value = 0 // 播完点播放 = 重头再来（直播时则原地跟随新数据）
  follow.value = true
  playing.value = true
  timer = window.setInterval(stepTick, TICK_MS)
}
function stop() {
  playing.value = false
  if (timer !== undefined) { clearInterval(timer); timer = undefined }
}
function toggle() { playing.value ? stop() : play() }
function reset() { stop(); vt.value = 0; follow.value = true }
function bumpSpeed(d: number) { speedIdx.value = (speedIdx.value + d + SPEEDS.length) % SPEEDS.length }
function seekTo(sec: number) { vt.value = Math.min(Math.max(0, sec), Math.max(0.01, dur.value - 0.01)) }

onUnmounted(() => { stop(); if (pollReplay !== undefined) clearInterval(pollReplay); if (pollSteps !== undefined) clearInterval(pollSteps) })

// ------------------ 时间显示 ------------------
const wallCache = new Map<number, string>()
function wallClock(t: number): string {
  const base = timeBase.value
  if (!base) return '-'
  const hit = wallCache.get(t)
  if (hit !== undefined) return hit
  const date = new Date((base + t) * 1000)
  const s = Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString('zh-CN', { hour12: false })
  wallCache.set(t, s)
  return s
}
function wallAbs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
function fmtRel(t: number): string {
  const s = Math.floor(t % 60), m = Math.floor((t / 60) % 60), h = Math.floor(t / 3600)
  return `${h > 0 ? `${h}:` : ''}${String(m).padStart(h > 0 ? 2 : 1, '0')}:${String(s).padStart(2, '0')}`
}

// ===================== 执行密度图（div 柱状，无 SVG）=====================
interface BucketView { toolsH: number; llmH: number }
const chartBuckets = computed<BucketView[]>(() => {
  const act = replay.value?.activity || []
  let max = 0
  for (const [, tools, llm] of act) { const s = tools + llm; if (s > max) max = s }
  if (max <= 0) return []
  return act.map(([, tools, llm]) => {
    const s = tools + llm
    if (s <= 0) return { toolsH: 0, llmH: 0 }
    const h = (s / max) * 100
    // llmH 为整柱高度（浅蓝底），toolsH 叠在其底部（实心蓝）——两段同锚点堆叠
    return { toolsH: (tools / s) * h, llmH: h }
  })
})

// 里程碑标记（发现 / 警告 / 错误）：颜色走内联样式，避免动态类名被裁剪
const MARK_COLOR: Record<string, string> = { finding: '#f5a524', warn: '#f0b429', error: '#e5484d' }
const MARK_ICON: Record<string, string> = { finding: '⚑', warn: '⚠', error: '✖' }
interface Marker { pct: number; color: string; icon: string; tip: string }
const markers = computed<Marker[]>(() => {
  const r = replay.value
  if (!r || r.duration_sec <= 0) return []
  return r.events
    .filter(e => e.k === 'finding' || e.k === 'warn' || e.k === 'error')
    .map(e => ({
      pct: (e.t / r.duration_sec) * 100,
      color: MARK_COLOR[e.k],
      icon: MARK_ICON[e.k],
      tip: `[${wallClock(e.t)}] ${e.m}`,
    }))
})
const playheadPct = computed(() => (dur.value > 0 ? Math.min(100, (vt.value / dur.value) * 100) : 0))

// 进度条拖动定位
const barEl = ref<HTMLElement | null>(null)
const dragging = ref(false)
function seekByPointer(ev: PointerEvent) {
  const el = barEl.value
  if (!el || dur.value <= 0) return
  const r = el.getBoundingClientRect()
  seekTo(((ev.clientX - r.left) / r.width) * dur.value)
}
function onBarDown(ev: PointerEvent) {
  dragging.value = true
  try { (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId) } catch { /* 指针非活动时捕获失败可忽略，拖动仍按事件坐标处理 */ }
  seekByPointer(ev)
}
function onBarMove(ev: PointerEvent) { if (dragging.value) seekByPointer(ev) }
function onBarUp() { dragging.value = false }

// ------------------ 智能体泳道 ------------------
const LANES_COLORS = ['#4f8cff', '#f5a524', '#46a758', '#a78bfa', '#3dd68c', '#e5484d', '#f0b429', '#00c8d7']
const lanes = computed(() => {
  const r = replay.value
  if (!r || r.duration_sec <= 0) return []
  return r.agents.map((a, i) => ({
    id: a.id,
    name: a.name,
    parentName: a.parent_name,
    t0: a.t0,
    open: a.t1 == null,
    color: LANES_COLORS[i % LANES_COLORS.length],
    leftPct: Math.min(99.9, (a.t0 / r.duration_sec) * 100),
    widthPct: Math.max(0.4, ((Math.max(a.t1 ?? vt.value, a.t0 + 1) - a.t0) / r.duration_sec) * 100),
    active: vt.value >= a.t0 && vt.value < (a.t1 ?? Infinity),
  }))
})

// ===================== 执行步骤（会话库事件流，按虚拟时钟跟随） =====================
interface AgentOption { id: string; name: string; status: string; total: number; parentName: string }

const agentOptions = computed<AgentOption[]>(() => {
  const t = transcript.value
  if (!t) return []
  const nameOf = (id: string | null) => (id ? t.agents.find(a => a.id === id)?.name || '' : '')
  return t.agents.map(a => ({
    id: a.id,
    name: a.name,
    status: a.status,
    total: t.counts[a.id]?.total || 0,
    parentName: nameOf(a.parent_id),
  }))
})
const AGENT_STATUS_DOT: Record<string, string> = {
  completed: 'bg-ok', running: 'bg-accent animate-pulse', waiting: 'bg-high', failed: 'bg-crit',
}

// ---- 工具家族（strix view 同款分类） ----
const FAMILY_TOOLS: Record<string, readonly string[]> = {
  terminal: ['exec_command', 'write_stdin', 'terminal_execute'],
  browser: ['browser_action'],
  filesystem: ['apply_patch', 'view_image', 'str_replace_editor', 'list_files', 'search_files'],
  proxy: ['list_requests', 'view_request', 'repeat_request', 'list_sitemap', 'view_sitemap_entry', 'scope_rules', 'send_request'],
  reporting: ['create_vulnerability_report', 'list_reports', 'get_report'],
  agents: ['create_agent', 'agent_finish', 'send_message_to_agent', 'wait_for_agents', 'view_agent_graph', 'stop_agent'],
  search: ['web_search'],
  lifecycle: ['scan_start_info', 'subagent_start_info', 'finish_scan', 'respond_to_user'],
  notes: ['create_note', 'delete_note', 'update_note', 'list_notes', 'get_note'],
  skills: ['load_skill'],
  todos: ['create_todo', 'list_todos', 'update_todo', 'mark_todo_done', 'mark_todo_pending', 'delete_todo'],
  coverage: ['record_coverage', 'update_coverage', 'list_coverage'],
  threatModel: ['get_threat_model', 'save_threat_model', 'amend_threat_model'],
}
const FAMILY_MATCH: [RegExp, string][] = [
  [/request|sitemap|scope/, 'proxy'], [/note/, 'notes'], [/todo/, 'todos'],
  [/agent/, 'agents'], [/coverage/, 'coverage'], [/threat_model/, 'threatModel'],
]
const FAMILY_META: Record<string, { label: string; color: string; bg: string }> = {
  terminal: { label: '终端命令', color: 'text-ok', bg: 'bg-ok/12' },
  filesystem: { label: '文件操作', color: 'text-accent', bg: 'bg-accent/12' },
  proxy: { label: '代理请求', color: 'text-[#a78bfa]', bg: 'bg-[#a78bfa]/12' },
  reporting: { label: '漏洞报告', color: 'text-crit', bg: 'bg-crit/12' },
  agents: { label: '智能体协同', color: 'text-[#22d3ee]', bg: 'bg-[#22d3ee]/12' },
  search: { label: '联网搜索', color: 'text-high', bg: 'bg-high/12' },
  notes: { label: '共享笔记', color: 'text-high', bg: 'bg-high/12' },
  todos: { label: '任务清单', color: 'text-[#a78bfa]', bg: 'bg-[#a78bfa]/12' },
  coverage: { label: '覆盖台账', color: 'text-[#22d3ee]', bg: 'bg-[#22d3ee]/12' },
  threatModel: { label: '威胁模型', color: 'text-accent', bg: 'bg-accent/12' },
  lifecycle: { label: '生命周期', color: 'text-ok', bg: 'bg-ok/12' },
  skills: { label: '加载技能', color: 'text-ok', bg: 'bg-ok/12' },
  browser: { label: '浏览器', color: 'text-accent', bg: 'bg-accent/12' },
  fallback: { label: '工具调用', color: 'text-muted', bg: 'bg-panel2' },
}
function familyOf(toolName: string): string {
  for (const [family, names] of Object.entries(FAMILY_TOOLS)) if (names.includes(toolName)) return family
  for (const [re, family] of FAMILY_MATCH) if (re.test(toolName)) return family
  return 'fallback'
}
function metaOf(ev: TranscriptEvent): { label: string; color: string; bg: string; name?: string } {
  if (ev.type === 'chat') {
    return ev.d.role === 'user'
      ? { label: '用户', color: 'text-accent', bg: 'bg-accent/12' }
      : { label: '思考', color: 'text-[#a78bfa]', bg: 'bg-[#a78bfa]/12' }
  }
  const name = ev.d.tool_name || 'tool'
  return { ...(FAMILY_META[familyOf(name)] || FAMILY_META.fallback), name }
}

const ARG_LABELS: Record<string, string> = {
  cmd: '命令', query: '检索词', name: '名称', task: '任务', title: '标题', severity: '严重度',
  path: '路径', file: '文件', message: '内容', content: '内容', skill: '技能', url: '地址',
  instruction: '指令', reason: '原因', summary: '摘要', report_id: '报告', note_name: '笔记',
  query_string: '查询', pattern: '模式', description: '描述', endpoint: '端点', category: '分类',
}
function argLines(ev: TranscriptEvent): [string, string][] {
  if (ev.type !== 'tool') return []
  let obj: Record<string, unknown> | null = null
  try { obj = JSON.parse(ev.d.args || '') as Record<string, unknown> } catch { return [] }
  if (!obj || typeof obj !== 'object') return []
  const out: [string, string][] = []
  for (const [k, v] of Object.entries(obj)) {
    const text = typeof v === 'string' ? v : JSON.stringify(v)
    if (!text || text === '{}' || text === '[]') continue
    out.push([ARG_LABELS[k] || k, text.length > 220 ? text.slice(0, 220) + '…' : text])
  }
  return out.slice(0, 6)
}
function cmdOf(ev: TranscriptEvent): string {
  if (ev.type !== 'tool') return ''
  try {
    const obj = JSON.parse(ev.d.args || '') as Record<string, unknown>
    return typeof obj?.cmd === 'string' ? obj.cmd : ''
  } catch { return '' }
}
function safeParsed(s: string | undefined): Record<string, unknown> {
  try { return JSON.parse(s || '') as Record<string, unknown> } catch { return {} }
}
function prettyArgs(ev: TranscriptEvent): string {
  if (!ev.d.args) return ''
  try { return JSON.stringify(JSON.parse(ev.d.args), null, 2) } catch { return ev.d.args }
}

// ---- 按虚拟时钟截取步骤：rel ≤ vt 的最后 FEED_MAX 条（时间正序，播放时跟随） ----
const FEED_MAX = 400
interface StepView { ev: TranscriptEvent; rel: number; absIdx: number }

const alignedSteps = computed<StepView[]>(() => {
  const t = transcript.value
  if (!t) return []
  const t0 = timeBase.value || t.started_at
  return t.events.map((ev, absIdx) => ({ ev, rel: Math.max(0, ev.ts - t0), absIdx }))
})
const stepEndIdx = computed(() => {
  const arr = alignedSteps.value
  const now = vt.value
  let lo = 0, hi = arr.length - 1, ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid].rel <= now) { ans = mid; lo = mid + 1 } else { hi = mid - 1 }
  }
  return ans
})
const hiddenSteps = computed(() => Math.max(0, stepEndIdx.value + 1 - FEED_MAX))
const stepSlice = computed<StepView[]>(() => {
  const end = stepEndIdx.value
  if (end < 0) return []
  const start = Math.max(0, end - FEED_MAX + 1)
  return alignedSteps.value.slice(start, end + 1)
})

// 播放/直播时自动滚动跟随最新步骤；用户上翻即暂停跟随
const stepsEl = ref<HTMLElement | null>(null)
const follow = ref(true)
function onStepsScroll() {
  const el = stepsEl.value
  if (!el) return
  follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}
watch([stepEndIdx, () => stepSlice.value.length], async () => {
  if ((!playing.value && !props.live) || !follow.value) return
  await nextTick()
  const el = stepsEl.value
  if (el) el.scrollTop = el.scrollHeight
})

// ===================== 引擎日志事件流（步骤数据缺失时的回退） =====================
type Group = 'milestone' | 'tool' | 'turn'
const groupOfKind: Record<ReplayEventKind, Group> = {
  engine: 'milestone', agent: 'milestone', finding: 'milestone', warn: 'milestone', error: 'milestone',
  tool: 'tool', turn: 'turn',
}
const showMilestone = ref(true)
const showTool = ref(true)
const showTurn = ref(false)
const LOG_FEED_MAX = 120

const feedAll = computed<ReplayEvent[]>(() =>
  (replay.value?.events || []).filter(e =>
    (groupOfKind[e.k] === 'milestone' && showMilestone.value)
    || (groupOfKind[e.k] === 'tool' && showTool.value)
    || (groupOfKind[e.k] === 'turn' && showTurn.value)))

// vt 的已发生右边界（feedAll 按 t 升序，二分）
const logEndIdx = computed(() => {
  const arr = feedAll.value
  const now = vt.value
  let lo = 0, hi = arr.length - 1, ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid].t <= now) { ans = mid; lo = mid + 1 } else { hi = mid - 1 }
  }
  return ans
})
const logSlice = computed<{ ev: ReplayEvent; seq: number }[]>(() => {
  const end = logEndIdx.value
  const out: { ev: ReplayEvent; seq: number }[] = []
  for (let i = Math.max(0, end - LOG_FEED_MAX + 1); i <= end; i++) out.push({ ev: feedAll.value[i], seq: i })
  out.reverse()
  return out
})

const ICON_OF_KIND: Record<ReplayEventKind, string> = {
  engine: '⚙', agent: '◆', tool: '▸', turn: '·', finding: '⚑', warn: '⚠', error: '✖',
}
const COLOR_OF_KIND: Record<ReplayEventKind, string> = {
  engine: '#4f8cff', agent: '#a78bfa', tool: '#3dd68c', turn: '#8b96ad',
  finding: '#f5a524', warn: '#f0b429', error: '#e5484d',
}

const unavailable = computed(() => replayMiss.value && !transcript.value)
const hasData = computed(() => !!replay.value || !!transcript.value)
</script>

<template>
  <!-- 内嵌于智能体页 card 的独立分区：时间轴 + 执行步骤一体化回放 -->
  <section class="mt-5 overflow-hidden rounded-xl border border-border bg-panel2/40">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-3">
      <span class="text-[14px] font-bold text-text">执行过程回放</span>
      <span class="text-xs text-muted">播放到哪一时刻，下方即展示该时刻前智能体的思考、命令与工具调用</span>
      <span
        v-if="live"
        class="inline-flex items-center gap-1.5 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent"
        title="任务运行中：定期重拉引擎数据，播放到当前尾部后驻留等待新事件"
      >
        <span class="inline-block size-2 animate-pulse rounded-full bg-accent"></span>
        实时跟拍中
      </span>
      <span v-if="replay" class="text-xs text-muted">
        run {{ replay.run }} · 引擎 {{ replay.image }} · {{ live ? '已进行' : '全程' }} {{ fmtRel(replay.duration_sec) }}
        · 北京时间 {{ replay.started_at.slice(11, 19) }} 起
      </span>
      <div class="flex-1"></div>
      <template v-if="replay">
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="各智能体累计轮次">{{ replay.totals.turns }} 轮</span>
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="LLM 请求总数">{{ replay.totals.llm_calls }} 次 LLM</span>
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="工具调用总数">{{ replay.totals.tool_calls }} 次工具</span>
        <span class="rounded-full bg-high/10 px-2.5 py-0.5 text-xs font-semibold text-high" title="落盘漏洞报告累计">{{ replay.totals.findings }} 条发现</span>
      </template>
    </div>

    <div v-if="!hasData" class="px-4 py-6 text-sm text-muted">
      {{ unavailable || '解析引擎数据中，大任务可能需要几秒…' }}<template v-if="live && unavailable">（自动重试，产生数据后自动开始跟拍）</template>
    </div>

    <template v-else>
      <!-- 播放控制 -->
      <div class="flex flex-wrap items-center gap-2.5 px-4 pt-3.5">
        <button
          class="inline-flex h-8 cursor-pointer items-center justify-center rounded-lg border transition-colors disabled:cursor-not-allowed disabled:opacity-40"
          :class="playing ? 'w-9 border-border bg-panel text-text hover:border-accent hover:text-accent' : 'border-accent bg-accent/15 px-3 text-accent hover:bg-accent/25'"
          :disabled="dur <= 0"
          :title="playing ? '暂停' : '播放'"
          @click="toggle"
        >
          <span v-if="playing">❚❚</span>
          <span v-else>▶ 播放</span>
        </button>
        <button
          class="inline-flex h-8 cursor-pointer items-center justify-center rounded-lg border border-border bg-panel px-2.5 text-xs text-muted transition-colors hover:border-accent hover:text-accent"
          title="回到起点并停止"
          @click="reset"
        >⟲ 重置</button>
        <button
          class="inline-flex h-8 w-14 cursor-pointer items-center justify-center rounded-lg border border-border bg-panel font-mono text-xs font-semibold text-accent transition-colors hover:bg-accent/15"
          title="切换倍速"
          @click="bumpSpeed(1)"
        >{{ speed }}×</button>

        <span class="ml-1 font-mono text-[13px] text-text" :title="'北京时间 ' + wallClock(vt)">
          {{ fmtRel(vt) }} <span class="text-muted">/ {{ fmtRel(dur) }}</span>
        </span>
        <span v-if="playing" class="inline-flex items-center gap-1.5 text-xs text-accent">
          <span class="inline-block size-2 animate-pulse rounded-full bg-accent"></span>
          {{ live && vt >= dur ? '跟随直播中' : '回放中' }}
        </span>
      </div>

      <!-- 执行密度图 + 里程碑标记 + 播放头（无日志时间轴时退化为细进度条） -->
      <div
        v-if="replay"
        ref="barEl"
        class="relative mx-4 mt-3 h-20 cursor-crosshair touch-none select-none overflow-hidden rounded-lg border border-border bg-panel"
        @pointerdown="onBarDown" @pointermove="onBarMove" @pointerup="onBarUp" @pointercancel="onBarUp"
      >
        <div class="absolute inset-x-0 bottom-0 top-6 flex items-end gap-px">
          <!-- 柱体各自绝对定位锚在列底部（父列 self-stretch 定高），浅蓝=LLM 全柱，实心蓝=工具叠加 -->
          <div v-for="(b, i) in chartBuckets" :key="i" class="relative min-w-0 flex-1 self-stretch">
            <div class="absolute inset-x-0 bottom-0 bg-accent/35" :style="{ height: `${b.llmH}%` }"></div>
            <div class="absolute inset-x-0 bottom-0 bg-accent" :style="{ height: `${b.toolsH}%`, opacity: 0.9 }"></div>
          </div>
        </div>
        <!-- 里程碑标记列 -->
        <div
          v-for="(mk, i) in markers" :key="i"
          class="absolute top-0 z-10 flex -translate-x-1/2 flex-col items-center leading-none"
          :style="{ left: `${mk.pct}%`, color: mk.color }"
          :title="mk.tip"
        >
          <span class="text-[11px]">{{ mk.icon }}</span>
          <span class="mt-0.5 block w-px opacity-40" :style="{ height: '18px', backgroundColor: mk.color }"></span>
        </div>
        <!-- 播放头 -->
        <div class="absolute inset-y-0 z-20 w-px bg-text/70" :style="{ left: `${playheadPct}%` }"></div>
        <div class="absolute top-1 left-1.5 z-30 rounded bg-bg/80 px-1.5 py-0.5 text-[10px] text-muted">
          执行密度：实心蓝 工具调用 · 浅蓝 LLM 请求 ⏱ {{ wallClock(vt) }}
        </div>
      </div>
      <div v-else class="relative mx-4 mt-3 h-1.5 overflow-hidden rounded-full bg-panel">
        <div class="h-full rounded-full bg-accent" :style="{ width: `${playheadPct}%` }"></div>
      </div>

      <!-- 智能体泳道 -->
      <div v-if="replay" class="mt-3 px-4">
        <div class="mb-1.5 text-[11px] font-semibold tracking-wide text-muted">
          智能体泳道（点击名称定位到启动时刻）
        </div>
        <div v-for="l in lanes" :key="l.id" class="mb-1 flex items-center gap-2">
          <button
            class="w-28 shrink-0 truncate rounded px-1.5 py-0.5 text-right text-[11px] leading-tight sm:w-36 sm:text-xs"
            :title="`${l.name} · ${l.parentName ? `由 ${l.parentName} 创建` : '根智能体'}${l.open ? ' · 未观测到收工标记' : ''}`"
            :class="l.active ? 'bg-accent/15 font-semibold text-accent' : 'text-muted hover:text-text'"
            @click="seekTo(l.t0)"
          >{{ l.name }}</button>
          <div class="relative h-3.5 flex-1 overflow-hidden rounded border border-border/60 bg-panel">
            <div
              class="absolute inset-y-0 rounded"
              :style="{
                left: `${l.leftPct}%`,
                width: `${l.widthPct}%`,
                backgroundColor: l.color,
                opacity: l.active ? 1 : 0.45,
              }"
              :title="`${fmtRel(l.t0)} 开始${l.open ? '，结束时刻未知' : ''}`"
            ></div>
          </div>
        </div>
        <div v-if="lanes.some(l => l.open)" class="mt-1 text-[11px] text-muted">
          ◆ 泳道上无收工标记的智能体可能在任务终止时尚未完成。
        </div>
      </div>

      <!-- ============ 执行步骤（会话库事件流，跟随虚拟时钟） ============ -->
      <template v-if="transcript">
        <!-- 智能体筛选 -->
        <div class="mt-3.5 flex flex-wrap items-center gap-1.5 border-t border-border px-4 pt-3">
          <span class="mr-1 text-[11px] font-semibold tracking-wide text-muted">步骤筛选：</span>
          <button
            class="cursor-pointer rounded-full border px-3 py-0.5 text-xs font-semibold transition-colors"
            :class="selected === 'all' ? 'border-accent bg-accent/12 text-accent' : 'border-border text-muted hover:text-text'"
            @click="pick('all')"
          >全部（{{ transcript.counts.all?.total || 0 }}）</button>
          <button
            v-for="a in agentOptions" :key="a.id"
            class="inline-flex max-w-64 cursor-pointer items-center gap-1.5 rounded-full border px-3 py-0.5 text-xs font-semibold transition-colors"
            :class="selected === a.id ? 'border-accent bg-accent/12 text-accent' : 'border-border text-muted hover:text-text'"
            :title="`${a.parentName ? `由 ${a.parentName} 派生` : '根智能体'} · ${a.status || '状态未知'}`"
            @click="pick(a.id)"
          >
            <span class="inline-block size-1.5 shrink-0 rounded-full" :class="AGENT_STATUS_DOT[a.status] || 'bg-border'"></span>
            <span class="truncate">{{ a.name }}</span>
            <span class="shrink-0 opacity-60">{{ a.total }}</span>
          </button>
          <div class="flex-1"></div>
          <span class="text-xs text-muted">
            已到 {{ stepEndIdx + 1 }} / {{ transcript.total_events }} 条
            <template v-if="transcript.truncated"> · 长任务已抽稀</template>
          </span>
        </div>

        <!-- 步骤流 -->
        <div
          ref="stepsEl"
          class="mx-4 my-3 max-h-96 min-h-24 overflow-y-auto rounded-lg border border-border bg-panel px-2 py-1.5"
          @scroll="onStepsScroll"
        >
          <div v-if="hiddenSteps > 0" class="px-2 py-1 text-center text-[11px] text-muted">
            前面 {{ hiddenSteps }} 条已省略（拖动进度条或重播可回看；最多保留最近 {{ FEED_MAX }} 条）
          </div>
          <div v-if="stepSlice.length === 0" class="px-2 py-4 text-center text-sm text-muted">
            当前位置之前还没有步骤——点击「▶ 播放」或拖动上方进度条开始回看执行过程。
          </div>
          <div v-for="s in stepSlice" :key="s.absIdx" class="flex items-start gap-2.5 rounded-lg px-1.5 py-1.5 hover:bg-panel2/60">
            <span class="shrink-0 pt-1 font-mono text-[11px] leading-4 text-muted" :title="'相对 ' + fmtRel(s.rel)">{{ wallAbs(s.ev.ts) }}</span>

            <!-- 思考 / 用户消息 -->
            <template v-if="s.ev.type === 'chat'">
              <span
                class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold leading-4"
                :class="metaOf(s.ev).bg + ' ' + metaOf(s.ev).color"
              >{{ metaOf(s.ev).label }}</span>
              <div
                class="min-w-0 flex-1 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed"
                :class="s.ev.d.role === 'user' ? 'text-text' : 'italic text-muted'"
              >{{ s.ev.d.content }}</div>
            </template>

            <!-- 工具调用 -->
            <template v-else>
              <span
                class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold leading-4"
                :class="metaOf(s.ev).bg + ' ' + metaOf(s.ev).color"
              >{{ metaOf(s.ev).label }}</span>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <code class="break-all font-mono text-[12px] text-body" :class="s.ev.d.status === 'failed' ? 'text-crit' : ''">
                    {{ metaOf(s.ev).name }}
                  </code>
                  <span
                    v-if="s.ev.d.status === 'failed'"
                    class="rounded bg-crit/15 px-1.5 py-px text-[10px] font-semibold text-crit"
                  >失败</span>
                  <span v-else-if="s.ev.d.status === 'running'" class="rounded bg-accent/15 px-1.5 py-px text-[10px] font-semibold text-accent">执行中</span>
                </div>
                <!-- 终端命令：bash 代码块（点击展开/收起） -->
                <pre
                  v-if="cmdOf(s.ev)"
                  class="mt-1 max-h-40 cursor-pointer overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-panel2/60 px-2.5 py-1.5 font-mono text-[12px] leading-relaxed text-ok"
                  title="点击展开/收起（超长自动折叠）"
                  @click="($event.currentTarget as HTMLElement).classList.toggle('max-h-40')"
                >{{ cmdOf(s.ev) }}</pre>
                <!-- 其余要点字段 -->
                <div v-for="([k, v], j) in argLines(s.ev).filter(([k]) => k !== '命令')" :key="j" class="mt-0.5 text-[12.5px] leading-relaxed">
                  <span class="font-semibold text-muted">{{ k }}：</span><span class="break-all text-body">{{ v }}</span>
                </div>
                <!-- 输出结果：默认折叠 -->
                <details v-if="s.ev.d.result">
                  <summary class="mt-0.5 inline cursor-pointer select-none text-[11.5px] text-muted hover:text-accent">
                    输出（{{ s.ev.d.result.length }} 字）{{ s.ev.d.status === 'failed' ? ' · 失败详情' : '' }}
                  </summary>
                  <pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-panel2/60 px-2.5 py-1.5 font-mono text-[12px] leading-relaxed text-body">{{ s.ev.d.result }}</pre>
                </details>
                <!-- 完整参数：默认折叠 -->
                <details v-if="prettyArgs(s.ev) && Object.keys(safeParsed(s.ev.d.args)).length > 1">
                  <summary class="mt-0.5 inline cursor-pointer select-none text-[11.5px] text-muted hover:text-accent">完整参数</summary>
                  <pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-panel2/60 px-2.5 py-1.5 font-mono text-[12px] leading-relaxed text-body">{{ prettyArgs(s.ev) }}</pre>
                </details>
              </div>
            </template>
          </div>
        </div>
      </template>

      <!-- ============ 回退：引擎日志事件流（无步骤数据时） ============ -->
      <template v-else-if="replay">
        <div class="mt-3.5 flex flex-wrap items-center gap-2 border-t border-border px-4 pt-3">
          <span class="text-[11px] font-semibold tracking-wide text-muted">事件流筛选：</span>
          <button
            class="cursor-pointer rounded-full border px-3 py-0.5 text-xs font-semibold transition-colors"
            :class="showMilestone ? 'border-accent bg-accent/12 text-accent' : 'border-border text-muted hover:text-text'"
            @click="showMilestone = !showMilestone"
          >关键节点</button>
          <button
            class="cursor-pointer rounded-full border px-3 py-0.5 text-xs font-semibold transition-colors"
            :class="showTool ? 'border-accent bg-accent/12 text-accent' : 'border-border text-muted hover:text-text'"
            @click="showTool = !showTool"
          >工具调用</button>
          <button
            class="cursor-pointer rounded-full border px-3 py-0.5 text-xs font-semibold transition-colors"
            :class="showTurn ? 'border-accent bg-accent/12 text-accent' : 'border-border text-muted hover:text-text'"
            @click="showTurn = !showTurn"
          >轮次心跳</button>
          <div class="flex-1"></div>
          <span class="text-xs text-muted">最新在前 · 最多 {{ LOG_FEED_MAX }} 条</span>
        </div>

        <div class="mx-4 my-3 max-h-72 min-h-24 overflow-y-auto rounded-lg border border-border bg-panel px-2 py-1.5 font-mono text-xs">
          <div v-if="logSlice.length === 0" class="px-2 py-3 text-muted">当前位置之前还没有筛选范围内的事件。</div>
          <div v-for="item in logSlice" :key="item.seq" class="flex items-start gap-2 rounded px-2 py-[3px] hover:bg-panel2/70">
            <span class="shrink-0 pt-px text-muted" style="font-size: 11px">{{ wallClock(item.ev.t) }}</span>
            <span class="w-3.5 shrink-0 text-center leading-4" :style="{ color: COLOR_OF_KIND[item.ev.k] }" :title="item.ev.k">
              {{ ICON_OF_KIND[item.ev.k] }}
            </span>
            <span
              class="min-w-0 break-all text-body"
              :class="{ 'font-semibold text-crit': item.ev.k === 'error', 'text-text': item.ev.k === 'finding' || item.ev.k === 'engine' }"
            >{{ item.ev.m }}</span>
          </div>
        </div>
      </template>
    </template>
  </section>
</template>
