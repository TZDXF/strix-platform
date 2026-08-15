<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
} from 'reka-ui'
import { api, type GitConfig, type GitProject, type Project, type User } from '../api'
import TargetUrlField from '../components/TargetUrlField.vue'
import { toast } from '../toast'
import { btn, btnGhost, card, cardLifted, hint, input, label, tableTd, tableTh, h3 } from '../ui'

const props = defineProps<{ user: User | null }>()
const projects = ref<Project[]>([])
const showCreate = ref(false)
const creating = ref(false)

// reka-ui SelectItem 不允许 value=""，用哨兵值表示「无需凭据」，提交时转回空串
const NO_AUTH = '__none__'
const ALL_ORGS = '__all__'

const emptyForm = {
  name: '', description: '', source_type: 'git',
  git_url: '', git_auth_type: NO_AUTH, git_token: '',
  default_test_url: '',
}
const form = ref({ ...emptyForm })

const authOptions = [
  { value: NO_AUTH, label: '无需凭据（公开仓库）' },
  { value: 'token', label: 'Personal Access Token' },
]

const typeOptions = [
  { value: 'git', label: 'Git 仓库' },
  { value: 'zip', label: '上传 zip 压缩包' },
]

const selectTrigger = 'flex w-full items-center justify-between rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent'
const selectItem = 'cursor-pointer rounded-md px-2.5 py-1.5 text-[13.5px] text-text data-highlighted:bg-accent/15 data-highlighted:outline-none'
const selectContent = 'z-50 max-h-72 min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-md border border-border bg-panel2 py-1 shadow-lg'

// ---- 个人 Git 配置导入（GitLab） ----
const gitConfigs = ref<GitConfig[]>([])
const gitMode = ref<'import' | 'manual'>('manual') // 有配置时默认导入，无配置只能手动
const selectedConfig = ref('') // GitConfig.id
const gitProjects = ref<GitProject[]>([])
const loadingRepos = ref(false)
const repoSearch = ref('')
const orgFilter = ref(ALL_ORGS)
const selectedRepo = ref<GitProject | null>(null)

async function loadGitConfigs() {
  try {
    gitConfigs.value = (await api.listGitConfigs()).items
    if (gitConfigs.value.length) {
      if (form.value.source_type === 'git' && gitMode.value !== 'manual') gitMode.value = 'import'
      if (!selectedConfig.value) selectedConfig.value = gitConfigs.value[0].id
    }
  } catch { /* 静默：导入功能不可用时仍可手动填写 */ }
}

function openCreate() {
  showCreate.value = true
  selectedRepo.value = null
  repoSearch.value = ''
  orgFilter.value = ALL_ORGS
  loadGitConfigs().then(() => {
    if (gitConfigs.value.length) {
      gitMode.value = form.value.source_type === 'git' ? 'import' : 'manual'
      if (selectedConfig.value) loadRepos()
    } else {
      gitMode.value = 'manual'
    }
  })
}

async function loadRepos() {
  if (!selectedConfig.value) return
  loadingRepos.value = true
  selectedRepo.value = null
  try {
    gitProjects.value = (await api.listGitProjects(selectedConfig.value)).items
    repoSearch.value = ''
    orgFilter.value = ALL_ORGS
  } catch (e) { toast.error((e as Error).message) } finally { loadingRepos.value = false }
}

// 按顶层组织/命名空间聚合（区分组织与个人项目）
function orgKey(p: GitProject): string {
  return p.namespace.full_path.split('/')[0] || p.namespace.name || '(其他)'
}

const orgOptions = computed(() => {
  const counts = new Map<string, { label: string; count: number }>()
  for (const p of gitProjects.value) {
    const key = orgKey(p)
    const entry = counts.get(key)
    if (entry) entry.count++
    else counts.set(key, { label: key, count: 1 })
  }
  const items = [...counts.entries()].map(([key, v]) => ({ key, ...v }))
  items.sort((a, b) => b.count - a.count)
  return items
})

const orgLabel = computed(() => {
  if (orgFilter.value === ALL_ORGS) return `全部组织（${gitProjects.value.length}）`
  const found = orgOptions.value.find((o) => o.key === orgFilter.value)
  if (!found) return '全部组织'
  const p = gitProjects.value.find((x) => orgKey(x) === orgFilter.value)
  const kind = p?.namespace.kind === 'user' ? '个人' : '组织'
  return `${kind}：${found.key}（${found.count}）`
})

