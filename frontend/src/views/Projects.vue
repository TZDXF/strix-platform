<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  SelectRoot, SelectTrigger, SelectValue, SelectPortal, SelectContent, SelectViewport, SelectItem, SelectItemText,
} from 'reka-ui'
import { api, type Project, type User } from '../api'
import { btn, card, err as errCls, hint, h3, input, label, tableTd, tableTh } from '../ui'

const props = defineProps<{ user: User | null }>()
const projects = ref<Project[]>([])
const error = ref('')
const showCreate = ref(false)
const creating = ref(false)

const emptyForm = {
  name: '', description: '', source_type: 'git',
  git_url: '', git_auth_type: '', git_token: '', git_ssh_key: '',
  default_test_url: '',
}
const form = ref({ ...emptyForm })

const authOptions = [
  { value: '', label: '无需凭据（公开仓库）' },
  { value: 'token', label: 'HTTPS Token' },
  { value: 'ssh', label: 'SSH 私钥' },
]

const typeOptions = [
  { value: 'git', label: 'Git 仓库' },
  { value: 'zip', label: '上传 zip 压缩包' },
]

const selectTrigger = 'flex w-full items-center justify-between rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent'
const selectItem = 'cursor-pointer rounded-md px-2.5 py-1.5 text-[13.5px] text-text data-highlighted:bg-accent/15 data-highlighted:outline-none'
const selectContent = 'z-50 max-h-72 min-w-[var(--reka-select-trigger-width)] overflow-hidden rounded-md border border-border bg-panel2 py-1 shadow-lg'

async function refresh() {
  try {
    projects.value = (await api.listProjects()).items
    error.value = ''
  } catch (e) { error.value = (e as Error).message }
}

async function create() {
  error.value = ''
  creating.value = true
  try {
    await api.createProject(form.value)
    showCreate.value = false
    form.value = { ...emptyForm }
    refresh()
  } catch (e) { error.value = (e as Error).message } finally { creating.value = false }
}

function open(id: string) { location.hash = `#/project/${id}` }
function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

onMounted(refresh)
</script>

<template>
  <div :class="card">
    <div class="mb-3.5 flex items-center gap-2.5">
      <h3 :class="[h3, 'mb-0']">项目（{{ props.user?.role === 'admin' ? '全部项目' : '我创建的项目' }}）</h3>
      <div class="flex-1"></div>
      <button :class="btn" @click="showCreate = !showCreate">{{ showCreate ? '收起' : '新建项目' }}</button>
    </div>

    <div v-if="showCreate" class="mb-4 rounded-lg border border-border bg-panel2 p-3.5">
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
        <div v-if="form.source_type === 'git'" class="col-span-2">
          <label :class="label">Git 仓库地址（白盒源码扫描）</label>
          <input v-model="form.git_url" type="text" placeholder="https://git.company.internal/team/app-a.git" :class="input" />
        </div>
        <div v-if="form.source_type === 'git'">
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
        <div v-if="form.source_type === 'git' && form.git_auth_type === 'token'" class="col-span-2">
          <label :class="label">访问 Token（git token 或 `用户名:token`，保存后不再回显）</label>
          <input v-model="form.git_token" type="password" placeholder="glpat-xxxx / ghp_xxxx" :class="input" />
        </div>
        <div v-if="form.source_type === 'git' && form.git_auth_type === 'ssh'" class="col-span-2">
          <label :class="label">SSH 私钥（PEM，保存后不再回显）</label>
          <textarea v-model="form.git_ssh_key" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
            class="w-full rounded-md border border-border bg-panel2 px-2.5 py-2 font-mono text-[12.5px] text-text outline-none focus:border-accent" />
        </div>
        <div class="col-span-2">
          <label :class="label">默认黑盒测试地址（可选，需内网测试环境）</label>
          <input v-model="form.default_test_url" type="text" placeholder="https://app-a.test.company.internal" :class="input" />
        </div>
        <div class="col-span-2">
          <label :class="label">描述（可选）</label>
          <input v-model="form.description" type="text" placeholder="项目说明 / 负责人 / 测试范围" :class="input" />
        </div>
      </div>
      <div class="mt-3.5 flex items-center gap-2.5">
        <span :class="hint">创建后即可在项目内选择分支 / 上传代码并发起扫描任务。</span>
        <div class="flex-1"></div>
        <button :class="btn" :disabled="creating" @click="create">{{ creating ? '创建中…' : '创建项目' }}</button>
      </div>
    </div>

    <div v-if="error" :class="errCls">{{ error }}</div>

    <table v-if="projects.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">项目</th><th :class="tableTh">来源</th><th :class="tableTh">仓库 / 说明</th>
          <th :class="tableTh">凭据</th><th :class="tableTh">任务数</th><th :class="tableTh">创建人</th><th :class="tableTh">创建时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in projects" :key="p.id" class="cursor-pointer hover:bg-accent/5" @click="open(p.id)">
          <td :class="[tableTd, 'font-semibold']">{{ p.name }}</td>
          <td :class="tableTd">
            <span class="inline-block rounded-full bg-border/30 px-2.5 py-0.5 text-xs font-semibold text-muted">
              {{ p.source_type === 'git' ? 'Git' : 'zip 上传' }}
            </span>
          </td>
          <td :class="[tableTd, 'max-w-[320px] truncate']">
            {{ p.source_type === 'git' ? p.git_url : (p.description || '代码压缩包') }}
          </td>
          <td :class="tableTd">{{ p.source_type === 'git' ? (p.has_credentials ? '已配置' : '-') : '-' }}</td>
          <td :class="tableTd">{{ p.tasks_count }}</td>
          <td :class="tableTd">{{ p.created_by_name }}</td>
          <td :class="tableTd">{{ fmtTime(p.created_at) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else :class="hint">还没有项目，点击右上角「新建项目」开始。</p>
  </div>
</template>
