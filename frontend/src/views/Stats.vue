<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api, type StatsData } from '../api'
import { toast } from '../toast'
import { card, cardLifted, hint, tableTd, tableTh } from '../ui'

const data = ref<StatsData | null>(null)
const loading = ref(false)

async function refresh() {
  loading.value = true
  try {
    data.value = await api.stats()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
onMounted(refresh)

const statusLabels: Record<string, string> = {
  pending: '等待中', fetching: '拉取代码', scanning: '扫描中',
  parsing: '解析报告', translating: '翻译中', done: '已完成', failed: '失败',
}
const modeLabels: Record<string, string> = { quick: 'quick（快速）', standard: 'standard（标准）', deep: 'deep（深度）' }
const sevOrder = ['critical', 'high', 'medium', 'low', 'info'] as const
const sevLabels: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危', info: '提示' }
const sevColors: Record<string, string> = {
  critical: 'bg-crit', high: 'bg-high', medium: 'bg-med', low: 'bg-low', info: 'bg-accent',
}

const criticalAndHigh = computed(() =>
  (data.value?.findings_by_severity.critical || 0) + (data.value?.findings_by_severity.high || 0),
)

const sevEntries = computed(() => {
  const counts = data.value?.findings_by_severity || {}
  return sevOrder
    .map((sev) => ({ sev, count: counts[sev] || 0 }))
    .filter((e) => e.count > 0)
})

const statusEntries = computed(() => {
  const counts = data.value?.tasks_by_status || {}
  return Object.entries(counts)
    .map(([k, v]) => ({ key: k, label: statusLabels[k] || k, count: v }))
    .sort((a, b) => b.count - a.count)
})

const modeEntries = computed(() => {
  const counts = data.value?.tasks_by_mode || {}
  return Object.entries(counts)
    .map(([k, v]) => ({ key: k, label: modeLabels[k] || k, count: v }))
    .sort((a, b) => b.count - a.count)
})

const modelEntries = computed(() => {
  const counts = data.value?.tasks_by_model || {}
  const tokens = data.value?.tokens_by_model || {}
  const keys = new Set([...Object.keys(counts), ...Object.keys(tokens)])
  return [...keys]
    .map((k) => ({ key: k, label: k || '（默认）', count: counts[k] || 0, tokens: tokens[k] || 0 }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)
})

const trendMax = computed(() => Math.max(1, ...(data.value?.trend || []).map((p) => p.count)))
const tokenMax = computed(() => Math.max(1, ...(data.value?.trend || []).map((p) => p.tokens)))

// 折线用 0-100 的虚拟坐标系（preserveAspectRatio="none" 拉伸铺满），x 与各柱中心对齐
const tokenLinePoints = computed(() => {
  const trend = data.value?.trend || []
  return trend
    .map((p, i) => {
      const x = (((i + 0.5) / trend.length) * 100).toFixed(2)
      const y = (100 - Math.min(1, p.tokens / tokenMax.value) * 92).toFixed(2)
      return `${x},${y}`
    })
    .join(' ')
})

function fmtDuration(sec: number | null): string {
  if (sec == null) return '-'
  if (sec < 60) return `${Math.round(sec)} 秒`
  if (sec < 3600) return `${(sec / 60).toFixed(1)} 分钟`
  return `${(sec / 3600).toFixed(1)} 小时`
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function fmtDay(iso: string): string {
  return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`
}

function openProject(id: string) {
  location.hash = `#/project/${id}`
}

const kpis = computed(() => [
  { label: '项目', value: data.value?.projects_total ?? '-', sub: data.value?.projects_archived ? `另有 ${data.value.projects_archived} 个已归档` : '进行中的项目' },
  { label: '扫描任务', value: data.value?.tasks_total ?? '-', sub: data.value?.avg_duration_sec != null ? `平均耗时 ${fmtDuration(data.value.avg_duration_sec)}` : '全部任务' },
  { label: '发现漏洞', value: data.value?.findings_total ?? '-', sub: data.value?.tasks_total ? `平均每任务 ${(data.value.findings_total / data.value.tasks_total).toFixed(1)} 个` : '全部漏洞' },
  { label: '严重 / 高危', value: criticalAndHigh.value || 0, sub: '需要优先修复', danger: true },
  {
    label: 'Token 消耗',
    value: fmtTokens(data.value?.total_tokens || 0),
    sub: `输入 ${fmtTokens(data.value?.total_input_tokens || 0)} / 输出 ${fmtTokens(data.value?.total_output_tokens || 0)} · ${fmtTokens(data.value?.llm_requests_total || 0)} 次请求`,
  },
])
</script>

<template>
  <div>
    <!-- 页头 -->
    <div class="mb-[18px] flex items-end gap-3">
      <div>
        <h1 class="text-[28px] leading-tight font-bold text-text">统计汇总</h1>
        <p class="mt-1 text-[13px] text-muted">
          {{ data?.scope === 'mine' ? '你创建的项目与任务' : '全平台项目与任务' }}的扫描与漏洞情况一览。
        </p>
      </div>
      <div class="flex-1"></div>
      <button
        class="cursor-pointer rounded-lg border border-border bg-panel px-4 py-2 text-[13px] font-semibold text-muted shadow-soft hover:border-accent hover:text-accent disabled:opacity-50"
        :disabled="loading"
        @click="refresh"
      >{{ loading ? '加载中…' : '刷新' }}</button>
    </div>

    <template v-if="data">
      <!-- KPI 卡片 -->
      <div class="mb-[18px] grid grid-cols-2 gap-3.5 lg:grid-cols-5">
        <div
          v-for="k in kpis" :key="k.label"
          :class="cardLifted" class="p-4.5"
        >
          <div class="text-[13px] font-semibold text-muted">{{ k.label }}</div>
          <div class="mt-1 text-[32px] leading-none font-bold" :class="k.danger && k.value ? 'text-crit' : 'text-text'">
            {{ k.value }}
          </div>
          <div class="mt-2 text-xs text-muted">{{ k.sub }}</div>
        </div>
      </div>

      <div class="grid gap-[18px] lg:grid-cols-2">
        <!-- 近 14 天任务趋势 -->
        <div :class="card">
          <h3 class="text-sm font-semibold text-muted">
            近 14 天任务趋势
            <span class="ml-1.5 font-normal">（累计 {{ fmtTokens(data.trend.reduce((s, p) => s + p.tokens, 0)) }} tokens）</span>
          </h3>
          <div class="mt-2 flex items-center gap-4 text-[11.5px] text-muted">
            <span class="flex items-center gap-1.5"><span class="inline-block size-2.5 rounded-[3px] bg-accent/80"></span>任务数</span>
            <span class="flex items-center gap-1.5"><span class="inline-block h-0.5 w-4 rounded-full bg-ok"></span>Token 消耗</span>
            <span class="ml-auto">峰值 {{ fmtTokens(tokenMax) }} / 天</span>
          </div>
          <!-- 柱与折线共享 0-100 高度但各自独立缩放；列内 padding 取代 gap，保证折线节点对准柱中心 -->
          <div class="relative mt-4 flex h-40">
            <div
              v-for="p in data.trend" :key="p.date"
              class="group flex h-full flex-1 flex-col justify-end px-[3px]"
              :title="`${p.date}：${p.count} 个任务 · ${fmtTokens(p.tokens)} tokens`"
            >
              <div
                class="rounded-t-[4px] bg-accent/80 transition-colors group-hover:bg-accent"
                :style="{ height: `${Math.max(p.count ? 6 : 2, (p.count / trendMax) * 100)}%` }"
              ></div>
            </div>
            <svg
              v-if="tokenLinePoints"
              class="pointer-events-none absolute inset-0 h-full w-full"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
            >
              <polyline
                :points="tokenLinePoints"
                fill="none"
                class="stroke-ok"
                stroke-width="2"
                stroke-linejoin="round"
                stroke-linecap="round"
                vector-effect="non-scaling-stroke"
              />
            </svg>
          </div>
          <div class="mt-1.5 flex text-[10.5px] text-muted">
            <div v-for="p in data.trend" :key="p.date" class="flex-1 text-center">{{ fmtDay(p.date) }}</div>
          </div>
        </div>

        <!-- 漏洞严重度分布 -->
        <div :class="card">
          <h3 class="text-sm font-semibold text-muted">漏洞严重度分布</h3>
          <template v-if="sevEntries.length">
            <div class="mt-4 flex h-3 w-full overflow-hidden rounded-full bg-panel2">
              <div
                v-for="e in sevEntries" :key="e.sev"
                :class="sevColors[e.sev]"
                :style="{ width: `${(e.count / (data.findings_total || 1)) * 100}%` }"
                :title="`${sevLabels[e.sev]}：${e.count}`"
              ></div>
            </div>
            <div class="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
              <div v-for="e in sevEntries" :key="e.sev" class="flex items-center gap-2 text-[13px]">
                <span class="inline-block size-2.5 rounded-full" :class="sevColors[e.sev]"></span>
                <span class="text-muted">{{ sevLabels[e.sev] }}</span>
                <span class="ml-auto font-semibold text-text">{{ e.count }}</span>
              </div>
            </div>
          </template>
          <p v-else :class="hint" class="mt-3">还没有发现漏洞。发起一次扫描后这里会展示严重度分布。</p>
        </div>

        <!-- 任务状态分布 -->
        <div :class="card">
          <h3 class="text-sm font-semibold text-muted">任务状态分布</h3>
          <template v-if="statusEntries.length">
            <div v-for="s in statusEntries" :key="s.key" class="mt-3">
              <div class="flex items-center justify-between text-[13px]">
                <span class="text-muted">{{ s.label }}</span>
                <span class="font-semibold text-text">{{ s.count }}</span>
              </div>
              <div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-panel2">
                <div
                  class="h-full rounded-full"
                  :class="s.key === 'failed' ? 'bg-crit' : s.key === 'done' ? 'bg-ok' : 'bg-accent'"
                  :style="{ width: `${(s.count / (data.tasks_total || 1)) * 100}%` }"
                ></div>
              </div>
            </div>
          </template>
          <p v-else :class="hint" class="mt-3">暂无任务。</p>
        </div>

        <!-- 扫描档位与模型使用 -->
        <div :class="card">
          <h3 class="text-sm font-semibold text-muted">扫描档位 / 模型使用</h3>
          <div class="mt-3 grid grid-cols-2 gap-4">
            <div>
              <div class="text-xs font-semibold text-muted">扫描档位</div>
              <div v-for="m in modeEntries" :key="m.key" class="mt-2 flex items-center justify-between text-[13px]">
                <span class="text-muted">{{ m.label }}</span>
                <span class="font-semibold text-text">{{ m.count }}</span>
              </div>
              <p v-if="!modeEntries.length" :class="hint">暂无数据</p>
            </div>
            <div>
              <div class="text-xs font-semibold text-muted">模型（Top 6，含 token 消耗）</div>
              <div v-for="m in modelEntries" :key="m.key" class="mt-2 flex items-center justify-between gap-2 text-[13px]">
                <span class="truncate text-muted" :title="m.key">{{ m.label }}</span>
                <span class="shrink-0">
                  <span class="font-semibold text-text">{{ m.count }}</span>
                  <span class="ml-1.5 text-[11.5px] text-muted">{{ fmtTokens(m.tokens) }} tok</span>
                </span>
              </div>
              <p v-if="!modelEntries.length" :class="hint">暂无数据</p>
            </div>
          </div>
        </div>
      </div>

      <!-- 项目漏洞 Top 5 -->
      <div :class="card" class="!mb-0">
        <h3 class="text-sm font-semibold text-muted">项目漏洞 Top 5</h3>
        <table v-if="data.top_projects.length" class="mt-2 w-full border-collapse">
          <thead>
            <tr>
              <th :class="tableTh">项目</th><th :class="tableTh">任务数</th>
              <th :class="tableTh">漏洞数</th><th :class="tableTh">分布</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in data.top_projects" :key="p.id"
              class="cursor-pointer hover:bg-accent/5"
              @click="openProject(p.id)"
            >
              <td :class="[tableTd, 'font-semibold']">{{ p.name }}</td>
              <td :class="tableTd">{{ p.tasks }}</td>
              <td :class="tableTd">{{ p.findings }}</td>
              <td :class="tableTd">
                <div
                  v-if="p.findings"
                  class="flex h-2.5 w-40 overflow-hidden rounded-full bg-panel2"
                  :title="sevOrder.filter((s) => p.severity_counts[s]).map((s) => `${sevLabels[s]} ${p.severity_counts[s]}`).join(' / ')"
                >
                  <div
                    v-for="s in sevOrder.filter((s) => p.severity_counts[s])" :key="s"
                    :class="sevColors[s]"
                    :style="{ width: `${(p.severity_counts[s] / p.findings) * 100}%` }"
                  ></div>
                </div>
                <span v-else class="text-xs text-muted">-</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else :class="hint" class="mt-2">暂无项目数据。</p>
      </div>
    </template>

    <div v-else-if="!loading" :class="card">
      <p :class="hint">统计加载失败，请点击右上角「刷新」重试。</p>
    </div>
  </div>
</template>
