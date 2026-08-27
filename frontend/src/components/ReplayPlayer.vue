<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, type ReplayData, type ReplayEvent, type ReplayEventKind } from '../api'

/** 执行过程回放播放器：把 strix.log 解析出的事件时间线按虚拟时钟重演。 */
const props = defineProps<{ taskId: string }>()

const loading = ref(false)
const data = ref<ReplayData | null>(null)
const unavailable = ref('')
let requested = false // TabsContent 反复挂载也只请求一次（结果缓存于后端库表）

async function load() {
  if (requested || loading.value) return
  requested = true
  loading.value = true
  try {
    const res = await api.getReplay(props.taskId)
    if (res.available && res.data) data.value = res.data
    else unavailable.value = res.reason || '该任务没有可用于回放的执行日志'
  } catch (e) {
    requested = false // 失败允许再次尝试（比如网络抖动）
    unavailable.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ------------------ 虚拟时钟与播放控制 ------------------
const TICK_MS = 200
const SPEEDS = [1, 8, 30, 120, 600]
const vt = ref(0) // 当前回放位置（秒，相对起点）
const playing = ref(false)
const speedIdx = ref(2)
const speed = computed(() => SPEEDS[speedIdx.value])
const dur = computed(() => data.value?.duration_sec ?? 0)
let timer: number | undefined

function stepTick() {
  if (!data.value) return stop()
  vt.value = Math.min(vt.value + (speed.value * TICK_MS) / 1000, dur.value)
  if (vt.value >= dur.value) stop()
}
function play() {
  if (dur.value <= 0 || playing.value) return
  if (vt.value >= dur.value) vt.value = 0 // 播完后点播放 = 重头再来
  playing.value = true
  timer = window.setInterval(stepTick, TICK_MS)
}
function stop() {
  playing.value = false
  if (timer !== undefined) { clearInterval(timer); timer = undefined }
}
function toggle() { playing.value ? stop() : play() }
function reset() { stop(); vt.value = 0 }
function bumpSpeed(d: number) { speedIdx.value = (speedIdx.value + d + SPEEDS.length) % SPEEDS.length }
function seekTo(sec: number) { vt.value = Math.min(Math.max(0, sec), dur.value - 0.01) }

onUnmounted(stop)

// ------------------ 时间显示 ------------------
// 同一事件的时间戳会被渲染多帧，简单 memo 避免反复构造 Date
const wallCache = new Map<number, string>()
function wallClock(t: number): string {
  const d = data.value
  if (!d) return ''
  const hit = wallCache.get(t)
  if (hit !== undefined) return hit
  const date = new Date(new Date(d.started_at).getTime() + t * 1000)
  const s = Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString('zh-CN', { hour12: false })
  wallCache.set(t, s)
  return s
}
function fmtRel(t: number): string {
  const s = Math.floor(t % 60), m = Math.floor((t / 60) % 60), h = Math.floor(t / 3600)
  return `${h > 0 ? `${h}:` : ''}${String(m).padStart(h > 0 ? 2 : 1, '0')}:${String(s).padStart(2, '0')}`
}

// ------------------ 执行密度图（div 柱状，无 SVG）------------------
// 每桶两段叠柱：实心蓝=工具调用、浅蓝=LLM 调用；轮次心跳不进图（与 LLM 同涨跌）
interface BucketView { toolsH: number; llmH: number }
const chartBuckets = computed<BucketView[]>(() => {
  const act = data.value?.activity || []
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
  const d = data.value
  if (!d || d.duration_sec <= 0) return []
  return d.events
    .filter(e => e.k === 'finding' || e.k === 'warn' || e.k === 'error')
    .map(e => ({
      pct: (e.t / d.duration_sec) * 100,
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
  ;(ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId)
  seekByPointer(ev)
}
function onBarMove(ev: PointerEvent) { if (dragging.value) seekByPointer(ev) }
function onBarUp() { dragging.value = false }

// ------------------ 智能体泳道 ------------------
const LANES_COLORS = ['#4f8cff', '#f5a524', '#46a758', '#a78bfa', '#3dd68c', '#e5484d', '#f0b429', '#00c8d7']
const lanes = computed(() => {
  const d = data.value
  if (!d || d.duration_sec <= 0) return []
  return d.agents.map((a, i) => ({
    id: a.id,
    name: a.name,
    parentName: a.parent_name,
    t0: a.t0,
    open: a.t1 == null,
    color: LANES_COLORS[i % LANES_COLORS.length],
    leftPct: Math.min(99.9, (a.t0 / d.duration_sec) * 100),
    widthPct: Math.max(0.4, ((Math.max(a.t1 ?? vt.value, a.t0 + 1) - a.t0) / d.duration_sec) * 100),
    active: vt.value >= a.t0 && vt.value < (a.t1 ?? Infinity),
  }))
})

// ------------------ 事件流筛选与窗口 ------------------
type Group = 'milestone' | 'tool' | 'turn'
const groupOfKind: Record<ReplayEventKind, Group> = {
  engine: 'milestone', agent: 'milestone', finding: 'milestone', warn: 'milestone', error: 'milestone',
  tool: 'tool', turn: 'turn',
}
const showMilestone = ref(true)
const showTool = ref(true)
const showTurn = ref(false)
const FEED_WINDOW = 120

const feedAll = computed<ReplayEvent[]>(() =>
  (data.value?.events || []).filter(e =>
    (groupOfKind[e.k] === 'milestone' && showMilestone.value)
    || (groupOfKind[e.k] === 'tool' && showTool.value)
    || (groupOfKind[e.k] === 'turn' && showTurn.value)))

// vt 的已发生右边界（feedAll 按 t 升序，二分）
const feedEndIdx = computed(() => {
  const arr = feedAll.value
  const now = vt.value
  let lo = 0, hi = arr.length - 1, ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid].t <= now) { ans = mid; lo = mid + 1 } else { hi = mid - 1 }
  }
  return ans
})
const feedSlice = computed<{ ev: ReplayEvent; seq: number }[]>(() => {
  const end = feedEndIdx.value
  const out: { ev: ReplayEvent; seq: number }[] = []
  for (let i = Math.max(0, end - FEED_WINDOW + 1); i <= end; i++) out.push({ ev: feedAll.value[i], seq: i })
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
</script>

<template>
  <!-- 内嵌于智能体页 card 的独立分区 -->
  <section class="mt-5 overflow-hidden rounded-xl border border-border bg-panel2/40">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-3">
      <span class="text-[14px] font-bold text-text">执行过程回放</span>
      <span v-if="data" class="text-xs text-muted">
        run {{ data.run }} · 引擎 {{ data.image }} · 全程 {{ fmtRel(data.duration_sec) }}
        · 北京时间 {{ data.started_at.slice(11, 19) }} 起
      </span>
      <div class="flex-1"></div>
      <template v-if="data">
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="各智能体累计轮次">{{ data.totals.turns }} 轮</span>
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="LLM 请求总数">{{ data.totals.llm_calls }} 次 LLM</span>
        <span class="rounded-full bg-panel px-2.5 py-0.5 text-xs text-muted" title="工具调用总数">{{ data.totals.tool_calls }} 次工具</span>
        <span class="rounded-full bg-high/10 px-2.5 py-0.5 text-xs font-semibold text-high" title="落盘漏洞报告累计">{{ data.totals.findings }} 条发现</span>
      </template>
    </div>

    <div v-if="loading" class="px-4 py-6 text-sm text-muted">解析引擎日志中，大任务可能需要几秒…</div>
    <div v-else-if="!data" class="px-4 py-6 text-sm text-muted">{{ unavailable }}</div>

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
          回放中
        </span>
      </div>

      <!-- 执行密度图 + 里程碑标记 + 播放头 -->
      <div
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

      <!-- 智能体泳道 -->
      <div class="mt-3 px-4">
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

      <!-- 事件流筛选 -->
      <div class="mt-3.5 flex flex-wrap items-center gap-2 px-4">
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
        <span class="text-xs text-muted">最新在前 · 最多 {{ FEED_WINDOW }} 条</span>
      </div>

      <!-- 事件流 -->
      <div class="mx-4 my-3 max-h-72 min-h-24 overflow-y-auto rounded-lg border border-border bg-panel px-2 py-1.5 font-mono text-xs">
        <div v-if="feedSlice.length === 0" class="px-2 py-3 text-muted">当前位置之前还没有筛选范围内的事件。</div>
        <div v-for="item in feedSlice" :key="item.seq" class="flex items-start gap-2 rounded px-2 py-[3px] hover:bg-panel2/70">
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
  </section>
</template>
