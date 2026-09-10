<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { TabsRoot, TabsList, TabsTrigger, TabsContent, CheckboxRoot, CheckboxIndicator } from 'reka-ui'
import { api, type AgentUsage, type Finding, type TaskDetail as TaskDetailData } from '../api'
import ReplayPlayer from '../components/ReplayPlayer.vue'
import MarkdownRender from 'markstream-vue'
import { toast } from '../toast'
import {
  btn, btnDanger, btnGhost, card, cardLifted, err as errCls, findingBarClass, findingStatusBadgeClass,
  findingStatusLabels, hint, logPre, sevBadgeClass, sevLabel, statusBadgeClass, tableTd, tableTh, taskStatusLabel,
} from '../ui'

const props = defineProps<{ taskId: string }>()
const task = ref<TaskDetailData | null>(null)
const logText = ref('')
const logBox = ref<HTMLElement | null>(null)
const showZh = ref(true)
const tab = ref('findings')
let timer: number | undefined

const running = computed(() => ['pending', 'fetching', 'scanning', 'parsing'].includes(task.value?.status || ''))

// ---- 任务取消（运行中可见；排队中的任务也可取消） ----
const cancelling = ref(false)

async function cancelTask() {
  if (!confirm('确定取消该任务？正在运行的扫描引擎进程会被终止，已产生的部分结果不会入库。')) return
  cancelling.value = true
  try {
    await api.cancelTask(props.taskId)
    toast.success('取消请求已下达，任务通常在几秒内终止')
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { cancelling.value = false }
}

// ---- 运行中转向：向智能体下发新指令（安全打断 + --resume 断点续扫注入） ----
const steerMsg = ref('')
const steering = ref(false)

async function steerTask() {
  const msg = steerMsg.value.trim()
  if (!msg) { toast.error('请输入转向指令'); return }
  if (!confirm('转向会安全打断当前引擎进程，把指令注入根智能体后从断点续扫（通常几十秒内恢复运行）。确定下发？')) return
  steering.value = true
  try {
    await api.steerTask(props.taskId, msg)
    toast.success('转向指令已下达，引擎将从断点携带新指令继续')
    steerMsg.value = ''
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { steering.value = false }
}

// ---- 断点续扫：失败/取消/完成后从上次 run 断点继续（--resume，保留已完成轮次） ----
const resuming = ref(false)
const canResume = computed(() =>
  ['done', 'failed', 'cancelled'].includes(task.value?.status || '') && !!task.value?.run_dir_name)

async function resumeTask() {
  if (!confirm(`确定从 run「${task.value?.run_dir_name}」断点续扫？已完成的工作保留，续扫期间 tokens 继续累计。`)) return
  resuming.value = true
  try {
    await api.resumeTask(props.taskId)
    toast.success('续扫已发起，任务重新进入队列')
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { resuming.value = false }
}

// ---- 手动触发中文翻译（流程内不自动翻译，报告为英文或明细缺中文时按需补翻） ----
const translatingZh = ref(false)

async function translateNow() {
  if (!confirm('确定将漏洞明细翻译为简体中文？将使用提交人的个人 AI 密钥调用 LLM 网关。')) return
  translatingZh.value = true
  try {
    await api.translateTask(props.taskId)
    toast.success('翻译已开始，完成后页面自动展示中文内容')
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { translatingZh.value = false }
}

// ---- 漏洞详情：独立页面展示（行点击跳转），处置状态在该页维护 ----
function openFinding(f: Finding) {
  location.hash = `#/task/${props.taskId}/finding/${f.id}`
}

const statusCounts = computed(() => {
  const c: Record<string, number> = { open: 0, fixed: 0, ignored: 0, false_positive: 0 }
  for (const f of task.value?.findings || []) if (f.status in c) c[f.status]++
  return c
})

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

// ---- 漏洞明细筛选：按严重度 + 是否含 PoC + 仅看待处理 ----
const sevFilter = ref('all')
const onlyPoc = ref(false)
const onlyOpen = ref(false)
const filteredFindings = computed(() =>
  (task.value?.findings || []).filter(f =>
    (sevFilter.value === 'all' || f.severity === sevFilter.value)
    && (!onlyPoc.value || f.has_poc)
    && (!onlyOpen.value || f.status === 'open')))

// 智能体自建靶场提示：检测是否存在回环地址目标的漏洞
const LOOPBACK_HOST = /^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?)$/i
function isLoopbackUrl(u: string): boolean {
  try { return LOOPBACK_HOST.test(new URL(u).hostname) } catch { return false }
}

function title(f: { title: string; title_zh: string }) { return showZh.value && f.title_zh ? f.title_zh : f.title }

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
async function downloadReport() {
  try { await api.downloadReport(props.taskId) } catch (e) { toast.error((e as Error).message) }
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
    <a v-if="task?.project_id" :href="`#/project/${task.project_id}`" class="mb-3 inline-block text-sm font-semibold text-accent hover:underline">&larr; 返回项目页</a>
    <a v-else href="#/tasks" class="mb-3 inline-block text-sm font-semibold text-accent hover:underline">&larr; 返回任务列表</a>

    <template v-if="task">
      <!-- 页头：标题 + 状态 + 元信息，右上为报告操作 -->
      <div class="mb-[18px] flex items-end gap-3">
        <div class="min-w-0">
          <h1 class="flex flex-wrap items-center gap-2.5 text-[26px] leading-tight font-bold text-text">
            安全测试报告
            <span :class="statusBadgeClass(task.status)" :title="task.status">
              {{ taskStatusLabel[task.status] || task.status }}
            </span>
            <span v-if="task.schedule_id" class="inline-block rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent" title="由定时计划自动发起">定时扫描</span>
            <span v-if="task.web_search" class="inline-block rounded-full bg-[#9b7bff]/15 px-2.5 py-0.5 text-xs font-semibold text-[#a78bfa]" title="智能体可经内网搜索端点查询公开漏洞/利用资料">联网搜索</span>
            <span v-if="task.timed_out && task.status !== 'done'" :class="statusBadgeClass('failed')">超时终止</span>
            <span v-if="task.timeout_disabled" class="inline-block rounded-full bg-border/30 px-2.5 py-0.5 text-xs font-semibold text-muted" title="发起任务时关闭了超时终止，引擎不受档位默认时长限制">不限时</span>
            <span v-if="task.scope_mode === 'diff'" class="inline-block rounded-full bg-ok/15 px-2.5 py-0.5 text-xs font-semibold text-ok" :title="`增量扫描：仅扫相对 ${task.diff_base || '仓库默认分支'} 的变更`">
              diff 增量{{ task.diff_base ? ` · ${task.diff_base}` : '' }}
            </span>
            <span v-if="task.resume_count" class="inline-block rounded-full bg-accent/15 px-2.5 py-0.5 text-xs font-semibold text-accent" title="该任务经「续扫」从断点恢复的次数">续扫 ×{{ task.resume_count }}</span>
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
          <button v-if="running" :class="btnDanger" :disabled="cancelling" @click="cancelTask">
            {{ cancelling ? '取消中…' : '取消任务' }}
          </button>
          <button v-if="canResume" :class="btnGhost" :disabled="resuming" @click="resumeTask">
            {{ resuming ? '发起中…' : `续扫${task.resume_count ? `（已续 ${task.resume_count} 次）` : ''}` }}
          </button>
          <button v-if="task.has_artifacts" :class="btnGhost" @click="downloadArtifacts">下载产物 zip</button>
          <button
            v-if="task.status === 'done' && task.findings_count > 0 && task.zh_status !== 'pending'"
            :class="btnGhost" :disabled="translatingZh" @click="translateNow"
          >{{ translatingZh ? '请求中…' : '翻译成中文' }}</button>
          <button v-if="task.status === 'done' && task.run_dir_name" :class="btn" @click="downloadReport">导出报告</button>
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

      <!-- 黑盒测试地址（每条含作用说明）；未配置时说明引擎可自建本地靶场实测 -->
      <div v-if="task.test_targets?.length" class="mb-[18px] rounded-xl border border-border bg-panel px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft">
        <div class="mb-1 font-semibold text-muted">黑盒测试地址（{{ task.test_targets.length }} 个）：</div>
        <div v-for="t in task.test_targets" :key="t.url" class="break-all">
          <span class="font-mono">{{ t.url }}</span>
          <span v-if="t.note" class="text-muted"> —— {{ t.note }}</span>
        </div>
      </div>
      <div v-else-if="task.findings.some(f => /^https?:\/\//i.test((f.target || '').trim()) && isLoopbackUrl(f.target.trim()))" class="mb-[18px] rounded-xl border border-med/40 bg-med/[.06] px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft">
        <div class="font-semibold text-muted">未配置黑盒测试地址</div>
        本次任务未提供任何黑盒目标。部分漏洞的 PoC 由引擎在<span class="font-semibold">扫描沙箱内自建的本地实例</span>上实测（构建并运行应用、自备数据库与测试数据），相关结论仅代表本地靶场行为，不代表真实环境响应；实测流量全部局限于沙箱内部。
      </div>

      <!-- API 规范目标（OpenAPI/Swagger/Postman/Apifox 导出文件） -->
      <div v-if="task.api_specs?.length" class="mb-[18px] rounded-xl border border-border bg-panel px-4 py-3 text-[13px] leading-relaxed text-body shadow-soft">
        <div class="mb-1 font-semibold text-muted">API 规范目标（{{ task.api_specs.length }} 个）：</div>
        <div v-for="s in task.api_specs" :key="s" class="break-all font-mono">{{ s }}</div>
      </div>

      <!-- 运行中转向：向智能体下发新指令 -->
      <div v-if="task.status === 'scanning'" class="mb-[18px] rounded-xl border border-accent/35 bg-accent/[.06] px-4 py-3 shadow-soft">
        <div class="mb-1.5 text-[13px] font-semibold text-text">运行中转向</div>
        <div class="flex items-start gap-2.5">
          <textarea
            v-model="steerMsg"
            rows="2"
            placeholder="给智能体的新指令，例如：&#10;Prioritize the admin panel at /admin and test default credentials.&#10;补充测试账号：user:test1234，重点看订单接口的水平越权。"
            class="flex-1 resize-y rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent"
          ></textarea>
          <button :class="btn" :disabled="steering || !steerMsg.trim()" @click="steerTask">
            {{ steering ? '下发中…' : '下发转向' }}
          </button>
        </div>
        <p :class="hint" class="!mt-1.5">
          指令会注入根智能体并从断点继续（引擎进程会被安全打断后 --resume 续扫，已完成的工作不丢失）；多个智能体的具体执行由根智能体转达。
        </p>
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
        个。待处理 <span class="font-bold text-text">{{ statusCounts.open }}</span> 个<template v-if="statusCounts.fixed || statusCounts.ignored || statusCounts.false_positive">
          （已修复 {{ statusCounts.fixed }} / 已忽略 {{ statusCounts.ignored }} / 误报 {{ statusCounts.false_positive }}）</template>。AI 辅助测试结果，建议人工复核。
      </p>
      <p v-else-if="running" class="mb-[18px] text-[13px] text-muted">
        任务{{ taskStatusLabel[task.status] || task.status }}中，页面每 5 秒自动刷新；「智能体」页可实时跟拍执行回放，执行日志持续追加。
      </p>
      <p v-else-if="task.status === 'cancelled'" class="mb-[18px] text-[13px] text-muted">
        任务已被取消：扫描引擎在执行中终止，未解析/保留部分结果。可重新发起任务。
      </p>
      <div v-if="task.error" :class="errCls" class="mb-[18px]">{{ task.error }}</div>

      <!-- 标签页（reka-ui Tabs） -->
      <TabsRoot v-model="tab">
        <TabsList class="flex flex-wrap items-center gap-1">
          <TabsTrigger
            v-for="t in [
              { value: 'findings', label: `漏洞明细（${task.findings.length}）` },
              ...((task.agents.length || running)
                ? [{ value: 'agents', label: task.agents.length ? `智能体（${task.agents.length}）` : '智能体（实时）' }]
                : []),
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
            <label class="flex cursor-pointer items-center gap-1.5 text-xs text-muted" title="隐藏已修复 / 已忽略 / 误报的漏洞">
              <CheckboxRoot v-model="onlyOpen"
                class="flex h-3.5 w-3.5 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                <CheckboxIndicator class="text-[10px] leading-none text-accent">✓</CheckboxIndicator>
              </CheckboxRoot>
              仅看待处理
            </label>
          </div>

          <div v-if="task.zh_status === 'pending'" :class="hint">
            中文翻译进行中，稍后自动刷新；当前可先查看原文。
          </div>
          <div v-else-if="task.zh_status === 'failed'" :class="hint">
            中文翻译失败（常见于个人 AI 密钥额度或网关连接问题），可点击页头「翻译成中文」重试。
          </div>
          <div v-if="task.findings.length === 0 && task.status === 'done'" :class="hint">
            本次扫描未发现漏洞（退出码 {{ task.exit_code }}）。
          </div>
          <div v-else-if="!filteredFindings.length" :class="hint">
            当前筛选条件下没有漏洞，试试切换严重度或取消「仅看有 PoC」。
          </div>

          <!-- 漏洞列表（按严重度排序的扁平列表，点击进入独立详情页） -->
          <div v-for="f in filteredFindings" :key="f.id"
            class="mb-2.5 overflow-hidden rounded-xl border border-border border-l-4" :class="findingBarClass(f.severity)">
            <button
              class="flex w-full cursor-pointer items-center gap-2.5 bg-panel2/60 px-3.5 py-2.5 text-left transition-colors hover:bg-panel2"
              :title="'点击查看完整明细'"
              @click="openFinding(f)"
            >
              <span class="shrink-0 whitespace-nowrap" :class="sevBadgeClass(f.severity)">{{ (sevLabel[f.severity] || f.severity).toUpperCase() }}</span>
              <span
                v-if="f.finding_class === 'dynamic'"
                class="shrink-0 whitespace-nowrap rounded-full bg-ok/15 px-2 py-0.5 text-[10.5px] font-bold text-ok"
                title="引擎已在运行环境中实测验证（PoC 动态验证）"
              >已验证</span>
              <span v-if="f.status && f.status !== 'open'" class="shrink-0 whitespace-nowrap" :class="findingStatusBadgeClass(f.status)">{{ findingStatusLabels[f.status] || f.status }}</span>
              <span class="min-w-0 flex-1">
                <span class="line-clamp-2 break-words text-[14px] leading-snug font-semibold text-text" :class="f.status === 'ignored' || f.status === 'false_positive' ? 'opacity-60' : ''">{{ title(f) }}</span>
                <span class="mt-0.5 block break-all text-xs text-muted">
                  <template v-if="f.method">{{ f.method }} </template>{{ f.endpoint || f.target || '-' }} · CVSS {{ f.cvss ?? '-' }} · {{ f.cwe || '-' }} · {{ f.has_poc ? '有 PoC' : '无 PoC' }}
                </span>
              </span>
              <span class="shrink-0 text-xs text-muted" title="进入详情页">›</span>
            </button>
          </div>
        </TabsContent>

        <!-- 智能体：token 消耗 + 各智能体执行过程（run.json 用量 + strix.log 生命周期合并）；运行中仅展示实时回放 -->
        <TabsContent v-if="task.agents.length || running" value="agents" :class="card" class="mt-3.5">
          <template v-if="task.agents.length">
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
          </template>

          <!-- 执行过程回放：时间轴（密度图 / 泳道 / 里程碑）+ 执行步骤（思考 / 命令 / 工具调用）
               一体化播放，运行中实时跟拍 -->
          <ReplayPlayer
            v-if="running || (['done', 'failed', 'cancelled'].includes(task.status) && task.run_dir_name)"
            :task-id="task.id"
            :live="running"
          />
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