const filteredRepos = computed(() => {
  const q = repoSearch.value.trim().toLowerCase()
  return gitProjects.value.filter((p) => {
    if (orgFilter.value !== ALL_ORGS && orgKey(p) !== orgFilter.value) return false
    if (q && !p.name.toLowerCase().includes(q) && !p.path_with_namespace.toLowerCase().includes(q)) return false
    return true
  })
})

function pickRepo(p: GitProject) {
  selectedRepo.value = selectedRepo.value?.id === p.id ? null : p
  if (selectedRepo.value) {
    form.value.git_url = p.http_url_to_repo || p.web_url
    if (!form.value.name) form.value.name = p.name
  } else {
    form.value.git_url = ''
  }
}

function switchMode(mode: 'import' | 'manual') {
  gitMode.value = mode
  if (mode === 'import' && !gitProjects.value.length && !loadingRepos.value) {
    loadGitConfigs().then(() => {
      if (!selectedConfig.value && gitConfigs.value.length) selectedConfig.value = gitConfigs.value[0].id
      loadRepos()
    })
  }
}

// ---- 手动输入仓库地址：测试访问（git ls-remote，同时校验地址与凭据），结果以 toast 提示 ----
const gitChecking = ref(false)

async function checkGitUrl() {
  const url = form.value.git_url.trim()
  if (!url) { toast.error('请先填写 Git 仓库地址'); return }
  gitChecking.value = true
  try {
    const r = await api.checkGitRepo(
      url,
      form.value.git_auth_type === 'token' ? 'token' : '',
      form.value.git_token,
    )
    if (r.reachable) {
      toast.success(`✓ 可访问：共 ${r.branches.length} 个分支，默认分支 ${r.branches[0] || '-'}，耗时 ${r.latency_ms}ms`)
    } else {
      toast.error(`✗ 无法访问：${r.detail}。请确认地址正确、私有仓库已配好凭据。`)
    }
  } catch (e) {
    toast.error(`探测失败：${(e as Error).message}`)
  } finally { gitChecking.value = false }
}

async function refresh() {
  try {
    projects.value = (await api.listProjects()).items
  } catch (e) { toast.error((e as Error).message) }
}

