<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'

const tasks = ref([])
const error = ref('')
const submitting = ref(false)
const submitted = ref('')
let timer = null

const form = ref({
  sourceKind: 'git',
  gitUrl: '',
  file: null,
  fileName: '',
  scanMode: 'quick',
  testUrl: '',
  model: '',
})
const models = ref([])
const defaultModel = ref('')
const modelsError = ref('')

async function loadModels() {
  try {
    const data = await api.listModels()
    models.value = data.items || []
    defaultModel.value = data.default || 'free'
    modelsError.value = ''
  } catch (e) {
    models.value = []
    defaultModel.value = ''
    modelsError.value = e.message
  }
  if (!form.value.model) form.value.model = defaultModel.value || models.value[0] || 'free'
}

async function refresh() {
  try {
    const data = await api.listTasks()
    tasks.value = data.items
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

async function submit() {
  error.value = ''
  submitted.value = ''
  if (form.value.sourceKind === 'git' && !form.value.gitUrl.trim()) {
    error.value = '请填写代码仓库地址'
    return
  }
  if (form.value.sourceKind === 'zip' && !form.value.file) {
    error.value = '请选择 zip 压缩包'
    return
  }
  submitting.value = true
  try {
    const res = await api.submit({
      scanMode: form.value.scanMode,
      testUrl: form.value.testUrl.trim(),
      gitUrl: form.value.sourceKind === 'git' ? form.value.gitUrl.trim() : '',
      file: form.value.sourceKind === 'zip' ? form.value.file : null,
      model: form.value.model,
    })
    submitted.value = res.id
    form.value.gitUrl = ''
    form.value.file = null
    form.value.fileName = ''
    form.value.testUrl = ''
    refresh()
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

function onFile(e) {
  const f = e.target.files?.[0]
  if (f) { form.value.file = f; form.value.fileName = f.name }
}

function fmtDur(s) {
  if (s == null) return '-'
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m`
}
function fmtTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}
function sevCounts(t) {
  const c = t.severity_counts || {}
  return ['critical', 'high', 'medium', 'low', 'info']
    .filter(k => c[k])
    .map(k => `${k[0].toUpperCase()}:${c[k]}`)
    .join(' ') || '-'
}
function open(id) { location.hash = `#/task/${id}` }

onMounted(() => { refresh(); loadModels(); timer = setInterval(refresh, 5000) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="card">
    <h3>发布新扫描任务</h3>
    <div class="form-grid">
      <div>
        <label>源码来源</label>
        <select v-model="form.sourceKind">
          <option value="git">Git 仓库地址</option>
          <option value="zip">上传 zip 压缩包</option>
        </select>
      </div>
      <div>
        <label>扫描档位（quick/standard/deep 全开放）</label>
        <select v-model="form.scanMode">
          <option value="quick">quick（约 1 小时）</option>
          <option value="standard">standard（0.5~1 小时+）</option>
          <option value="deep">deep（1~4 小时+）</option>
        </select>
      </div>
      <div>
        <label>模型（网关 free 池，默认 {{ defaultModel || 'free' }}）</label>
        <select v-if="models.length" v-model="form.model">
          <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
        </select>
        <template v-else>
          <input v-model="form.model" type="text" placeholder="free" />
          <div class="hint">{{ modelsError || '未获取到模型列表，将使用平台默认模型' }}</div>
        </template>
      </div>
      <div class="full" v-if="form.sourceKind === 'git'">
        <label>代码仓库地址（白盒源码扫描，必填）</label>
        <input v-model="form.gitUrl" type="text" placeholder="https://git.company.internal/team/app-a.git" />
      </div>
      <div class="full" v-else>
        <label>zip 压缩包（≤500MB）</label>
        <input type="file" accept=".zip" @change="onFile" />
        <div class="hint" v-if="form.fileName">已选择：{{ form.fileName }}</div>
      </div>
      <div class="full">
        <label>黑盒测试地址（可选，需为内网测试环境；扫描会发送真实攻击流量）</label>
        <input v-model="form.testUrl" type="text" placeholder="https://app-a.test.company.internal" />
      </div>
    </div>
    <div class="toolbar">
      <span class="hint">提交即表示确认对该目标执行安全测试已获授权。</span>
      <div class="grow"></div>
      <button :disabled="submitting" @click="submit">{{ submitting ? '提交中…' : '发布任务' }}</button>
    </div>
    <div class="err" v-if="error">{{ error }}</div>
    <div class="hint" v-if="submitted" style="color: var(--ok); margin-top: 10px">
      任务已提交：{{ submitted }}，见下方列表。
    </div>
  </div>

  <div class="card">
    <h3>任务列表（每 5 秒自动刷新）</h3>
    <table v-if="tasks.length">
      <thead>
        <tr>
          <th>任务</th><th>状态</th><th>模式</th><th>模型</th><th>来源</th><th>发现</th>
          <th>耗时</th><th>tokens</th><th>提交时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.id" @click="open(t.id)">
          <td style="font-family: monospace">{{ t.id.slice(0, 10) }}…</td>
          <td><span class="badge" :class="'st-' + t.status">{{ t.status }}</span></td>
          <td>{{ t.scan_mode }}</td>
          <td>{{ t.model || '-' }}</td>
          <td style="max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ t.source_type === 'git' ? t.source_ref : t.source_ref }}
          </td>
          <td>{{ t.findings_count > 0 ? sevCounts(t) : (t.status === 'done' ? '无' : '-') }}</td>
          <td>{{ fmtDur(t.duration_sec) }}</td>
          <td>{{ t.total_tokens > 10000 ? Math.round(t.total_tokens / 10000) / 100 + 'M' : (t.total_tokens ?? '-') }}</td>
          <td>{{ fmtTime(t.created_at) }}</td>
        </tr>
      </tbody>
    </table>
    <p class="hint" v-else>暂无任务。</p>
  </div>
</template>
