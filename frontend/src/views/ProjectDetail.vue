<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
  RadioGroupRoot, RadioGroupItem, RadioGroupIndicator,
  CheckboxRoot, CheckboxIndicator,
} from 'reka-ui'
import { api, type GitRepoRef, type ProjectDetailData, type TestTarget, type User } from '../api'
import RepoListField from '../components/RepoListField.vue'
import TargetListField from '../components/TargetListField.vue'
import { toast } from '../toast'
import {
  badge, btn, btnDanger, btnGhost, card, hint, h3, input, label, statusBadgeClass, tableTd, tableTh,
} from '../ui'

const props = defineProps<{ projectId: string; user: User | null }>()
const project = ref<ProjectDetailData | null>(null)
const showLaunch = ref(false)
const launching = ref(false)
let timer: number | undefined

// 每个仓库一个分支选择器：{url, note, branch, branches, loaded, loading}
interface RepoBranchSel {
  url: string
  note: string
  branch: string
  branches: string[]
  loaded: boolean
  loading: boolean
}

const form = ref({
  scanMode: 'quick', model: '', blackbox: false, testTargets: [] as TestTarget[], instruction: '',
  repos: [] as RepoBranchSel[],
  uploadChoice: 'new' as 'history' | 'new', uploadId: '', file: null as File | null, fileName: '',
})

// 提交前统一清洗：trim + 丢弃未填地址的行（地址列表与仓库列表同构，共用）
function cleanTargets<T extends { url: string; note: string }>(list: T[]): T[] {
  return list.map(t => ({ ...t, url: t.url.trim(), note: t.note.trim() })).filter(t => t.url)
}

// 仓库短名（URL 末段去 .git），用于分支选择器与展示
function repoBaseName(url: string): string {
  const seg = url.replace(/\/+$/, '').split(/[/:]/).pop() || url
  return seg.endsWith('.git') ? seg.slice(0, -4) : seg
}
const models = ref<string[]>([])
const defaultModel = ref('')

const isGit = computed(() => project.value?.source_type === 'git')

const showEdit = ref(false)
const savingEdit = ref(false)
const editForm = ref({
  name: '', description: '', git_repos: [] as GitRepoRef[],
  clearProjectToken: false, default_test_targets: [] as TestTarget[],
})

function openEdit() {
  const p = project.value
  if (!p) return
  editForm.value = {
    name: p.name, description: p.description,
    // 令牌不回显：行内 token 置空，留空提交=保持各仓库已存令牌
    git_repos: (p.git_repos || []).map(r => ({ ...r, token: '' })),
    clearProjectToken: false, default_test_targets: (p.default_test_targets || []).map(t => ({ ...t })),
  }
  showEdit.value = true
}