async function create() {
  if (form.value.source_type === 'git' && gitMode.value === 'import') {
    if (!selectedRepo.value) { toast.error('请先从列表中选择一个仓库'); return }
    if (!form.value.name.trim()) { toast.error('请填写项目名称'); return }
  }
  creating.value = true
  try {
    const payload: Parameters<typeof api.createProject>[0] = {
      ...form.value,
      git_auth_type: form.value.git_auth_type === NO_AUTH ? '' : form.value.git_auth_type,
    }
    if (form.value.source_type === 'git' && gitMode.value === 'import') {
      // 凭据由后端从所选 Git 配置复制（令牌不回显，前端拿不到）
      payload.git_config_id = selectedConfig.value
      payload.git_auth_type = 'token'
      payload.git_token = ''
    }
    await api.createProject(payload)
    toast.success('项目已创建')
    showCreate.value = false
    form.value = { ...emptyForm }
    selectedRepo.value = null
    gitProjects.value = []
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { creating.value = false }
}

function open(id: string) { location.hash = `#/project/${id}` }
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

const search = ref('')

// 首页入口说明的三步流程
const entrySteps = [
  { no: 1, title: '创建项目', desc: '从 Git 配置导入仓库或手动填写地址，也可以直接上传代码压缩包。' },
  { no: 2, title: '发起扫描', desc: '选择扫描档位与模型，可选填内网黑盒测试地址和自定义测试指令。' },
  { no: 3, title: '查看报告', desc: '查看漏洞明细与 PoC，下载中文 PDF 报告与完整产物归档。' },
]

const activeProjects = computed(() => {
  const q = search.value.trim().toLowerCase()
  return projects.value.filter(p => {
    if (p.is_archived) return false
    if (!q) return true
    return [p.name, p.git_url, p.description, p.created_by_name].some(v => v?.toLowerCase().includes(q))
  })
})
const archivedProjects = computed(() => projects.value.filter(p => p.is_archived))

async function restore(p: Project) {
  try { await api.unarchiveProject(p.id); toast.success(`项目「${p.name}」已恢复`); refresh() } catch (e) { toast.error((e as Error).message) }
}

onMounted(refresh)
</script>

<template>
  <div>
  <!-- 首页入口说明 -->
  <section :class="cardLifted" class="relative mb-[18px] overflow-hidden p-7">
    <!-- 氛围色块（仅装饰，不承载交互） -->
    <div class="pointer-events-none absolute -top-20 -right-16 size-64 rounded-full bg-[#e55cff]/12 blur-3xl"></div>
    <div class="pointer-events-none absolute -bottom-24 right-40 size-56 rounded-full bg-[#0099ff]/12 blur-3xl"></div>

    <div class="relative">
      <h1 class="max-w-2xl text-[30px] leading-tight font-bold text-text">
        欢迎使用 <span class="text-accent">Strix</span>，三步完成一次内部安全测试
      </h1>
      <p class="mt-2.5 max-w-2xl text-[13.5px] leading-relaxed text-muted">
        接入 Git 仓库或上传代码压缩包，由 AI 模型执行白盒代码审计与黑盒渗透测试，自动生成含漏洞明细、PoC 与修复建议的中文测试报告。
      </p>
      <div class="mt-4.5 flex flex-wrap items-center gap-3">
        <button :class="btn" @click="openCreate">新建项目开始</button>
        <a
          href="#/stats"
          class="cursor-pointer rounded-lg bg-text px-5.5 py-2 text-sm font-semibold text-panel transition-opacity hover:opacity-90"
        >查看统计汇总</a>
        <a href="#/tasks" class="cursor-pointer text-sm font-semibold text-accent hover:underline">浏览全部任务 →</a>
      </div>

      <!-- 使用流程 -->
      <div class="mt-6 grid gap-3.5 sm:grid-cols-3">
        <div
          v-for="step in entrySteps" :key="step.no"
          class="rounded-xl border border-border bg-panel2/60 p-4"
        >
          <div class="flex size-7 items-center justify-center rounded-full bg-accent/12 text-[13px] font-bold text-accent">
            {{ step.no }}
          </div>
          <div class="mt-2.5 text-sm font-semibold text-text">{{ step.title }}</div>
          <p class="mt-1 text-xs leading-relaxed text-muted">{{ step.desc }}</p>
        </div>
      </div>
    </div>
  </section>

  <div :class="card">
    <div class="mb-3.5 flex items-center gap-2.5">
      <h3 class="mb-0 text-sm font-semibold text-muted">项目（{{ props.user?.role === 'admin' ? '全部项目' : '我创建的项目' }}）</h3>
      <div class="flex-1"></div>
      <input v-model="search" type="text" placeholder="搜索项目 / 仓库 / 创建人" :class="[input, '!w-56']" />
      <button :class="btn" @click="openCreate">新建项目</button>
    </div>

    <table v-if="activeProjects.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">项目</th><th :class="tableTh">来源</th><th :class="tableTh">仓库 / 说明</th>
          <th :class="tableTh">任务数</th><th :class="tableTh">创建人</th><th :class="tableTh">创建时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in activeProjects" :key="p.id" class="cursor-pointer hover:bg-accent/5" @click="open(p.id)">
          <td :class="[tableTd, 'font-semibold']">{{ p.name }}</td>
          <td :class="tableTd">
            <span class="inline-block rounded-full bg-border/30 px-2.5 py-0.5 text-xs font-semibold text-muted">
              {{ p.source_type === 'git' ? 'Git' : 'zip 上传' }}
            </span>
          </td>
          <td :class="[tableTd, 'max-w-[320px] truncate']">
            {{ p.source_type === 'git' ? p.git_url : (p.description || '代码压缩包') }}
          </td>
          <td :class="tableTd">{{ p.tasks_count }}</td>
          <td :class="tableTd">{{ p.created_by_name }}</td>
          <td :class="tableTd">{{ fmtTime(p.created_at) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else :class="hint">还没有项目，点击右上角「新建项目」开始。</p>
  </div>

  <!-- 已归档项目（数据保留，可恢复） -->
  <div v-if="archivedProjects.length" :class="card" class="mt-4">
    <div class="mb-2.5 flex items-center gap-2.5">
      <h3 :class="[h3, 'mb-0']">已归档项目（{{ archivedProjects.length }}）</h3>
    </div>
    <p :class="hint">归档项目的任务与上传数据全部保留，仅不能再发起新任务；点击「恢复」可重新启用。</p>
    <table class="mt-2 w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">项目</th><th :class="tableTh">来源</th><th :class="tableTh">任务数</th>
          <th :class="tableTh">创建人</th><th :class="tableTh">创建时间</th><th :class="tableTh">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in archivedProjects" :key="p.id" class="cursor-pointer hover:bg-accent/5" @click="open(p.id)">
          <td :class="[tableTd, 'font-semibold']">
            {{ p.name }}
            <span class="ml-1.5 inline-block rounded-full bg-border/30 px-2 py-0.5 text-xs font-semibold text-muted">已归档</span>
          </td>
          <td :class="tableTd">{{ p.source_type === 'git' ? 'Git' : 'zip 上传' }}</td>
          <td :class="tableTd">{{ p.tasks_count }}</td>
          <td :class="tableTd">{{ p.created_by_name }}</td>
          <td :class="tableTd">{{ fmtTime(p.created_at) }}</td>
          <td :class="tableTd" @click.stop><button :class="btnGhost" @click="restore(p)">恢复</button></td>
        </tr>
      </tbody>
    </table>
  </div>

    <!-- 新建项目弹窗 -->
    <DialogRoot v-model:open="showCreate">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
        <DialogContent class="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[760px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[10px] border border-border bg-panel p-5">
          <DialogTitle class="mb-4 text-base font-semibold text-text">新建项目</DialogTitle>
          <div class="grid grid-cols-2 gap-3.5">
            <div>
              <label :class="label">项目名称</label>
              <input v-model="form.name" type="text" placeholder="业务前台 / app-a" :class="input" />
            </div>
            <div>
              <label :class="label">来源类型</label>
              <SelectRoot v-model="form.source_type">
                <SelectTrigger :class="selectTrigger"><SelectValue /></SelectTrigger>
                <SelectPortal>
                  <SelectContent :class="selectContent" position="popper">
                    <SelectViewport>
                      <SelectItem v-for="o in typeOptions" :key="o.value" :value="o.value" :class="selectItem">
                        <SelectItemText>{{ o.label }}</SelectItemText>
                      </SelectItem>
                    </SelectViewport>
                  </SelectContent>
                </SelectPortal>
              </SelectRoot>
            </div>

            <template v-if="form.source_type === 'git'">
              <!-- 仓库来源方式切换 -->
              <div class="col-span-2 flex items-center gap-2.5 rounded-lg bg-panel2 px-3 py-2">
                <button
                  v-if="gitConfigs.length" class="cursor-pointer rounded-md px-3 py-1 text-xs font-semibold"
                  :class="gitMode === 'import' ? 'bg-accent text-white' : 'border border-border text-muted'"
                  @click="switchMode('import')"
                >从 Git 配置选择</button>
                <button
                  class="cursor-pointer rounded-md px-3 py-1 text-xs font-semibold"
                  :class="gitMode === 'manual' ? 'bg-accent text-white' : 'border border-border text-muted'"
                  @click="switchMode('manual')"
                >手动输入</button>
              </div>

              <!-- 方式一：从个人 Git 配置选择仓库 -->
              <template v-if="gitMode === 'import'">
                <div class="col-span-2">
                  <label :class="label">Git 配置</label>
                  <div class="flex gap-2.5">
                    <div class="w-[280px]">
                      <SelectRoot v-model="selectedConfig" @update:model-value="loadRepos">
                        <SelectTrigger :class="selectTrigger"><SelectValue /></SelectTrigger>
                        <SelectPortal>
                          <SelectContent :class="selectContent" position="popper">
                            <SelectViewport>
                              <SelectItem v-for="c in gitConfigs" :key="c.id" :value="c.id" :class="selectItem">
                                <SelectItemText>{{ c.name }}（{{ c.base_url }}）</SelectItemText>
                              </SelectItem>
                            </SelectViewport>
                          </SelectContent>
                        </SelectPortal>
                      </SelectRoot>
                    </div>
                    <div class="w-[220px]">
                      <SelectRoot v-model="orgFilter">
                        <SelectTrigger :class="selectTrigger"><span class="truncate">{{ orgLabel }}</span></SelectTrigger>
                        <SelectPortal>
                          <SelectContent :class="selectContent" position="popper">
                            <SelectViewport>
                              <SelectItem :value="ALL_ORGS" :class="selectItem">
                                <SelectItemText>全部组织（{{ gitProjects.length }}）</SelectItemText>
                              </SelectItem>
                              <SelectItem v-for="o in orgOptions" :key="o.key" :value="o.key" :class="selectItem">
                                <SelectItemText>{{ o.label }}（{{ o.count }}）</SelectItemText>
                              </SelectItem>
                            </SelectViewport>
                          </SelectContent>
                        </SelectPortal>
                      </SelectRoot>
                    </div>
                    <div class="flex-1"></div>
                    <button :class="btnGhost + ' !px-3 !py-1.5 !text-xs'" :disabled="loadingRepos" @click="loadRepos">
                      {{ loadingRepos ? '加载中…' : '刷新' }}
                    </button>
                  </div>
                </div>
                <div class="col-span-2">
                  <label :class="label">搜索仓库（按名称 / 路径）</label>
                  <input v-model="repoSearch" type="text" placeholder="输入关键字过滤，如 app / api / web" :class="input" />
                </div>
                <div class="col-span-2">
                  <label :class="label">选择仓库（{{ filteredRepos.length }} 个）</label>
                  <div class="max-h-56 overflow-auto rounded-lg border border-border">
                    <div v-if="loadingRepos" class="px-3 py-4 text-center text-xs text-muted">正在从 Git 服务拉取仓库列表…</div>
                    <div v-else-if="!filteredRepos.length" class="px-3 py-4 text-center text-xs text-muted">
                      {{ gitProjects.length ? '没有匹配的仓库，试试其他关键字或切换组织。' : '未拉取到任何仓库（令牌需要 read_api / api 权限）。' }}
                    </div>
                    <div
                      v-for="p in filteredRepos" :key="p.id"
                      class="flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2 last:border-b-0 hover:bg-panel2"
                      :class="selectedRepo?.id === p.id ? 'bg-accent/10' : ''"
                      @click="pickRepo(p)"
                    >
                      <div class="min-w-0 flex-1">
                        <div class="flex items-center gap-2">
                          <span class="truncate text-[13.5px] font-semibold">{{ p.name }}</span>
                          <span
                            class="inline-block shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold"
                            :class="p.namespace.kind === 'user' ? 'bg-border/30 text-muted' : 'bg-accent/15 text-accent'"
                          >{{ p.namespace.kind === 'user' ? '个人' : '组织' }}</span>
                        </div>
                        <div class="truncate font-mono text-[11.5px] text-muted">{{ p.path_with_namespace }}</div>
                      </div>
                      <div class="shrink-0 text-right text-[11px] text-muted">
                        <div>最近活跃</div>
                        <div>{{ fmtTime(p.last_activity_at || null).split(' ')[0] }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 方式二：手动输入 -->
              <template v-else>
                <div class="col-span-2">
                  <label :class="label">Git 仓库地址（白盒源码扫描）</label>
                  <div class="flex gap-2">
                    <input v-model="form.git_url" type="text" placeholder="https://git.company.internal/team/app-a.git" :class="input" />
                    <button
                      :class="btnGhost + ' !px-3 !py-1.5 !text-xs'"
                      class="shrink-0 whitespace-nowrap"
                      :disabled="gitChecking"
                      @click="checkGitUrl"
                    >
                      {{ gitChecking ? '测试中…' : '测试访问' }}
                    </button>
                  </div>
                </div>
                <div>
                  <label :class="label">访问凭据（私有仓库需要）</label>
                  <SelectRoot v-model="form.git_auth_type">
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
                <div v-if="form.git_auth_type === 'token'" class="col-span-2">
                  <label :class="label">Personal Access Token（token 或 `用户名:token`，保存后不再回显）</label>
                  <input v-model="form.git_token" type="password" placeholder="glpat-xxxx / ghp_xxxx" :class="input" />
                </div>
              </template>
            </template>

            <div class="col-span-2">
              <TargetUrlField
                v-model="form.default_test_url"
                label="默认黑盒测试地址（可选，需内网测试环境）"
                placeholder="https://app-a.test.company.internal"
              />
            </div>
            <div class="col-span-2">
              <label :class="label">描述（可选）</label>
              <input v-model="form.description" type="text" placeholder="项目说明 / 负责人 / 测试范围" :class="input" />
            </div>
          </div>
          <div class="mt-4 flex items-center gap-2.5">
            <div class="flex-1"></div>
            <button class="cursor-pointer rounded-md border border-border bg-transparent px-5 py-2 text-sm font-semibold text-text" @click="showCreate = false">取消</button>
            <button :class="btn" :disabled="creating" @click="create">{{ creating ? '创建中…' : '创建项目' }}</button>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>
