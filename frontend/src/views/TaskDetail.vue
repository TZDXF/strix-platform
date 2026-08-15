<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { TabsRoot, TabsList, TabsTrigger, TabsContent, CheckboxRoot, CheckboxIndicator } from 'reka-ui'
import { api, type TaskDetail as TaskDetailData } from '../api'
import MarkdownRender from 'markstream-vue'
import { toast } from '../toast'
import {
  btn, btnGhost, card, err as errCls, findingBarClass, hint, logPre,
  sevBadgeClass, sevCellClass, sevLabel, statusBadgeClass,
} from '../ui'

const props = defineProps<{ taskId: string }>()
const task = ref<TaskDetailData | null>(null)
const logText = ref('')
const expanded = ref<Record<number, boolean>>({})
const showZh = ref(true)
const tab = ref('findings')
let timer: number | undefined

const running = computed(() => ['pending', 'fetching', 'scanning', 'parsing'].includes(task.value?.status || ''))
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

function title(f: { title: string; title_zh: string }) { return showZh.value && f.title_zh ? f.title_zh : f.title }
function desc(f: { description: string; description_zh: string }) { return showZh.value && f.description_zh ? f.description_zh : f.description }
function fix(f: { remediation: string; remediation_zh: string }) { return showZh.value && f.remediation_zh ? f.remediation_zh : f.remediation }

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
    <a href="#/tasks" class="mb-3.5 inline-block text-accent">&larr; 返回任务列表</a>

    <template v-if="task">
      <!-- 报告头部：概览 + 严重级别统计（对齐 strix 官方报告页结构） -->
      <div :class="card">
        <div class="mb-3.5 flex items-start gap-2.5">
          <div>
            <h3 class="m-0 text-[17px] font-semibold text-text">
              安全测试报告
              <span :class="statusBadgeClass(task.status)" class="ml-2.5">{{ task.status }}</span>
              <span v-if="task.timed_out" :class="statusBadgeClass('failed')" class="ml-1.5">超时终止</span>
              <span v-if="task.zh_status === 'pending'" class="ml-1.5 inline-block rounded-full bg-[#9b7bff]/15 px-2.5 py-0.5 text-xs font-semibold text-[#a78bfa]">翻译中</span>
            </h3>
            <div class="mt-1.5 text-xs text-muted">
              项目「{{ task.project_name }}」 · {{ task.source_type === 'git' ? (task.branch || '默认分支') : task.source_ref }}
              · {{ task.scan_mode }} 模式 · {{ task.model || '-' }} · 提交人 {{ task.created_by_name }} · {{ fmtTime(task.created_at) }}
            </div>
            <div v-if="task.instruction" class="mt-2 rounded-md bg-panel2 px-2.5 py-2 text-xs text-muted" style="white-space: pre-wrap">
              <span class="font-semibold">测试指令：</span>{{ task.instruction }}
            </div>
          </div>
          <div class="flex-1"></div>
          <button v-if="task.has_artifacts" :class="btnGhost" @click="downloadArtifacts">下载产物 zip</button>
          <button v-if="task.status === 'done' && task.run_dir_name" :class="btn" @click="downloadPdf">导出 PDF 报告</button>
        </div>

        <div class="mb-3.5 grid grid-cols-5 gap-2.5">
          <div v-for="s in sevGrid" :key="s.key" :class="sevCellClass(s.key)" class="rounded-[10px] px-2.5 py-3.5 text-center" :data-zero="!s.count" :style="!s.count ? 'opacity:.45' : ''">
            <div class="text-[26px] leading-tight font-bold">{{ s.count }}</div>
            <div class="mt-0.5 text-xs">{{ s.label }}</div>
          </div>
        </div>
        <div v-if="task.status === 'done'" :class="hint">
          共发现 {{ task.findings_count }} 个问题，其中严重/高危 {{ criticalPlus }} 个。AI 辅助测试结果，建议人工复核。
        </div>
        <div v-if="task.error" :class="errCls" class="mt-2.5">{{ task.error }}</div>
      </div>

      <!-- 标签页（reka-ui Tabs） -->
      <TabsRoot v-model="tab" class="mb-3.5">
        <TabsList class="flex items-center gap-1">
          <TabsTrigger
            v-for="t in [
              { value: 'findings', label: `漏洞明细（${task.findings.length}）` },
              ...(task.has_report_md ? [{ value: 'summary', label: '执行摘要报告' }] : []),
              { value: 'log', label: '执行日志' },
            ]"
            :key="t.value" :value="t.value"
            class="cursor-pointer rounded-lg border border-border bg-panel px-4 py-1.5 font-semibold"
            :class="tab === t.value ? 'border-accent bg-accent/12 text-accent' : 'text-muted'"
          >{{ t.label }}</TabsTrigger>
          <template v-if="zhReady">
            <div class="flex-1"></div>
            <label class="flex cursor-pointer items-center gap-1.5 text-[13px] text-text">
              <CheckboxRoot v-model:checked="showZh"
                class="flex h-4 w-4 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                <CheckboxIndicator class="text-xs leading-none text-accent">✓</CheckboxIndicator>
              </CheckboxRoot>
              显示中文翻译
            </label>
          </template>
        </TabsList>

        <!-- 漏洞明细 -->
        <TabsContent value="findings" :class="card" class="mt-0">
          <div v-if="task.zh_status === 'pending'" :class="hint">
            中文翻译进行中，稍后自动刷新；当前可先查看原文。
          </div>
          <div v-if="task.findings.length === 0 && task.status === 'done'" :class="hint">
            本次扫描未发现漏洞（退出码 {{ task.exit_code }}）。
          </div>
          <div v-for="f in task.findings" :key="f.id"
            class="mb-2.5 overflow-hidden rounded-lg border border-border border-l-4" :class="findingBarClass(f.severity)">
            <div class="flex cursor-pointer items-center gap-2.5 bg-panel2 px-3.5 py-2.5" @click="expanded[f.id] = !expanded[f.id]">
              <span :class="sevBadgeClass(f.severity)">{{ (sevLabel[f.severity] || f.severity).toUpperCase() }}</span>
              <span class="flex-1 font-semibold">{{ title(f) }}</span>
              <span class="text-xs text-muted">CVSS {{ f.cvss ?? '-' }} · {{ f.cwe || '-' }} · {{ f.has_poc ? '有 PoC' : '无 PoC' }}</span>
              <span class="text-xs text-muted">{{ expanded[f.id] ? '收起 ▲' : '展开 ▼' }}</span>
            </div>
            <div v-if="expanded[f.id]" class="px-4 pt-1 pb-3.5 text-body">
              <h4 class="mt-3 mb-1 text-[12.5px] text-muted">端点 / 目标</h4>
              <div class="break-all">{{ f.endpoint || '-' }}</div>
              <template v-if="desc(f)">
                <h4 class="mt-3 mb-1 text-[12.5px] text-muted">描述</h4>
                <div class="whitespace-pre-wrap">{{ desc(f) }}</div>
              </template>
              <template v-if="f.poc_description">
                <h4 class="mt-3 mb-1 text-[12.5px] text-muted">PoC 说明</h4>
                <div class="whitespace-pre-wrap">{{ f.poc_description }}</div>
              </template>
              <template v-if="f.poc_code">
                <h4 class="mt-3 mb-1 text-[12.5px] text-muted">PoC 脚本</h4>
                <pre :class="logPre">{{ f.poc_code }}</pre>
              </template>
              <template v-if="fix(f)">
                <h4 class="mt-3 mb-1 text-[12.5px] text-muted">修复建议</h4>
                <div class="whitespace-pre-wrap">{{ fix(f) }}</div>
              </template>
              <template v-if="showZh && f.title_zh">
                <h4 class="mt-3 mb-1 text-[12.5px] text-muted">原文</h4>
                <div class="whitespace-pre-wrap text-xs text-muted">{{ f.title }}<br />{{ f.description }}</div>
              </template>
            </div>
          </div>
        </TabsContent>

        <!-- 官方执行摘要报告（strix view 同款 penetration_test_report.md），markstream-vue 渲染 -->
        <TabsContent v-if="task.has_report_md" value="summary" :class="card" class="mt-0">
          <MarkdownRender mode="docs" :content="reportMd" :final="true" :is-dark="isDark" />
        </TabsContent>

        <!-- 执行日志 -->
        <TabsContent value="log" :class="card" class="mt-0">
          <div class="mb-4 grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
            <div class="rounded-lg bg-panel2 px-3 py-2.5">
              <div class="text-xs text-muted">引擎</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">strix {{ task.strix_version }}</div>
            </div>
            <div class="rounded-lg bg-panel2 px-3 py-2.5">
              <div class="text-xs text-muted">耗时</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ fmtDur(task.duration_sec) }}</div>
            </div>
            <div class="rounded-lg bg-panel2 px-3 py-2.5">
              <div class="text-xs text-muted">tokens</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ fmtTokens(task.total_tokens) }}</div>
            </div>
            <div class="rounded-lg bg-panel2 px-3 py-2.5">
              <div class="text-xs text-muted">重试次数</div>
              <div class="mt-0.5 text-[14.5px] font-semibold">{{ task.attempts || 0 }}</div>
            </div>
            <div class="rounded-lg bg-panel2 px-3 py-2.5">
              <div class="text-xs text-muted">开始 / 完成</div>
              <div class="mt-0.5 text-xs font-semibold">{{ fmtTime(task.started_at) }}<br />{{ fmtTime(task.finished_at) }}</div>
            </div>
          </div>
          <pre :class="logPre">{{ logText || '（暂无）' }}</pre>
        </TabsContent>
      </TabsRoot>
    </template>
  </div>
</template>
