<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
  RadioGroupRoot, RadioGroupItem, RadioGroupIndicator,
} from 'reka-ui'
import { api, type ProjectDetailData, type User } from '../api'
import {
  badge, btn, btnDanger, btnGhost, card, err as errCls, hint, h3, input, label, statusBadgeClass, tableTd, tableTh,
} from '../ui'

const props = defineProps<{ projectId: string; user: User | null }>()
const project = ref<ProjectDetailData | null>(null)
const error = ref('')
const info = ref('')
const showLaunch = ref(false)
const launching = ref(false)
const submittedId = ref('')
let timer: number | undefined

const form = ref({
  scanMode: 'quick', model: '', testUrl: '',
  branch: '', branches: [] as string[], branchesLoaded: false, loadingBranches: false, branchesError: '',
  uploadChoice: 'new' as 'history' | 'new', uploadId: '', file: null as File | null, fileName: '',
  reportLang: 'zh',
})
const models = ref<string[]>([])
const defaultModel = ref('')

const isGit = computed(() => project.value?.source_type === 'git')

const selectTrigger = 'flex w-full items-center justify-between rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent disabled:opacity-50'
const selectItem = 'cursor-pointer rounded-md px-2.5 py-1.5 text-[13.5px] text-text data-highlighted:bg-accent/15 data-highlighted:outline-none'
const selectContent = 'z-50 max-h-72 min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-md border border-border bg-panel2 py-1 shadow-lg'
const radioItem = 'flex h-4.5 w-4.5 cursor-pointer items-center justify-center rounded-full border border-border bg-panel2 data-state-checked:border-accent'

async function refresh() {
  try {
    project.value = await api.getProject(props.projectId)
    error.value = ''
  } catch (e) { error.value = (e as Error).message }
}

async function loadModels() {
  try {
    const data = await api.listModels()
    models.value = data.items || []
    defaultModel.value = data.default || 'free'
    if (!form.value.model) form.value.model = defaultModel.value || models.value[0] || 'free'
  } catch { /* 使用平台默认模型 */ }
}

async function loadBranches() {
  const f = form.value
  f.loadingBranches = true; f.branchesError = ''
  try {
    f.branches = (await api.listBranches(props.projectId)).items || []
    f.branchesLoaded = true
    if (f.branches.length) f.branch = f.branches[0]  // 默认分支排首位
  } catch (e) { f.branchesError = (e as Error).message } finally { f.loadingBranches = false }
}

function openLaunch() {
  showLaunch.value = true
  submittedId.value = ''
  form.value.testUrl = project.value?.default_test_url || ''
  loadModels()
  if (isGit.value && !form.value.branchesLoaded) loadBranches()
  else if (!isGit.value && project.value?.uploads.length) {
    form.value.uploadChoice = 'history'
    form.value.uploadId = project.value.uploads[0].id
  }
}

async function launch() {
  const f = form.value
  error.value = ''
  if (isGit.value && !f.branch) { error.value = '请选择要扫描的分支'; return }
  if (!isGit.value && f.uploadChoice === 'history' && !f.uploadId) { error.value = '请选择历史上传'; return }
  if (!isGit.value && f.uploadChoice === 'new' && !f.file) { error.value = '请选择 zip 压缩包'; return }
  launching.value = true
  try {
    const res = await api.submitTask(props.projectId, {
      scanMode: f.scanMode,
      testUrl: f.testUrl.trim(),
      model: f.model,
      branch: isGit.value ? f.branch : '',
      uploadId: !isGit.value && f.uploadChoice === 'history' ? f.uploadId : '',
      reportLang: f.reportLang,
      file: !isGit.value && f.uploadChoice === 'new' ? f.file : null,
    })
    submittedId.value = res.id
    showLaunch.value = false
    f.file = null; f.fileName = ''
    refresh()
  } catch (e) { error.value = (e as Error).message } finally { launching.value = false }
}

function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) { form.value.file = f; form.value.fileName = f.name }
}

async function uploadStandalone(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  info.value = ''
  try {
    await api.uploadToProject(props.projectId, f)
    info.value = '压缩包已上传到项目历史'
    ;(e.target as HTMLInputElement).value = ''
    refresh()
  } catch (err) { error.value = (err as Error).message }
}

