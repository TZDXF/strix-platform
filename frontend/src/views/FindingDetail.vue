<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
} from 'reka-ui'
import { api, type FindingDetailData, type FindingStatus } from '../api'
import MarkdownRender from 'markstream-vue'
import { toast } from '../toast'
import {
  btn, card, err as errCls, findingStatusBadgeClass, findingStatusLabels, hint, sevBadgeClass, sevLabel,
} from '../ui'

/** 漏洞详情页：完整明细（CVSS 分项 / 影响 / 技术分析 / 证据 / 代码位置 / 修复 PR）+ 处置状态。 */
const props = defineProps<{ findingId: string; taskId?: string }>()

const data = ref<FindingDetailData | null>(null)
const failed = ref('')
const showZh = ref(true)

const backHref = computed(() => data.value ? `#/task/${data.value.task_id}` : (props.taskId ? `#/task/${props.taskId}` : '#/tasks'))

async function load() {
  failed.value = ''
  try {
    data.value = await api.getFindingDetail(Number(props.findingId))
  } catch (e) {
    failed.value = (e as Error).message
  }
}

// ---- 处置状态 ----
const FINDING_STATUS_OPTIONS: FindingStatus[] = ['open', 'fixed', 'ignored', 'false_positive']
const editStatus = ref<FindingStatus>('open')
const editNote = ref('')
const saving = ref(false)
const dirty = ref(false)

function initEdit(d: FindingDetailData) {
  editStatus.value = d.status
  editNote.value = d.note
}

async function saveStatus() {
  if (!data.value) return
  saving.value = true
  try {
    const res = await api.patchFinding(data.value.id, { status: editStatus.value, note: editNote.value })
    data.value.status = res.status
    data.value.note = res.note
    data.value.status_updated_at = res.status_updated_at
    dirty.value = false
    toast.success('处置状态已保存')
  } catch (e) { toast.error((e as Error).message) } finally { saving.value = false }
}

// ---- CVSS 分项 ----
const CVSS_GROUPS: { label: string; keys: [string, string][] }[] = [
  {
    label: '可利用性',
    keys: [
      ['attack_vector', '攻击途径'], ['attack_complexity', '攻击复杂度'],
      ['privileges_required', '权限要求'], ['user_interaction', '用户交互'],
    ],
  },
  {
    label: '影响',
    keys: [
      ['scope', '影响范围'], ['confidentiality', '机密性'],
      ['integrity', '完整性'], ['availability', '可用性'],
    ],
  },
]
const CVSS_VALUES: Record<string, Record<string, string>> = {
  attack_vector: { N: '网络', A: '相邻', L: '本地', P: '物理' },
  attack_complexity: { L: '低', H: '高' },
  privileges_required: { N: '无', L: '低', H: '高' },
  user_interaction: { N: '无需', R: '需要' },
  scope: { U: '不变', C: '改变' },
  confidentiality: { H: '高', L: '低', N: '无' },
  integrity: { H: '高', L: '低', N: '无' },
  availability: { H: '高', L: '低', N: '无' },
}
function cvssValue(key: string, code: string | undefined): string {
  if (!code) return '-'
  return CVSS_VALUES[key]?.[code.toUpperCase()] || code
}
const breakdown = computed<Record<string, string> | null>(() => {
  const b = data.value?.detail?.cvss_breakdown
  return b && Object.keys(b).length ? b : null
})

// ---- 富字段取值 ----
function dField(key: string): string {
  const v = (data.value?.detail as Record<string, unknown> | undefined)?.[key]
  return typeof v === 'string' ? v : ''
}
const codeLocs = computed(() => {
  const v = data.value?.detail?.code_locations
  return Array.isArray(v) ? v : []
})

const zhReady = computed(() => !!(data.value?.title_zh || data.value?.description_zh || data.value?.remediation_zh))
function pick(zh: string, en: string) { return showZh.value && zh ? zh : en }
function title() { return data.value ? pick(data.value.title_zh, data.value.title) : '' }
function desc() { return data.value ? pick(data.value.description_zh, data.value.description) : '' }
function fix() { return data.value ? pick(data.value.remediation_zh, data.value.remediation) : '' }

function pocMd(code: string): string {
  const t = code.trim()
  return t.includes('```') ? t : `\`\`\`\n${t}\n\`\`\``
}

function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

// 主题在 html.light 类上生效，监听类变化让报告渲染同步亮暗色
const isDark = ref(!document.documentElement.classList.contains('light'))
let themeObserver: MutationObserver | undefined

