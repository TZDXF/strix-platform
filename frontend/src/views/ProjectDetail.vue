<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
  RadioGroupRoot, RadioGroupItem, RadioGroupIndicator,
  CheckboxRoot, CheckboxIndicator,
} from 'reka-ui'
import { api, type ProjectDetailData, type User } from '../api'
import TargetUrlField from '../components/TargetUrlField.vue'
import { toast } from '../toast'
import {
  badge, btn, btnDanger, btnGhost, card, hint, h3, input, label, statusBadgeClass, tableTd, tableTh,
} from '../ui'

const props = defineProps<{ projectId: string; user: User | null }>()
const project = ref<ProjectDetailData | null>(null)
const showLaunch = ref(false)
const launching = ref(false)
let timer: number | undefined

const form = ref({
  scanMode: 'quick', model: '', blackbox: false, testUrl: '', instruction: '',
  branch: '', branches: [] as string[], branchesLoaded: false, loadingBranches: false,
  uploadChoice: 'new' as 'history' | 'new', uploadId: '', file: null as File | null, fileName: '',
})
const models = ref<string[]>([])
const defaultModel = ref('')

const isGit = computed(() => project.value?.source_type === 'git')

// reka-ui SelectItem 不允许 value=""，用哨兵值表示「无需凭据」，提交时转回空串
const NO_AUTH = '__none__'

const showEdit = ref(false)
const savingEdit = ref(false)
const editForm = ref({
  name: '', description: '', git_url: '', git_auth_type: NO_AUTH,
  git_token: '', default_test_url: '',
})

const authOptions = [
  { value: NO_AUTH, label: '无需凭据（公开仓库）' },
  { value: 'token', label: 'Personal Access Token' },
]

function openEdit() {
  const p = project.value
  if (!p) return
  editForm.value = {
    name: p.name, description: p.description,
    git_url: p.git_url, git_auth_type: p.has_credentials ? p.git_auth_type : NO_AUTH,
    git_token: '', default_test_url: p.default_test_url,
  }
  showEdit.value = true
}

async function saveEdit() {
  if (!editForm.value.name.trim()) { toast.error('项目名称不能为空'); return }
  savingEdit.value = true
  try {
    await api.patchProject(props.projectId, {
      name: editForm.value.name.trim(),
      description: editForm.value.description.trim(),
      ...(isGit.value ? {
        git_url: editForm.value.git_url.trim(),
        git_auth_type: editForm.value.git_auth_type === NO_AUTH ? '' : editForm.value.git_auth_type,
        ...(editForm.value.git_token.trim() ? { git_token: editForm.value.git_token.trim() } : {}),
      } : {}),
      ...(editForm.value.default_test_url.trim() ? { default_test_url: editForm.value.default_test_url.trim() } : {}),
    })
    toast.success('项目已保存')
    showEdit.value = false
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { savingEdit.value = false }
}

const selectTrigger = 'flex w-full items-center justify-between rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent disabled:opacity-50'
const selectItem = 'cursor-pointer rounded-md px-2.5 py-1.5 text-[13.5px] text-text data-highlighted:bg-accent/15 data-highlighted:outline-none'
const selectContent = 'z-50 max-h-72 min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-md border border-border bg-panel2 py-1 shadow-lg'
const radioItem = 'flex h-4.5 w-4.5 cursor-pointer items-center justify-center rounded-full border border-border bg-panel2 data-state-checked:border-accent'

async function refresh() {
  try {
    project.value = await api.getProject(props.projectId)
  } catch (e) { toast.error((e as Error).message) }
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
  f.loadingBranches = true
  try {
    f.branches = (await api.listBranches(props.projectId)).items || []
    f.branchesLoaded = true
    if (f.branches.length) f.branch = f.branches[0]  // 默认分支排首位
  } catch (e) { toast.error((e as Error).message) } finally { f.loadingBranches = false }
}

function openLaunch() {
  form.value.testUrl = project.value?.default_test_url || ''
  form.value.blackbox = !!project.value?.default_test_url
  loadModels()
  if (isGit.value && !form.value.branchesLoaded) loadBranches()
  else if (!isGit.value && project.value?.uploads.length) {
    form.value.uploadChoice = 'history'
    form.value.uploadId = project.value.uploads[0].id
  }
  showLaunch.value = true
}

async function launch() {
  const f = form.value
  if (isGit.value && !f.branch) { toast.error('请选择要扫描的分支'); return }
  if (f.blackbox && !f.testUrl.trim()) { toast.error('已勾选黑盒测试，请填写黑盒测试地址，或取消勾选仅做白盒扫描'); return }
  if (!isGit.value && f.uploadChoice === 'history' && !f.uploadId) { toast.error('请选择历史上传'); return }
  if (!isGit.value && f.uploadChoice === 'new' && !f.file) { toast.error('请选择 zip 压缩包'); return }
  launching.value = true
  try {
    // 黑盒任务提交前先探测一次目标：无法访问时提示用户确认，避免任务空跑
    if (f.blackbox) {
      let reachable: boolean | null = null
      let failMsg = ''
      try {
        const r = await api.checkTarget(f.testUrl.trim())
        if (!r.allowed) { toast.error(r.reason); return }
        reachable = r.reachable
        failMsg = r.detail
      } catch (e) {
        failMsg = (e as Error).message  // 探测接口本身异常：提示但不阻塞提交
      }
      if (reachable === false &&
        !confirm(`黑盒测试地址当前无法访问：${failMsg}\n\n请确认目标服务已启动（可在地址旁点「测试访问」查看详情）。是否仍要提交任务？`)) {
        return
      }
    }
    const res = await api.submitTask(props.projectId, {
      scanMode: f.scanMode,
      testUrl: f.blackbox ? f.testUrl.trim() : '',
      instruction: f.instruction.trim(),
      model: f.model,
      branch: isGit.value ? f.branch : '',
      uploadId: !isGit.value && f.uploadChoice === 'history' ? f.uploadId : '',
      file: !isGit.value && f.uploadChoice === 'new' ? f.file : null,
    })
    toast.success(`任务已提交（${res.id.slice(0, 12)}…），见下方任务列表`)
    showLaunch.value = false
    f.file = null; f.fileName = ''
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { launching.value = false }
}

function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) { form.value.file = f; form.value.fileName = f.name }
}

