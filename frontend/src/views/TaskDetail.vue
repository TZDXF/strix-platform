<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { TabsRoot, TabsList, TabsTrigger, TabsContent, CheckboxRoot, CheckboxIndicator } from 'reka-ui'
import { api, type AgentUsage, type Finding, type TaskDetail as TaskDetailData } from '../api'
import MarkdownRender from 'markstream-vue'
import { toast } from '../toast'
import {
  btn, btnGhost, card, cardLifted, err as errCls, findingBarClass, hint, logPre,
  sevBadgeClass, sevLabel, statusBadgeClass, tableTd, tableTh,
} from '../ui'

const props = defineProps<{ taskId: string }>()
const task = ref<TaskDetailData | null>(null)
const logText = ref('')
const logBox = ref<HTMLElement | null>(null)
const expanded = ref<Record<number, boolean>>({})
const showZh = ref(true)
const tab = ref('findings')
let timer: number | undefined

const running = computed(() => ['pending', 'fetching', 'scanning', 'parsing'].includes(task.value?.status || ''))

// 运行中日志持续追加，自动滚动到底部跟随最新输出
watch(logText, async () => {
  if (!running.value) return
  await nextTick()
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
})
const zhReady = computed(() => (task.value?.findings || []).some(f => f.title_zh))
const reportMd = computed(() => task.value?.report_md || '')
// 主题在 html.light 类上生效，监听类变化让报告渲染同步亮暗色
const isDark = ref(!document.documentElement.classList.contains('light'))
let themeObserver: MutationObserver | undefined

const statusLabels: Record<string, string> = {
  pending: '等待中', fetching: '拉取代码', scanning: '扫描中',
  parsing: '解析报告', done: '已完成', failed: '失败',
}

const sevOrder = ['critical', 'high', 'medium', 'low', 'info']
const sevGrid = computed(() => {
  const c = task.value?.severity_counts || {}
  return sevOrder.map(s => ({ key: s, label: sevLabel[s], count: c[s] || 0 }))
})
const criticalPlus = computed(() =>
  (task.value?.severity_counts?.critical || 0) + (task.value?.severity_counts?.high || 0))

// 严重度数字与占比条的颜色（静态映射，供模板安全引用）
const sevTextCls: Record<string, string> = {
  critical: 'text-crit', high: 'text-high', medium: 'text-med', low: 'text-low', info: 'text-accent',
}
const sevBarCls: Record<string, string> = {
  critical: 'bg-crit', high: 'bg-high', medium: 'bg-med', low: 'bg-low', info: 'bg-accent',
}

// ---- 漏洞明细筛选：按严重度 + 是否含 PoC ----
const sevFilter = ref('all')
const onlyPoc = ref(false)
const filteredFindings = computed(() =>
  (task.value?.findings || []).filter(f =>
    (sevFilter.value === 'all' || f.severity === sevFilter.value) && (!onlyPoc.value || f.has_poc)))
const allExpanded = computed(() =>
  filteredFindings.value.length > 0 && filteredFindings.value.every(f => expanded.value[f.id]))
function expandAll(open: boolean) {
  for (const f of task.value?.findings || []) expanded.value[f.id] = open
}

// ---- 漏洞按目标分组：白盒各仓库 / 黑盒各地址 / 未标注 ----
// target 由引擎逐条填写（受影响仓库或源码路径 / 黑盒地址），与任务绑定的仓库和
// 黑盒地址匹配归类；匹配不上的 URL 视为黑盒、本地路径视为白盒（兼容历史任务）
interface FindingGroup { key: string; kind: 'whitebox' | 'blackbox' | 'unknown'; name: string; findings: Finding[] }

function repoNameOf(url: string): string {
  const seg = url.replace(/\/+$/, '').split(/[/:]/).pop() || url
  return seg.endsWith('.git') ? seg.slice(0, -4) : seg
}
const repoDefs = computed(() => {
  const t = task.value
  if (!t || t.source_type !== 'git') return [] as { url: string; name: string }[]
  const refs = t.repo_branches?.length ? t.repo_branches : [{ url: t.source_ref, branch: '' }]
  return refs.map(r => ({ url: r.url, name: repoNameOf(r.url) })).filter(r => r.url)
})
const targetDefs = computed(() => task.value?.test_targets || [])

