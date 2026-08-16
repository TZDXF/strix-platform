<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
} from 'reka-ui'
import { api, type GitConfig, type GitProject, type GitRepoRef, type Project, type TestTarget, type User } from '../api'
import RepoListField from '../components/RepoListField.vue'
import TargetListField from '../components/TargetListField.vue'
import { toast } from '../toast'
import { btn, btnGhost, card, hint, input, label, tableTd, tableTh, h3 } from '../ui'

const props = defineProps<{ user: User | null }>()
const projects = ref<Project[]>([])
const showCreate = ref(false)
const creating = ref(false)

// reka-ui SelectItem 不允许 value=""，用哨兵值表示「全部组织」
const ALL_ORGS = '__all__'

const emptyForm = {
  name: '', description: '', source_type: 'git',
  git_repos: [] as GitRepoRef[],
  default_test_targets: [] as TestTarget[],
}
const form = ref({ ...emptyForm })

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
const selectedRepos = ref<GitProject[]>([]) // 可多选：一个项目绑定多个仓库

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
  selectedRepos.value = []
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
  selectedRepos.value = []
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

// 点击切换选中（多选）：同一项目可绑定多个仓库，一次扫描覆盖全部
function pickRepo(p: GitProject) {
  const idx = selectedRepos.value.findIndex(x => x.id === p.id)
  if (idx >= 0) selectedRepos.value.splice(idx, 1)
  else {
    selectedRepos.value.push(p)
    if (!form.value.name) form.value.name = p.name
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

async function refresh() {
  try {
    projects.value = (await api.listProjects()).items
  } catch (e) { toast.error((e as Error).message) }
}

async function create() {
  if (form.value.source_type === 'git') {
    if (gitMode.value === 'import') {
      if (!selectedRepos.value.length) { toast.error('请从列表中选择至少一个仓库（可多选）'); return }
      if (!form.value.name.trim()) { toast.error('请填写项目名称'); return }
    } else if (!form.value.git_repos.some(r => r.url.trim())) {
      toast.error('请至少填写一个 Git 仓库地址'); return
    }
  }
  creating.value = true
  try {
    // 仓库列表：导入模式取自所选 GitLab 仓库（可多选），手动模式取表单（丢弃空行，携带逐仓库令牌）
    const repos: GitRepoRef[] = form.value.source_type === 'git' && gitMode.value === 'import'
      ? selectedRepos.value.map(p => ({ url: (p.http_url_to_repo || p.web_url).trim(), note: '' }))
      : form.value.git_repos
          .map(r => ({ url: r.url.trim(), note: r.note.trim(), token: (r.token || '').trim() }))
          .filter(r => r.url)
    const payload: Parameters<typeof api.createProject>[0] = {
      name: form.value.name.trim(),
      description: form.value.description.trim(),
      source_type: form.value.source_type,
      git_repos: repos,
      default_test_targets: form.value.default_test_targets
        .map(t => ({ url: t.url.trim(), note: t.note.trim() }))
        .filter(t => t.url),
    }
    if (form.value.source_type === 'git' && gitMode.value === 'import') {
      // 凭据由后端从所选 Git 配置逐仓库复制（令牌不回显，前端拿不到）
      payload.git_config_id = selectedConfig.value
    }
    await api.createProject(payload)
    toast.success('项目已创建')
    showCreate.value = false
    form.value = { ...emptyForm }
    selectedRepos.value = []
    gitProjects.value = []
    refresh()
  } catch (e) { toast.error((e as Error).message) } finally { creating.value = false }
}

function open(id: string) { location.hash = `#/project/${id}` }
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

const search = ref('')

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

onMounted(() => {
  refresh()
  // 从欢迎页「新建项目开始」跳转而来（#/projects?create=1）时自动打开新建弹窗
  if (location.hash.includes('create=1')) {
    openCreate()
    // 只清掉查询后缀，不产生新的历史记录（视图仍为 projects，无需触发路由变化）
    history.replaceState(null, '', '#/projects')
  }
})
</script>

<template>
  <div>
  <!-- 页头 -->
  <div class="mb-[18px] flex items-end gap-3">
    <div>
      <h1 class="text-[28px] leading-tight font-bold text-text">项目</h1>
      <p class="mt-1 text-[13px] text-muted">
        {{ props.user?.role === 'admin' ? '全部项目（含各用户创建）' : '你创建的项目' }}，创建后即可发起扫描；
        完整操作步骤见<a href="#/welcome" class="cursor-pointer font-semibold text-accent hover:underline">使用指南</a>。
      </p>
    </div>
    <div class="flex-1"></div>
    <button :class="btn" @click="openCreate">新建项目</button>
  </div>

  <div :class="card">
    <div class="mb-3.5 flex items-center gap-2.5">
      <h3 class="mb-0 text-sm font-semibold text-muted">项目列表（{{ activeProjects.length }}）</h3>
      <div class="flex-1"></div>
      <input v-model="search" type="text" placeholder="搜索项目 / 仓库 / 创建人" :class="[input, '!w-56']" />
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
                  <label :class="label">选择仓库（{{ filteredRepos.length }} 个，已选 {{ selectedRepos.length }}，可多选）</label>
                  <div class="max-h-56 overflow-auto rounded-lg border border-border">
                    <div v-if="loadingRepos" class="px-3 py-4 text-center text-xs text-muted">正在从 Git 服务拉取仓库列表…</div>
                    <div v-else-if="!filteredRepos.length" class="px-3 py-4 text-center text-xs text-muted">
                      {{ gitProjects.length ? '没有匹配的仓库，试试其他关键字或切换组织。' : '未拉取到任何仓库（令牌需要 read_api / api 权限）。' }}
                    </div>
                    <div
                      v-for="p in filteredRepos" :key="p.id"
                      class="flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2 last:border-b-0 hover:bg-panel2"
                      :class="selectedRepos.some(x => x.id === p.id) ? 'bg-accent/10' : ''"
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
                      <span
                        class="shrink-0 w-5 text-center text-sm font-bold"
                        :class="selectedRepos.some(x => x.id === p.id) ? 'text-accent' : 'text-transparent'"
                      >✓</span>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 方式二：手动输入 -->
              <template v-else>
                <div class="col-span-2">
                  <RepoListField
                    v-model="form.git_repos"
                    label="Git 仓库（白盒源码扫描，可绑定多个）"
                    hint="同一项目的全部仓库会在一次扫描中一起分析。每个仓库可单独填写专属访问令牌；留空时按域名自动匹配「设置」中的个人 Git 服务凭据，公开仓库无需填写。"
                  />
                </div>
              </template>
            </template>

            <div class="col-span-2">
              <TargetListField
                v-model="form.default_test_targets"
                label="默认黑盒测试地址（可选，需内网测试环境；可添加多个并注明作用）"
                hint="发起扫描任务时会预填这些地址，提交任务时可再增删。"
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