onMounted(async () => {
  await load()
  if (data.value) initEdit(data.value)
  themeObserver = new MutationObserver(() => {
    isDark.value = !document.documentElement.classList.contains('light')
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})
onUnmounted(() => { themeObserver?.disconnect() })
</script>

<template>
  <div>
    <a :href="backHref" class="mb-3 inline-block text-sm font-semibold text-accent hover:underline">&larr; 返回任务详情</a>

    <div v-if="failed" :class="card">
      <div :class="errCls">{{ failed }}</div>
      <button :class="btn" class="mt-3" @click="load">重试</button>
    </div>

    <div v-else-if="!data" :class="card">
      <p :class="hint">漏洞明细加载中…</p>
    </div>

    <template v-else>
      <!-- 页头：严重度 + 标题 + 概览徽标 -->
      <div class="mb-[18px]">
        <h1 class="flex flex-wrap items-center gap-2.5 text-[22px] leading-tight font-bold text-text">
          <span :class="sevBadgeClass(data.severity)">{{ (sevLabel[data.severity] || data.severity).toUpperCase() }}</span>
          <span
            v-if="dField('finding_class') === 'dynamic'"
            class="inline-block rounded-full bg-ok/15 px-2.5 py-0.5 text-xs font-bold text-ok"
            title="引擎已在运行环境中实测验证（PoC 动态验证）"
          >已验证</span>
          <span v-if="data.status !== 'open'" :class="findingStatusBadgeClass(data.status)">{{ findingStatusLabels[data.status] || data.status }}</span>
          <span class="min-w-0">{{ title() }}</span>
        </h1>
        <p class="mt-1.5 text-[13px] text-muted">
          <span class="font-mono">{{ data.vuln_id || '-' }}</span>
          · CVSS <span class="font-bold text-text">{{ data.cvss ?? '-' }}</span>
          · {{ data.cwe || 'CWE 未知' }}<template v-if="data.cve"> · {{ data.cve }}</template>
          · {{ data.has_poc ? '有 PoC' : '无 PoC' }}<template v-if="dField('timestamp')"> · 发现于 {{ dField('timestamp') }}</template>
        </p>
        <p v-if="showZh && data.title_zh && data.title_zh !== title()" class="mt-1 text-[13px] text-muted">{{ data.title }}</p>
      </div>

      <!-- 概览徽标行 -->
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <span
          v-if="dField('finding_class')"
          class="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold"
          :class="dField('finding_class') === 'dynamic' ? 'bg-ok/15 text-ok' : 'bg-panel2 text-muted'"
        >{{ dField('finding_class') === 'dynamic' ? '动态验证' : '静态分析' }}</span>
        <span v-if="dField('agent_name')" class="inline-block rounded-full bg-accent/12 px-2.5 py-0.5 text-xs font-semibold text-accent">报告智能体：{{ dField('agent_name') }}</span>
        <span v-if="dField('fix_effort')" class="inline-block rounded-full bg-panel2 px-2.5 py-0.5 text-xs font-semibold text-muted" title="预估修复代价（fix_effort）">修复代价：{{ dField('fix_effort') }}</span>
        <span v-if="dField('method')" class="inline-block rounded-full bg-panel2 px-2.5 py-0.5 font-mono text-xs font-semibold text-muted">{{ dField('method') }}</span>
        <div class="flex-1"></div>
        <label v-if="zhReady" class="flex cursor-pointer items-center gap-1.5 text-[13px] text-text">
          <input v-model="showZh" type="checkbox" class="size-3.5 cursor-pointer accent-[var(--accent)]" />
          显示中文翻译
        </label>
      </div>

      <!-- CVSS 分项 -->
      <div v-if="breakdown" class="mb-3 flex flex-wrap gap-x-10 gap-y-2.5 rounded-xl border border-border bg-panel2/40 px-4 py-3">
        <div v-for="g in CVSS_GROUPS" :key="g.label">
          <div class="mb-1 text-xs font-semibold text-muted">{{ g.label }}</div>
          <div class="flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px]">
            <span v-for="[key, label] in g.keys" :key="key" :class="breakdown[key] ? '' : 'opacity-40'">
              <span class="text-muted">{{ label }}</span>
              <span class="ml-1 font-semibold text-text">{{ cvssValue(key, breakdown[key]) }}</span>
            </span>
          </div>
        </div>
      </div>

      <!-- 处置状态（人工维护） -->
      <div class="mb-3 rounded-xl border border-border bg-panel px-4 py-3.5 shadow-soft">
        <div class="flex flex-wrap items-center gap-2.5">
          <span class="shrink-0 text-xs font-semibold tracking-wide text-muted">处置状态</span>
          <div class="w-36">
            <SelectRoot v-model="editStatus">
              <SelectTrigger class="flex w-full items-center justify-between rounded-md border border-border bg-panel2 px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent">
                <SelectValue />
              </SelectTrigger>
              <SelectPortal>
                <SelectContent class="z-50 max-h-72 min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-md border border-border bg-panel2 py-1 shadow-lg" position="popper">
                  <SelectViewport>
                    <SelectItem
                      v-for="s in FINDING_STATUS_OPTIONS" :key="s" :value="s"
                      class="cursor-pointer rounded-md px-2.5 py-1.5 text-[13px] text-text data-highlighted:bg-accent/15 data-highlighted:outline-none"
                    >
                      <SelectItemText>{{ findingStatusLabels[s] }}</SelectItemText>
                    </SelectItem>
                  </SelectViewport>
                </SelectContent>
              </SelectPortal>
            </SelectRoot>
          </div>
          <span v-if="data.status_updated_at" class="text-[11px] text-muted">上次更新 {{ fmtTime(data.status_updated_at) }}</span>
          <div class="flex-1"></div>
          <button
            :class="btn"
            class="!px-4 !py-1.5 text-xs"
            :disabled="saving || (!dirty && editStatus === data.status && editNote === data.note)"
            @click="saveStatus"
          >{{ saving ? '保存中…' : '保存' }}</button>
        </div>
        <textarea
          v-model="editNote"
          rows="2"
          placeholder="备注（可选）：修复说明 / 忽略原因 / 复测结论等，最长 2000 字"
          class="mt-2.5 w-full resize-y rounded-md border border-border bg-panel2 px-2.5 py-1.5 text-xs text-text outline-none focus:border-accent"
          @input="dirty = true"
        ></textarea>
      </div>

      <!-- 明细正文 -->
      <div :class="card" class="!mb-0 text-[13.5px] leading-relaxed text-body">
        <h4 class="mb-1.5 text-xs font-semibold tracking-wide text-muted">端点 / 目标</h4>
        <div class="break-all rounded-lg bg-panel2 px-3 py-2 font-mono text-xs">
          <template v-if="dField('method')">{{ dField('method') }} </template>{{ data.endpoint || '-' }}<template v-if="data.target"><br />{{ data.target }}</template>
        </div>

        <template v-if="desc()">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">描述</h4>
          <MarkdownRender mode="docs" :content="desc()" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="dField('impact')">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">影响</h4>
          <MarkdownRender mode="docs" :content="dField('impact')" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="dField('technical_analysis')">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">技术分析</h4>
          <MarkdownRender mode="docs" :content="dField('technical_analysis')" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="dField('evidence')">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">验证证据</h4>
          <div class="rounded-lg border border-high/25 bg-high/[.06] px-3.5 py-2.5">
            <MarkdownRender mode="docs" :content="dField('evidence')" :final="true" :is-dark="isDark" />
          </div>
        </template>
        <template v-if="dField('assumptions')">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">前提条件</h4>
          <MarkdownRender mode="docs" :content="dField('assumptions')" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="data.poc_description">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">PoC 说明</h4>
          <MarkdownRender mode="docs" :content="data.poc_description" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="data.poc_code">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">PoC 脚本</h4>
          <MarkdownRender mode="docs" :content="pocMd(data.poc_code)" :final="true" :is-dark="isDark" />
        </template>
        <template v-if="codeLocs.length">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">涉及代码位置（{{ codeLocs.length }} 处）</h4>
          <div v-for="(loc, li) in codeLocs" :key="li" class="mb-2 overflow-hidden rounded-lg border border-border">
            <div class="flex items-center gap-2 border-b border-border bg-panel2/60 px-3 py-1.5 font-mono text-xs">
              <span class="min-w-0 break-all text-text">{{ loc.file }}</span>
              <span v-if="loc.start_line" class="shrink-0 text-muted">:{{ loc.start_line }}<template v-if="loc.end_line && loc.end_line !== loc.start_line">-{{ loc.end_line }}</template></span>
            </div>
            <pre v-if="loc.snippet" class="max-h-72 overflow-auto bg-[#0b101b] px-3 py-2 font-mono text-xs leading-relaxed text-[#9fb0cd]">{{ loc.snippet }}</pre>
          </div>
        </template>
        <template v-if="fix()">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">修复建议</h4>
          <div class="rounded-lg border border-ok/25 bg-ok/10 px-3.5 py-2.5">
            <MarkdownRender mode="docs" :content="fix()" :final="true" :is-dark="isDark" />
          </div>
        </template>
        <template v-if="dField('fix_pr_body')">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">修复 PR 说明</h4>
          <div class="rounded-lg border border-ok/25 bg-ok/[.06] px-3.5 py-2.5">
            <MarkdownRender mode="docs" :content="dField('fix_pr_body')" :final="true" :is-dark="isDark" />
          </div>
        </template>
        <template v-if="showZh && data.title_zh">
          <h4 class="mt-3.5 mb-1.5 text-xs font-semibold tracking-wide text-muted">原文</h4>
          <MarkdownRender mode="docs" :content="`**${data.title}**\n\n${data.description}`" :final="true" :is-dark="isDark" />
        </template>
      </div>
    </template>
  </div>
</template>