function groupOf(f: Finding): { key: string; kind: FindingGroup['kind']; name: string } {
  const t = (f.target || '').trim()
  if (!t) return { key: 'unknown', kind: 'unknown', name: '未标注目标' }
  for (const r of repoDefs.value) {
    if (t.includes(r.url) || t.includes(r.name)) return { key: `wb:${r.name}`, kind: 'whitebox', name: r.name }
  }
  const norm = (u: string) => u.replace(/\/+$/, '')
  for (const tt of targetDefs.value) {
    if (t === tt.url || norm(t).startsWith(norm(tt.url)) || norm(tt.url).startsWith(norm(t))) {
      return { key: `bb:${tt.url}`, kind: 'blackbox', name: tt.url }
    }
  }
  if (/^https?:\/\//i.test(t)) return { key: `bb:${t}`, kind: 'blackbox', name: t }
  const base = t.split(/[\\/]/).filter(Boolean).pop() || t
  return { key: `wb:${base}`, kind: 'whitebox', name: base }
}

const findingGroups = computed<FindingGroup[]>(() => {
  const map = new Map<string, FindingGroup>()
  for (const f of filteredFindings.value) {
    const { key, kind, name } = groupOf(f)
    let g = map.get(key)
    if (!g) { g = { key, kind, name, findings: [] }; map.set(key, g) }
    g.findings.push(f)
  }
  // 分组排序：白盒仓库（按任务绑定顺序）→ 黑盒地址（按配置顺序）→ 未标注
  const rank = (g: FindingGroup) => (g.kind === 'whitebox' ? 0 : g.kind === 'blackbox' ? 1 : 2)
  const pos = (g: FindingGroup) =>
    g.kind === 'whitebox' ? repoDefs.value.findIndex(r => g.key === `wb:${r.name}`)
      : g.kind === 'blackbox' ? targetDefs.value.findIndex(t => g.key === `bb:${t.url}`)
        : 0
  return [...map.values()].sort((a, b) => rank(a) - rank(b) || pos(a) - pos(b))
})

function title(f: { title: string; title_zh: string }) { return showZh.value && f.title_zh ? f.title_zh : f.title }
function desc(f: { description: string; description_zh: string }) { return showZh.value && f.description_zh ? f.description_zh : f.description }
function fix(f: { remediation: string; remediation_zh: string }) { return showZh.value && f.remediation_zh ? f.remediation_zh : f.remediation }

// poc_code 原样入库，可能带 ``` 围栏；裸代码补一层围栏，避免 # 注释等被当成 markdown 渲染
function pocMd(code: string): string {
  const t = code.trim()
  return t.includes('```') ? t : `\`\`\`\n${t}\n\`\`\``
}

async function refresh() {
  try {
    const [t, l] = await Promise.all([api.getTask(props.taskId), api.getLog(props.taskId)])
    task.value = t
    logText.value = l.log
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function downloadArtifacts() {
  try { await api.downloadArtifacts(props.taskId) } catch (e) { toast.error((e as Error).message) }
}
async function downloadPdf() {
  try { await api.downloadPdf(props.taskId) } catch (e) { toast.error((e as Error).message) }
}

function fmtDur(s: number | null) {
  if (s == null) return '-'
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m`
}
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }
function fmtTokens(n: number | null) { return n != null ? n.toLocaleString() : '-' }

// 智能体执行过程：启动/完成时间取自 strix.log，tokens/请求取自 run.json
function agentClock(a: AgentUsage) { return a.started_at ? a.started_at.slice(11, 19) : '-' }
function agentDur(a: AgentUsage) {
  if (!a.started_at || !a.finished_at) return '-'
  const sec = (Date.parse(a.finished_at.replace(' ', 'T')) - Date.parse(a.started_at.replace(' ', 'T'))) / 1000
  if (Number.isNaN(sec) || sec < 0) return '-'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m${Math.round(sec % 60)}s`
}
const agentMaxTokens = computed(() => Math.max(1, ...(task.value?.agents || []).map(a => a.total_tokens)))
const agentStatusLabels: Record<string, string> = { completed: '已完成', waiting: '等待中', failed: '失败', running: '运行中' }

onMounted(() => {
  refresh()
  timer = window.setInterval(() => { if (running.value || task.value?.zh_status === 'pending') refresh() }, 5000)
  themeObserver = new MutationObserver(() => {
    isDark.value = !document.documentElement.classList.contains('light')
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})
onUnmounted(() => { clearInterval(timer); themeObserver?.disconnect() })
</script>

<template>
  <div>
    <a href="#/tasks" class="mb-3 inline-block text-sm font-semibold text-accent hover:underline">&larr; 返回任务列表</a>

    <template v-if="task">
      <!-- 页头：标题 + 状态 + 元信息，右上为报告操作 -->
      <div class="mb-[18px] flex items-end gap-3">
        <div class="min-w-0">
          <h1 class="flex flex-wrap items-center gap-2.5 text-[26px] leading-tight font-bold text-text">
            安全测试报告
            <span :class="statusBadgeClass(task.status)" :title="task.status">
              {{ statusLabels[task.status] || task.status }}
            </span>
            <span v-if="task.timed_out" :class="statusBadgeClass('failed')">超时终止</span>
            <span v-if="task.zh_status === 'pending'" class="inline-block rounded-full bg-[#9b7bff]/15 px-2.5 py-0.5 text-xs font-semibold text-[#a78bfa]">翻译中</span>
          </h1>
          <p class="mt-1.5 text-[13px] text-muted">
            项目「{{ task.project_name }}」 · {{ task.source_type === 'git'
              ? (task.repo_branches?.length > 1 ? `${task.repo_branches.length} 个仓库` : (task.branch || '默认分支'))
              : task.source_ref }}
            · {{ task.scan_mode }} 模式 · {{ task.model || '-' }} · 提交人 {{ task.created_by_name }} · {{ fmtTime(task.created_at) }}
          </p>
        </div>
        <div class="flex-1"></div>
        <div class="flex shrink-0 items-center gap-2.5">
          <button v-if="task.has_artifacts" :class="btnGhost" @click="downloadArtifacts">下载产物 zip</button>
          <button v-if="task.status === 'done' && task.run_dir_name" :class="btn" @click="downloadPdf">导出 PDF 报告</button>
        </div>
      </div>

      <!-- 扫描仓库（多仓库任务展示各仓库与分支） -->
      <div v-if="task.repo_branches?.length > 1" class="mb-[18px] rounded-xl border border-border bg-panel px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft">
        <div class="mb-1 font-semibold text-muted">扫描仓库（{{ task.repo_branches.length }} 个）：</div>
        <div v-for="r in task.repo_branches" :key="r.url" class="break-all">
          <span class="font-mono">{{ r.url }}</span>
          <span class="text-muted"> —— 分支 {{ r.branch || '默认' }}</span>
        </div>
      </div>

      <!-- 黑盒测试地址（每条含作用说明） -->
      <div v-if="task.test_targets?.length" class="mb-[18px] rounded-xl border border-border bg-panel px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft">
        <div class="mb-1 font-semibold text-muted">黑盒测试地址（{{ task.test_targets.length }} 个）：</div>
        <div v-for="t in task.test_targets" :key="t.url" class="break-all">
          <span class="font-mono">{{ t.url }}</span>
          <span v-if="t.note" class="text-muted"> —— {{ t.note }}</span>
        </div>
      </div>

      <!-- 测试指令 -->
      <div v-if="task.instruction" class="mb-[18px] rounded-xl border border-border bg-panel px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft" style="white-space: pre-wrap">
        <span class="font-semibold text-muted">测试指令：</span>{{ task.instruction }}
      </div>

      <!-- 严重度概览：KPI 卡片 + 占比条 -->
      <div class="mb-3 grid grid-cols-2 gap-3.5 sm:grid-cols-3 lg:grid-cols-5">
        <div
          v-for="s in sevGrid" :key="s.key"
          :class="cardLifted" class="p-4.5"
          :style="!s.count ? 'opacity:.45' : ''"
        >
          <div class="flex items-center justify-between">
            <div class="text-[13px] font-semibold text-muted">{{ s.label }}</div>
            <span class="inline-block size-2.5 rounded-full" :class="sevBarCls[s.key]"></span>
          </div>
          <div class="mt-1 text-[32px] leading-none font-bold" :class="sevTextCls[s.key]">{{ s.count }}</div>
          <div class="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-panel2">
            <div
              class="h-full rounded-full transition-all"
              :class="sevBarCls[s.key]"
              :style="{ width: `${(s.count / Math.max(1, task.findings_count)) * 100}%` }"
            ></div>
          </div>
        </div>
      </div>

      <!-- 概览说明 / 运行中提示 / 失败原因 -->
      <p v-if="task.status === 'done'" class="mb-[18px] text-[13px] text-muted">
        共发现 <span class="font-bold text-text">{{ task.findings_count }}</span> 个问题，其中
        <span :class="criticalPlus ? 'font-bold text-crit' : 'font-bold text-text'">严重 / 高危 {{ criticalPlus }}</span>
        个。AI 辅助测试结果，建议人工复核。
      </p>
      <p v-else-if="running" class="mb-[18px] text-[13px] text-muted">
        任务{{ statusLabels[task.status] || task.status }}中，页面每 5 秒自动刷新，可先查看执行日志。
      </p>
      <div v-if="task.error" :class="errCls" class="mb-[18px]">{{ task.error }}</div>

      <!-- 标签页（reka-ui Tabs） -->
      <TabsRoot v-model="tab">
        <TabsList class="flex flex-wrap items-center gap-1">
          <TabsTrigger
            v-for="t in [
              { value: 'findings', label: `漏洞明细（${task.findings.length}）` },
              ...(task.agents.length ? [{ value: 'agents', label: `智能体（${task.agents.length}）` }] : []),
              ...(task.has_report_md ? [{ value: 'summary', label: '执行摘要报告' }] : []),
              { value: 'log', label: '执行日志' },
            ]"
            :key="t.value" :value="t.value"
            class="cursor-pointer rounded-lg border border-border bg-panel px-4 py-1.5 font-semibold shadow-soft transition-colors"
            :class="tab === t.value ? 'border-accent bg-accent/12 text-accent' : 'text-muted hover:text-text'"
          >{{ t.label }}</TabsTrigger>
          <template v-if="zhReady">
            <div class="flex-1"></div>
            <label class="flex cursor-pointer items-center gap-1.5 text-[13px] text-text">
              <CheckboxRoot v-model="showZh"
                class="flex h-4 w-4 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                <CheckboxIndicator class="text-xs leading-none text-accent">✓</CheckboxIndicator>
              </CheckboxRoot>
              显示中文翻译
            </label>
          </template>
        </TabsList>

        <!-- 漏洞明细 -->
        <TabsContent value="findings" :class="card" class="mt-3.5">
          <!-- 筛选：严重度 + PoC + 批量展开 -->
          <div class="mb-3.5 flex flex-wrap items-center gap-2">
            <button
              class="cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold transition-colors"
              :class="sevFilter === 'all' ? 'border-accent bg-accent/12 text-accent' : 'border-border bg-panel2 text-muted hover:text-text'"
              @click="sevFilter = 'all'"
            >全部（{{ task.findings.length }}）</button>
            <button
              v-for="s in sevGrid.filter(x => x.count)" :key="s.key"
              class="cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold transition-colors"
              :class="sevFilter === s.key ? 'border-accent bg-accent/12 text-accent' : 'border-border bg-panel2 text-muted hover:text-text'"
              @click="sevFilter = sevFilter === s.key ? 'all' : s.key"
            >{{ s.label }}（{{ s.count }}）</button>
            <div class="flex-1"></div>
            <label class="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
              <CheckboxRoot v-model="onlyPoc"
                class="flex h-3.5 w-3.5 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                <CheckboxIndicator class="text-[10px] leading-none text-accent">✓</CheckboxIndicator>
              </CheckboxRoot>
              仅看有 PoC
            </label>
            <button
              class="cursor-pointer rounded-md border border-border bg-transparent px-2.5 py-1 text-xs font-semibold text-muted transition-colors hover:border-accent hover:text-accent"
              @click="expandAll(!allExpanded)"
            >{{ allExpanded ? '收起全部' : '展开全部' }}</button>
          </div>

          <div v-if="task.zh_status === 'pending'" :class="hint">
            中文翻译进行中，稍后自动刷新；当前可先查看原文。
          </div>
          <div v-if="task.findings.length === 0 && task.status === 'done'" :class="hint">
            本次扫描未发现漏洞（退出码 {{ task.exit_code }}）。
          </div>
          <div v-else-if="!filteredFindings.length" :class="hint">
            当前筛选条件下没有漏洞，试试切换严重度或取消「仅看有 PoC」。
          </div>

          <div v-for="g in findingGroups" :key="g.key" class="mb-5">
            <div class="mb-2.5 flex flex-wrap items-center gap-2 border-b border-border pb-2">
              <span
                class="shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-bold"
                :class="g.kind === 'whitebox' ? 'bg-accent/15 text-accent' : g.kind === 'blackbox' ? 'bg-high/15 text-high' : 'bg-panel2 text-muted'"
              >{{ g.kind === 'whitebox' ? '白盒' : g.kind === 'blackbox' ? '黑盒' : '其他' }}</span>
              <span class="min-w-0 break-all text-[13.5px] font-semibold text-text">{{ g.name }}</span>
              <span class="shrink-0 text-xs text-muted">{{ g.findings.length }} 条</span>
            </div>
            <div v-for="f in g.findings" :key="f.id"
              class="mb-2.5 overflow-hidden rounded-xl border border-border border-l-4" :class="findingBarClass(f.severity)">
            <button
              class="flex w-full cursor-pointer items-center gap-2.5 bg-panel2/60 px-3.5 py-2.5 text-left transition-colors hover:bg-panel2"
              @click="expanded[f.id] = !expanded[f.id]"
            >
              <span :class="sevBadgeClass(f.severity)">{{ (sevLabel[f.severity] || f.severity).toUpperCase() }}</span>
              <span class="min-w-0 flex-1 truncate text-[14px] font-semibold text-text">{{ title(f) }}</span>
              <span class="hidden shrink-0 text-xs text-muted sm:inline">
                CVSS {{ f.cvss ?? '-' }} · {{ f.cwe || '-' }} · {{ f.has_poc ? '有 PoC' : '无 PoC' }}
              </span>
              <span
                class="shrink-0 text-xs text-muted transition-transform duration-200"
                :class="expanded[f.id] ? 'rotate-180' : ''"
              >▼</span>
            </button>
            <div v-if="expanded[f.id]" class="px-4 pt-1 pb-4 text-[13.5px] leading-relaxed text-body">
              <h4 class="mt-3 mb-1.5 text-xs font-semibold tracking-wide text-muted">端点 / 目标</h4>
              <div class="break-all rounded-lg bg-panel2 px-3 py-2 font-mono text-xs">
                <template v-if="f.target">{{ f.target }}<br /></template>{{ f.endpoint || '-' }}
              </div>
              <template v-if="desc(f)">
                <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">描述</h4>
                <MarkdownRender mode="docs" :content="desc(f)" :final="true" :is-dark="isDark" />
              </template>
              <template v-if="f.poc_description">
                <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">PoC 说明</h4>
                <MarkdownRender mode="docs" :content="f.poc_description" :final="true" :is-dark="isDark" />
              </template>
              <template v-if="f.poc_code">
                <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">PoC 脚本</h4>
                <MarkdownRender mode="docs" :content="pocMd(f.poc_code)" :final="true" :is-dark="isDark" />
              </template>
              <template v-if="fix(f)">
                <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">修复建议</h4>
                <div class="rounded-lg border border-ok/25 bg-ok/10 px-3.5 py-2.5">
                  <MarkdownRender mode="docs" :content="fix(f)" :final="true" :is-dark="isDark" />
                </div>
              </template>
              <template v-if="showZh && f.title_zh">
                <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">原文</h4>
                <MarkdownRender mode="docs" :content="`**${f.title}**\n\n${f.description}`" :final="true" :is-dark="isDark" />
              </template>
            </div>
            </div>
          </div>
        </TabsContent>

        <!-- 智能体：token 消耗 + 各智能体执行过程（run.json 用量 + strix.log 生命周期合并） -->
        <TabsContent v-if="task.agents.length" value="agents" :class="card" class="mt-3.5">
          <!-- token 消耗概览 -->
          <div class="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">总 tokens</div>
              <div class="mt-0.5 text-[22px] font-bold text-text">{{ fmtTokens(task.total_tokens) }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">输入 tokens</div>
              <div class="mt-0.5 text-[22px] font-bold text-text">{{ fmtTokens(task.input_tokens) }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">输出 tokens</div>
              <div class="mt-0.5 text-[22px] font-bold text-text">{{ fmtTokens(task.output_tokens) }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">LLM 请求次数</div>
              <div class="mt-0.5 text-[22px] font-bold text-text">{{ task.llm_requests != null ? task.llm_requests.toLocaleString() : '-' }}</div>
            </div>
          </div>

          <!-- 智能体执行过程表 -->
          <table class="mt-4 w-full border-collapse">
            <thead>
              <tr>
                <th :class="tableTh">智能体</th><th :class="tableTh">启动 / 完成</th>
                <th :class="tableTh">运行时长</th><th :class="tableTh">状态</th>
                <th :class="tableTh">请求</th><th :class="tableTh">tokens（输入 / 输出）</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in task.agents" :key="a.agent_id || a.agent_name">
                <td :class="tableTd">
                  <div class="font-semibold">{{ a.agent_name || a.agent_id || '（未命名）' }}</div>
                  <div class="text-xs text-muted">
                    {{ a.parent && a.parent !== '-' ? `由 ${a.parent === task.agents[0]?.agent_id ? 'Root Agent' : a.parent} 调度` : '根智能体' }}
                    <span v-if="a.model"> · 模型 {{ a.model }}</span>
                  </div>
                </td>
                <td :class="tableTd">
                  <span class="font-mono text-xs">{{ agentClock(a) }} → {{ a.finished_at ? a.finished_at.slice(11, 19) : '-' }}</span>
                </td>
                <td :class="tableTd">{{ agentDur(a) }}</td>
                <td :class="tableTd">
                  <span
                    v-if="a.status"
                    :class="a.status === 'completed' ? statusBadgeClass('done') : a.status === 'failed' ? statusBadgeClass('failed') : statusBadgeClass('pending')"
                  >{{ agentStatusLabels[a.status] || a.status }}</span>
                  <span v-else class="text-xs text-muted">-</span>
                </td>
                <td :class="tableTd">{{ a.requests || '-' }}</td>
                <td :class="tableTd">
                  <div v-if="a.total_tokens" class="flex items-center gap-2">
                    <div class="h-2 w-24 shrink-0 overflow-hidden rounded-full bg-panel2">
                      <div class="h-full rounded-full bg-accent" :style="{ width: `${(a.total_tokens / agentMaxTokens) * 100}%` }"></div>
                    </div>
                    <span class="text-xs whitespace-nowrap">{{ fmtTokens(a.total_tokens) }}（{{ fmtTokens(a.input_tokens) }} / {{ fmtTokens(a.output_tokens) }}）</span>
                  </div>
                  <span v-else class="text-xs text-muted">-</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div :class="hint" class="mt-2.5">
            智能体按启动时间排序；Root Agent 负责调度，各专项智能体（SQLi / XSS / SSRF 等）由其按需派生，token 为该智能体全部 LLM 请求的累计值。
          </div>
        </TabsContent>

        <!-- 官方执行摘要报告（strix view 同款 penetration_test_report.md），markstream-vue 渲染 -->
        <TabsContent v-if="task.has_report_md" value="summary" :class="card" class="mt-3.5">
          <MarkdownRender mode="docs" :content="reportMd" :final="true" :is-dark="isDark" />
        </TabsContent>

        <!-- 执行日志 -->
        <TabsContent value="log" :class="card" class="mt-3.5">
          <div class="mb-4 grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">引擎</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">strix {{ task.strix_version }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">耗时</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ fmtDur(task.duration_sec) }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">tokens</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ fmtTokens(task.total_tokens) }}</div>
              <div class="mt-0.5 text-[11px] text-muted">输入 {{ fmtTokens(task.input_tokens) }} / 输出 {{ fmtTokens(task.output_tokens) }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">重试次数</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ task.attempts || 0 }}</div>
            </div>
            <div class="rounded-xl bg-panel2 px-3.5 py-3">
              <div class="text-xs text-muted">开始 / 完成</div>
              <div class="mt-0.5 text-xs font-semibold">{{ fmtTime(task.started_at) }}<br />{{ fmtTime(task.finished_at) }}</div>
            </div>
          </div>
          <pre ref="logBox" :class="logPre">{{ logText || '（暂无）' }}</pre>
        </TabsContent>
      </TabsRoot>
    </template>

    <div v-else :class="card">
      <p :class="hint">报告加载中…</p>
    </div>
  </div>
</template>