async function removeUpload(u: { id: string; filename: string }) {
  if (!confirm(`删除历史上传「${u.filename}」？`)) return
  try { await api.deleteUpload(props.projectId, u.id); refresh() } catch (err) { error.value = (err as Error).message }
}

async function removeProject() {
  if (!confirm(`确定删除项目「${project.value?.name}」？项目下的历史任务与上传将一并删除。`)) return
  try { await api.deleteProject(props.projectId); location.hash = '#/projects' } catch (err) { error.value = (err as Error).message }
}

function fmtDur(s: number | null) {
  if (s == null) return '-'
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m`
}
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }
function fmtSize(b: number) { return b > 1048576 ? Math.round(b / 1048576) + ' MB' : Math.round(b / 1024) + ' KB' }
function sevCounts(t: { severity_counts: Record<string, number> }) {
  const c = t.severity_counts || {}
  return ['critical', 'high', 'medium', 'low', 'info'].filter(k => c[k]).map(k => `${k[0].toUpperCase()}:${c[k]}`).join(' ') || '-'
}
function openTask(id: string) { location.hash = `#/task/${id}` }

onMounted(() => {
  refresh()
  timer = window.setInterval(() => refresh(), 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <a href="#/projects" class="mb-3.5 inline-block text-accent">&larr; 返回项目列表</a>
    <div v-if="error && !project" :class="[card, errCls]">{{ error }}</div>

    <template v-if="project">
      <div :class="card">
        <div class="mb-3.5 flex items-start gap-2.5">
          <h3 class="m-0 text-base font-semibold text-text">
            {{ project.name }}
            <span :class="[badge, 'bg-border/30 text-muted']" class="ml-2">Git 仓库</span>
          </h3>
          <div class="flex-1"></div>
          <button :class="btnDanger" @click="removeProject">删除项目</button>
          <button :class="btn" @click="openLaunch">发起扫描任务</button>
        </div>
        <div class="mb-4 grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
          <div v-if="isGit" class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">仓库地址</div>
            <div class="mt-0.5 text-[12.5px] font-semibold break-all">{{ project.git_url }}</div>
          </div>
          <div v-if="isGit" class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">访问凭据</div>
            <div class="mt-0.5 text-[14.5px] font-semibold">{{ project.has_credentials ? `已配置（${project.git_auth_type}）` : '未配置' }}</div>
          </div>
          <div class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">默认黑盒地址</div>
            <div class="mt-0.5 text-[12.5px] font-semibold">{{ project.default_test_url || '（仅白盒）' }}</div>
          </div>
          <div class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">创建人 / 时间</div>
            <div class="mt-0.5 text-[14.5px] font-semibold">{{ project.created_by_name }} · {{ fmtTime(project.created_at) }}</div>
          </div>
        </div>
        <div v-if="project.description" :class="hint">{{ project.description }}</div>
        <div v-if="info" class="mt-1 text-xs text-ok">{{ info }}</div>
      </div>

      <!-- 发起扫描任务 -->
      <div v-if="showLaunch" :class="card">
        <h3 :class="h3">发起扫描任务</h3>
        <div class="grid grid-cols-2 gap-3.5">
          <div v-if="isGit">
            <label :class="label">扫描分支</label>
            <div class="flex gap-2">
              <SelectRoot v-model="form.branch">
                <SelectTrigger :class="selectTrigger" class="flex-1">
                  <SelectValue placeholder="选择分支" />
                </SelectTrigger>
                <SelectPortal>
                  <SelectContent :class="selectContent" position="popper">
                    <SelectViewport>
                      <SelectItem v-for="b in form.branches" :key="b" :value="b" :class="selectItem">
                        <SelectItemText>{{ b }}{{ form.branches[0] === b ? '（默认）' : '' }}</SelectItemText>
                      </SelectItem>
                    </SelectViewport>
                  </SelectContent>
                </SelectPortal>
              </SelectRoot>
              <button :class="btnGhost" :disabled="form.loadingBranches" @click="loadBranches">
                {{ form.loadingBranches ? '刷新中…' : '刷新' }}
              </button>
            </div>
            <div v-if="form.branchesError" class="mt-1 text-xs text-crit">{{ form.branchesError }}</div>
            <div v-else-if="!form.branchesLoaded" :class="hint">点击「刷新」拉取远端分支列表（需要项目已配置可用的访问凭据）</div>
          </div>
          <div>
            <label :class="label">扫描档位</label>
            <SelectRoot v-model="form.scanMode">
              <SelectTrigger :class="selectTrigger"><SelectValue /></SelectTrigger>
              <SelectPortal>
                <SelectContent :class="selectContent" position="popper">
                  <SelectViewport>
                    <SelectItem value="quick" :class="selectItem"><SelectItemText>quick（约 1 小时）</SelectItemText></SelectItem>
                    <SelectItem value="standard" :class="selectItem"><SelectItemText>standard（0.5~1 小时+）</SelectItemText></SelectItem>
                    <SelectItem value="deep" :class="selectItem"><SelectItemText>deep（1~4 小时+）</SelectItemText></SelectItem>
                  </SelectViewport>
                </SelectContent>
              </SelectPortal>
            </SelectRoot>
          </div>
          <div>
            <label :class="label">模型（网关 free 池，默认 {{ defaultModel || 'free' }}）</label>
            <SelectRoot v-model="form.model">
              <SelectTrigger :class="selectTrigger"><SelectValue placeholder="free" /></SelectTrigger>
              <SelectPortal>
                <SelectContent :class="selectContent" position="popper">
                  <SelectViewport>
                    <SelectItem v-for="m in models" :key="m" :value="m" :class="selectItem">
                      <SelectItemText>{{ m }}</SelectItemText>
                    </SelectItem>
                  </SelectViewport>
                </SelectContent>
              </SelectPortal>
            </SelectRoot>
          </div>
          <div>
            <label :class="label">报告语言</label>
            <SelectRoot v-model="form.reportLang">
              <SelectTrigger :class="selectTrigger"><SelectValue /></SelectTrigger>
              <SelectPortal>
                <SelectContent :class="selectContent" position="popper">
                  <SelectViewport>
                    <SelectItem value="zh" :class="selectItem"><SelectItemText>中文报告（提示词要求中文撰写 + LLM 翻译兜底）</SelectItemText></SelectItem>
                    <SelectItem value="en" :class="selectItem"><SelectItemText>英文报告</SelectItemText></SelectItem>
                  </SelectViewport>
                </SelectContent>
              </SelectPortal>
            </SelectRoot>
          </div>
          <template v-if="!isGit">
            <div class="col-span-2">
              <label :class="label">代码来源：历史上传 / 新上传</label>
              <RadioGroupRoot v-model="form.uploadChoice" class="flex gap-4.5">
                <div class="flex items-center gap-1.5">
                  <RadioGroupItem id="uc-history" value="history" :class="radioItem">
                    <RadioGroupIndicator class="h-2 w-2 rounded-full bg-accent" />
                  </RadioGroupItem>
                  <label for="uc-history" class="cursor-pointer text-[13px] text-text">复用历史上传</label>
                </div>
                <div class="flex items-center gap-1.5">
                  <RadioGroupItem id="uc-new" value="new" :class="radioItem">
                    <RadioGroupIndicator class="h-2 w-2 rounded-full bg-accent" />
                  </RadioGroupItem>
                  <label for="uc-new" class="cursor-pointer text-[13px] text-text">上传新压缩包</label>
                </div>
              </RadioGroupRoot>
            </div>
            <div v-if="form.uploadChoice === 'history'" class="col-span-2">
              <label :class="label">选择历史上传</label>
              <SelectRoot v-model="form.uploadId">
                <SelectTrigger :class="selectTrigger"><SelectValue placeholder="选择历史上传" /></SelectTrigger>
                <SelectPortal>
                  <SelectContent :class="selectContent" position="popper">
                    <SelectViewport>
                      <SelectItem v-for="u in project.uploads" :key="u.id" :value="u.id" :class="selectItem">
                        <SelectItemText>{{ u.filename }}（{{ fmtSize(u.size_bytes) }}，{{ fmtTime(u.created_at) }}）</SelectItemText>
                      </SelectItem>
                    </SelectViewport>
                  </SelectContent>
                </SelectPortal>
              </SelectRoot>
            </div>
            <div v-else class="col-span-2">
              <label :class="label">zip 压缩包（≤500MB，同时会存入项目历史上传）</label>
              <input type="file" accept=".zip" class="w-full rounded-md border border-border bg-panel2 p-1.5 text-[13.5px] text-text file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-accent/20 file:px-3 file:py-1 file:text-accent" @change="onFile" />
              <div v-if="form.fileName" :class="hint">已选择：{{ form.fileName }}</div>
            </div>
          </template>
          <div class="col-span-2">
            <label :class="label">黑盒测试地址（可选，扫描会发送真实攻击流量）</label>
            <input v-model="form.testUrl" type="text" placeholder="https://app-a.test.company.internal" :class="input" />
          </div>
        </div>
        <div class="mt-3.5 flex items-center gap-2.5">
          <span :class="hint">提交即表示确认对该目标执行安全测试已获授权。</span>
          <div class="flex-1"></div>
          <button class="cursor-pointer rounded-md border border-border bg-transparent px-5 py-2 text-sm font-semibold text-text" @click="showLaunch = false">取消</button>
          <button :class="btn" :disabled="launching" @click="launch">{{ launching ? '提交中…' : '发布任务' }}</button>
        </div>
        <div v-if="error" :class="errCls">{{ error }}</div>
      </div>

      <div v-if="!isGit" :class="card">
        <div class="mb-2.5 flex items-center gap-2.5">
          <h3 :class="[h3, 'mb-0']">历史上传（{{ project.uploads.length }}）</h3>
          <div class="flex-1"></div>
          <input type="file" accept=".zip" class="rounded-md border border-border bg-panel2 p-1.5 text-[13px] text-text file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-accent/20 file:px-3 file:py-1 file:text-accent" @change="uploadStandalone" />
        </div>
        <table v-if="project.uploads.length" class="w-full border-collapse">
          <thead><tr><th :class="tableTh">文件名</th><th :class="tableTh">大小</th><th :class="tableTh">上传时间</th><th :class="tableTh">操作</th></tr></thead>
          <tbody>
            <tr v-for="u in project.uploads" :key="u.id" class="hover:bg-accent/5">
              <td :class="tableTd">{{ u.filename }}</td>
              <td :class="tableTd">{{ fmtSize(u.size_bytes) }}</td>
              <td :class="tableTd">{{ fmtTime(u.created_at) }}</td>
              <td :class="tableTd"><a href="javascript:void(0)" class="text-crit" @click="removeUpload(u)">删除</a></td>
            </tr>
          </tbody>
        </table>
        <p v-else :class="hint">暂无历史 zip，发起任务时直接上传即可。</p>
      </div>

      <div :class="card">
        <h3 :class="h3">项目任务（每 5 秒自动刷新）</h3>
        <div v-if="submittedId" class="mb-2 text-xs text-ok">任务已提交：{{ submittedId.slice(0, 12) }}…，见下方列表。</div>
        <table v-if="project.tasks.length" class="w-full border-collapse">
          <thead>
            <tr>
              <th :class="tableTh">任务</th><th :class="tableTh">状态</th><th :class="tableTh">分支/来源</th>
              <th :class="tableTh">模式</th><th :class="tableTh">语言</th><th :class="tableTh">发现</th>
              <th :class="tableTh">耗时</th><th :class="tableTh">提交时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in project.tasks" :key="t.id" class="cursor-pointer hover:bg-accent/5" @click="openTask(t.id)">
              <td :class="[tableTd, 'font-mono']">{{ t.id.slice(0, 10) }}…</td>
              <td :class="tableTd">
                <span :class="statusBadgeClass(t.status)">{{ t.status }}</span>
                <span v-if="t.zh_status === 'pending'" :class="badge" class="ml-1 bg-[#9b7bff]/15 text-[#a78bfa]">翻译中</span>
              </td>
              <td :class="[tableTd, 'max-w-[200px] truncate']">
                {{ t.source_type === 'git' ? (t.branch || '默认分支') : t.source_ref }}
              </td>
              <td :class="tableTd">{{ t.scan_mode }}</td>
              <td :class="tableTd">{{ t.report_lang === 'zh' ? '中文' : 'EN' }}</td>
              <td :class="tableTd">{{ t.findings_count > 0 ? sevCounts(t) : (t.status === 'done' ? '无' : '-') }}</td>
              <td :class="tableTd">{{ fmtDur(t.duration_sec) }}</td>
              <td :class="tableTd">{{ fmtTime(t.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else :class="hint">项目下还没有任务，点击上方「发起扫描任务」。</p>
      </div>

      <div v-if="error && project" :class="errCls" style="margin-top: -10px">{{ error }}</div>
    </template>
  </div>
</template>