async function uploadStandalone(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  try {
    await api.uploadToProject(props.projectId, f)
    toast.success('压缩包已上传到项目历史')
    ;(e.target as HTMLInputElement).value = ''
    refresh()
  } catch (err) { toast.error((err as Error).message) }
}

async function removeUpload(u: { id: string; filename: string }) {
  if (!confirm(`删除历史上传「${u.filename}」？`)) return
  try { await api.deleteUpload(props.projectId, u.id); toast.success(`已删除上传「${u.filename}」`); refresh() } catch (err) { toast.error((err as Error).message) }
}

async function archiveProject() {
  if (!confirm(`确定归档项目「${project.value?.name}」？归档后项目及其任务、上传数据全部保留，仅不能再发起新任务，可随时恢复。`)) return
  try { await api.archiveProject(props.projectId); toast.success('项目已归档'); refresh() } catch (err) { toast.error((err as Error).message) }
}

async function restoreProject() {
  try { await api.unarchiveProject(props.projectId); toast.success('项目已恢复'); refresh() } catch (err) { toast.error((err as Error).message) }
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

    <template v-if="project">
      <div :class="card">
        <div class="mb-3.5 flex items-start gap-2.5">
          <h3 class="m-0 text-base font-semibold text-text">
            {{ project.name }}
            <span :class="[badge, 'bg-border/30 text-muted']" class="ml-2">Git 仓库</span>
            <span v-if="project.is_archived" class="ml-1 inline-block rounded-full bg-border/30 px-2.5 py-0.5 text-xs font-semibold text-muted">已归档</span>
          </h3>
          <div class="flex-1"></div>
          <button :class="btnGhost" @click="openEdit">编辑项目</button>
          <button v-if="project.is_archived" :class="btn" @click="restoreProject">恢复项目</button>
          <template v-else>
            <button :class="btnDanger" @click="archiveProject">归档项目</button>
            <button :class="btn" @click="openLaunch">发起扫描任务</button>
          </template>
        </div>
        <div class="mb-4 grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(170px, 1fr))">
          <div v-if="isGit" class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">仓库地址</div>
            <div class="mt-0.5 text-[12.5px] font-semibold break-all">{{ project.git_url }}</div>
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
      </div>

      <div v-if="!isGit" :class="card">
        <div class="mb-2.5 flex items-center gap-2.5">
          <h3 :class="[h3, 'mb-0']">历史上传（{{ project.uploads.length }}）</h3>
          <div class="flex-1"></div>
          <input v-if="!project.is_archived" type="file" accept=".zip" class="rounded-md border border-border bg-panel2 p-1.5 text-[13px] text-text file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-accent/20 file:px-3 file:py-1 file:text-accent" @change="uploadStandalone" />
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
        <table v-if="project.tasks.length" class="w-full border-collapse">
          <thead>
            <tr>
              <th :class="tableTh">任务</th><th :class="tableTh">状态</th><th :class="tableTh">分支/来源</th>
              <th :class="tableTh">模式</th><th :class="tableTh">发现</th>
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
              <td :class="tableTd">{{ t.findings_count > 0 ? sevCounts(t) : (t.status === 'done' ? '无' : '-') }}</td>
              <td :class="tableTd">{{ fmtDur(t.duration_sec) }}</td>
              <td :class="tableTd">{{ fmtTime(t.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else :class="hint">项目下还没有任务，点击上方「发起扫描任务」。</p>
      </div>

      <!-- 发起扫描任务弹窗 -->
      <DialogRoot v-model:open="showLaunch">
        <DialogPortal>
          <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
          <DialogContent class="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[680px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[10px] border border-border bg-panel p-5">
            <DialogTitle class="mb-4 text-base font-semibold text-text">发起扫描任务</DialogTitle>
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
                </div>
              </template>
              <div class="col-span-2">
                <label :class="label">测试方式</label>
                <label class="flex cursor-pointer items-center gap-1.5 text-[13px] text-text">
                  <CheckboxRoot v-model:checked="form.blackbox"
                    class="flex h-4 w-4 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                    <CheckboxIndicator class="text-xs leading-none text-accent">✓</CheckboxIndicator>
                  </CheckboxRoot>
                  执行黑盒测试（扫描会向测试地址发送真实攻击流量）
                </label>
              </div>
              <div v-if="form.blackbox" class="col-span-2">
                <TargetUrlField
                  v-model="form.testUrl"
                  label="黑盒测试地址"
                  placeholder="https://app-a.test.company.internal"
                />
              </div>
              <div class="col-span-2">
                <label :class="label">测试指令（可选，最长 4000 字符）</label>
                <textarea
                  v-model="form.instruction"
                  rows="3"
                  placeholder="聚焦的漏洞类型或测试要求，例如：&#10;Focus on IDOR and SQL injection.&#10;Use credentials admin:password123 for authenticated testing.&#10;重点检查 /api/v2 下的接口鉴权。"
                  class="w-full resize-y rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent"
                ></textarea>
              </div>
            </div>
            <div class="mt-4 flex items-center gap-2.5">
              <div class="flex-1"></div>
              <button :class="btnGhost" @click="showLaunch = false">取消</button>
              <button :class="btn" :disabled="launching" @click="launch">{{ launching ? '提交中…' : '发布任务' }}</button>
            </div>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>

      <!-- 编辑项目弹窗 -->
      <DialogRoot v-model:open="showEdit">
        <DialogPortal>
          <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
          <DialogContent class="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[640px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[10px] border border-border bg-panel p-5">
            <DialogTitle class="mb-4 text-base font-semibold text-text">编辑项目</DialogTitle>
            <div class="grid grid-cols-2 gap-3.5">
              <div>
                <label :class="label">项目名称</label>
                <input v-model="editForm.name" type="text" :class="input" />
              </div>
              <div>
                <TargetUrlField
                  v-model="editForm.default_test_url"
                  label="默认黑盒测试地址（可选）"
                  placeholder="https://app-a.test.company.internal"
                />
              </div>
              <div v-if="isGit" class="col-span-2">
                <label :class="label">Git 仓库地址</label>
                <input v-model="editForm.git_url" type="text" :class="input" />
              </div>
              <div v-if="isGit">
                <label :class="label">访问凭据（当前：{{ project.has_credentials ? `已配置（${project.git_auth_type}）` : '未配置' }}）</label>
                <SelectRoot v-model="editForm.git_auth_type">
                  <SelectTrigger :class="selectTrigger"><SelectValue /></SelectTrigger>
                  <SelectPortal>
                    <SelectContent :class="selectContent" position="popper">
                      <SelectViewport>
                        <SelectItem v-for="o in authOptions" :key="o.value" :value="o.value" :class="selectItem">
                          <SelectItemText>{{ o.label }}</SelectItemText>
                        </SelectItem>
                      </SelectViewport>
                    </SelectContent>
                  </SelectPortal>
                </SelectRoot>
              </div>
              <div v-if="isGit && editForm.git_auth_type === 'token'" class="col-span-2">
                <label :class="label">Personal Access Token（{{ project.git_auth_type === 'token' ? '留空保持现有 token' : '必填' }}）</label>
                <input v-model="editForm.git_token" type="password" placeholder="glpat-xxxx / ghp_xxxx" :class="input" />
              </div>
              <div class="col-span-2">
                <label :class="label">描述（可选，清空后提交则保持原描述）</label>
                <input v-model="editForm.description" type="text" placeholder="项目说明 / 负责人 / 测试范围" :class="input" />
              </div>
            </div>
            <div class="mt-4 flex items-center gap-2.5">
              <div class="flex-1"></div>
              <button :class="btnGhost" @click="showEdit = false">取消</button>
              <button :class="btn" :disabled="savingEdit" @click="saveEdit">{{ savingEdit ? '保存中…' : '保存' }}</button>
            </div>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>
    </template>
  </div>
</template>