async function saveEdit() {
  if (!editForm.value.name.trim()) { toast.error('项目名称不能为空'); return }
  const repos = cleanTargets(editForm.value.git_repos)
  if (isGit.value && !repos.length) { toast.error('Git 项目必须至少保留一个仓库'); return }
  savingEdit.value = true
  try {
    await api.patchProject(props.projectId, {
      name: editForm.value.name.trim(),
      description: editForm.value.description.trim(),
      // 列表全量提交：仓库列表与默认地址列表，空数组表示清空（仓库至少保留一个，上方已校验）
      ...(isGit.value ? {
        git_repos: repos,
        // 旧版项目级统一 PAT：仅在用户勾选清除时提交清空（现在凭据按仓库单独配置）
        ...(editForm.value.clearProjectToken ? { git_auth_type: '' } : {}),
      } : {}),
      default_test_targets: cleanTargets(editForm.value.default_test_targets),
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

// 逐仓库加载分支列表（idx 缺省 = 全部仓库）；默认分支排首位并自动选中
async function loadRepoBranches(idx?: number) {
  const repos = form.value.repos
  const idxs = idx == null ? repos.map((_, i) => i) : [idx]
  if (!idxs.length) return
  idxs.forEach(i => { repos[i].loading = true })
  try {
    await Promise.all(idxs.map(async i => {
      try {
        repos[i].branches = (await api.listBranches(props.projectId, repos[i].url)).items || []
        repos[i].loaded = true
        if (repos[i].branches.length && !repos[i].branch) repos[i].branch = repos[i].branches[0]
      } catch (e) {
        toast.error(`获取仓库「${repoBaseName(repos[i].url)}」分支失败：${(e as Error).message}`)
      }
    }))
  } finally {
    idxs.forEach(i => { repos[i].loading = false })
  }
}

function openLaunch() {
  const defaults = (project.value?.default_test_targets || []).map(t => ({ ...t }))
  form.value.testTargets = defaults
  form.value.blackbox = defaults.length > 0
  form.value.repos = (project.value?.git_repos || []).map(r => ({
    url: r.url, note: r.note, branch: '', branches: [], loaded: false, loading: false,
  }))
  loadModels()
  if (isGit.value) loadRepoBranches()
  else if (project.value?.uploads.length) {
    form.value.uploadChoice = 'history'
    form.value.uploadId = project.value.uploads[0].id
  }
  showLaunch.value = true
}

async function launch() {
  const f = form.value
  if (isGit.value && f.repos.some(r => !r.branch)) { toast.error('请为每个仓库选择扫描分支（可点「刷新」重试拉取）'); return }
  const targets = cleanTargets(f.testTargets)
  if (f.blackbox && !targets.length) { toast.error('已勾选黑盒测试，请至少填写一个黑盒测试地址，或取消勾选仅做白盒扫描'); return }
  if (!isGit.value && f.uploadChoice === 'history' && !f.uploadId) { toast.error('请选择历史上传'); return }
  if (!isGit.value && f.uploadChoice === 'new' && !f.file) { toast.error('请选择 zip 压缩包'); return }
  launching.value = true
  try {
    // 黑盒任务提交前逐个探测目标：不在允许清单的直接拦截；无法访问的汇总后确认一次，避免任务空跑
    if (f.blackbox) {
      const results = await Promise.allSettled(targets.map(t => api.checkTarget(t.url)))
      const notAllowed: string[] = []
      const unreachable: string[] = []
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          if (!r.value.allowed) notAllowed.push(`${targets[i].url}：${r.value.reason}`)
          else if (r.value.reachable === false) unreachable.push(`${targets[i].url}（${r.value.detail}）`)
        } else {
          unreachable.push(`${targets[i].url}（探测接口异常：${(r.reason as Error)?.message || '未知错误'}）`)
        }
      })
      if (notAllowed.length) { toast.error(`以下地址不在允许清单：${notAllowed.join('；')}`); return }
      if (unreachable.length &&
        !confirm(`以下黑盒测试地址当前无法访问：\n${unreachable.join('\n')}\n\n请确认目标服务已启动（可在地址旁点「测试访问」查看详情）。是否仍要提交任务？`)) {
        return
      }
    }
    const res = await api.submitTask(props.projectId, {
      scanMode: f.scanMode,
      repoBranches: isGit.value ? f.repos.map(r => ({ url: r.url, branch: r.branch })) : undefined,
      testTargets: f.blackbox ? targets : [],
      instruction: f.instruction.trim(),
      model: f.model,
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
            <div class="text-xs text-muted">代码仓库（{{ project.git_repos?.length || 0 }} 个）</div>
            <div class="mt-0.5 flex flex-col gap-0.5">
              <div v-for="r in project.git_repos" :key="r.url" class="text-[12.5px] font-semibold break-all">
                {{ r.url }}
                <span v-if="r.note" class="font-normal text-muted">（{{ r.note }}）</span>
                <span
                  class="ml-1 inline-block rounded-full px-1.5 py-0.5 align-middle text-[10.5px] font-semibold"
                  :class="r.credential === 'repo' ? 'bg-ok/15 text-ok' : (r.credential === 'project' ? 'bg-accent/15 text-accent' : 'bg-border/30 text-muted')"
                  :title="r.credential === 'repo' ? '已保存该仓库专属令牌（手动填写或从个人 Git 配置按域名解析）' : (r.credential === 'project' ? '使用旧版项目级统一 PAT' : '无凭据（仅公开仓库可克隆）')"
                >{{ r.credential === 'repo' ? '仓库令牌' : (r.credential === 'project' ? '项目级令牌' : '无凭据') }}</span>
              </div>
            </div>
          </div>
          <div class="rounded-lg bg-panel2 px-3 py-2.5">
            <div class="text-xs text-muted">默认黑盒地址（{{ project.default_test_targets?.length || 0 }} 个）</div>
            <div v-if="project.default_test_targets?.length" class="mt-0.5 flex flex-col gap-0.5">
              <div v-for="t in project.default_test_targets" :key="t.url" class="text-[12.5px] font-semibold break-all">
                {{ t.url }}<span v-if="t.note" class="font-normal text-muted">（{{ t.note }}）</span>
              </div>
            </div>
            <div v-else class="mt-0.5 text-[12.5px] font-semibold">（仅白盒）</div>
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
                {{ t.source_type === 'git'
                  ? (t.repo_branches?.length > 1 ? `${t.repo_branches.length} 个仓库` : (t.branch || '默认分支'))
                  : t.source_ref }}
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
              <div v-if="isGit" class="col-span-2">
                <div class="mb-1.5 flex items-center gap-2.5">
                  <label :class="label" class="!mb-0">
                    扫描分支{{ form.repos.length > 1 ? `（${form.repos.length} 个仓库，各自选择分支）` : '' }}
                  </label>
                  <button
                    :class="btnGhost" class="!px-3 !py-1 !text-xs"
                    :disabled="form.repos.some(r => r.loading)" @click="loadRepoBranches()"
                  >
                    {{ form.repos.some(r => r.loading) ? '刷新中…' : '刷新' }}
                  </button>
                </div>
                <div class="flex flex-col gap-2">
                  <div
                    v-for="(r, i) in form.repos" :key="r.url"
                    class="flex items-center gap-2.5 rounded-lg border border-border bg-panel2/40 px-2.5 py-2"
                  >
                    <span class="shrink-0 text-xs font-bold text-muted">{{ i + 1 }}</span>
                    <div class="min-w-0 flex-1" :title="r.url">
                      <div class="truncate font-mono text-[12.5px] font-semibold">{{ repoBaseName(r.url) }}</div>
                      <div v-if="r.note" class="truncate text-[11px] text-muted">{{ r.note }}</div>
                    </div>
                    <div class="w-48 shrink-0">
                      <SelectRoot v-model="r.branch">
                        <SelectTrigger :class="selectTrigger">
                          <SelectValue :placeholder="r.loading ? '分支拉取中…' : '选择分支'" />
                        </SelectTrigger>
                        <SelectPortal>
                          <SelectContent :class="selectContent" position="popper">
                            <SelectViewport>
                              <SelectItem v-for="b in r.branches" :key="b" :value="b" :class="selectItem">
                                <SelectItemText>{{ b }}{{ r.branches[0] === b ? '（默认）' : '' }}</SelectItemText>
                              </SelectItem>
                            </SelectViewport>
                          </SelectContent>
                        </SelectPortal>
                      </SelectRoot>
                    </div>
                  </div>
                </div>
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
                  <CheckboxRoot v-model="form.blackbox"
                    class="flex h-4 w-4 cursor-pointer items-center justify-center rounded border border-border bg-panel2 data-state-checked:border-accent data-state-checked:bg-accent/20">
                    <CheckboxIndicator class="text-xs leading-none text-accent">✓</CheckboxIndicator>
                  </CheckboxRoot>
                  执行黑盒测试（扫描会向测试地址发送真实攻击流量）
                </label>
              </div>
              <div v-if="form.blackbox" class="col-span-2">
                <TargetListField
                  v-model="form.testTargets"
                  label="黑盒测试地址（可添加多个，建议注明每个地址的作用）"
                  hint="每个地址都会作为独立目标传入扫描引擎；作用说明会随测试指令注入，帮助引擎理解各入口用途。"
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
                <label :class="label">描述（可选，清空后提交则保持原描述）</label>
                <input v-model="editForm.description" type="text" placeholder="项目说明 / 负责人 / 测试范围" :class="input" />
              </div>
              <div v-if="isGit" class="col-span-2">
                <RepoListField
                  v-model="editForm.git_repos"
                  label="代码仓库（可绑定多个，一次扫描覆盖全部）"
                  hint="每个仓库可单独填写专属访问令牌（保存后不回显，留空=保持已存令牌）；未填写时按域名自动使用「设置」中的个人 Git 服务密钥，公开仓库无需填写。凭据状态见仓库行内徽标。"
                />
              </div>
              <div v-if="isGit && project.has_credentials" class="col-span-2 flex items-center gap-2.5 rounded-lg bg-panel2 px-3 py-2 text-xs text-muted">
                <span>
                  该项目仍保有旧版「项目级统一 PAT」，未单独配置令牌的仓库用它兜底；
                  已为仓库单独保存令牌的优先使用各自令牌。
                </span>
                <label class="ml-auto flex shrink-0 cursor-pointer items-center gap-1.5 font-semibold">
                  <input v-model="editForm.clearProjectToken" type="checkbox" class="accent-accent" />
                  保存时清除
                </label>
              </div>
              <div class="col-span-2">
                <TargetListField
                  v-model="editForm.default_test_targets"
                  label="默认黑盒测试地址（可选，发起任务时预填；可添加多个并注明作用）"
                />
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
