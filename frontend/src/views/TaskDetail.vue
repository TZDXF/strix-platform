<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api, getToken } from '../api'

const props = defineProps({ taskId: String })
const task = ref(null)
const logText = ref('')
const error = ref('')
const expanded = ref({})
let timer = null

const running = computed(() => ['pending', 'fetching', 'scanning', 'parsing'].includes(task.value?.status))

async function refresh() {
  try {
    const [t, l] = await Promise.all([api.getTask(props.taskId), api.getLog(props.taskId)])
    task.value = t
    logText.value = l.log
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
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
function artifactsUrl() {
  const base = '/api/tasks/' + props.taskId + '/artifacts'
  return base + '?t=' + getToken()
}
// 文件下载需带令牌头，fetch 方式触发保存
async function download() {
  const resp = await fetch('/api/tasks/' + props.taskId + '/artifacts', {
    headers: { 'X-Api-Token': getToken() },
  })
  if (!resp.ok) { error.value = '产物下载失败'; return }
  const blob = await resp.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = props.taskId + '-artifacts.zip'
  a.click()
  URL.revokeObjectURL(a.href)
}

onMounted(() => {
  refresh()
  timer = setInterval(() => { if (running.value) refresh() }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <a class="back" href="#/">&larr; 返回任务列表</a>
    <div class="err card" v-if="error">{{ error }}</div>

    <template v-if="task">
      <div class="card">
        <h3>任务 {{ task.id.slice(0, 12) }}…
          <span class="badge" :class="'st-' + task.status" style="margin-left: 10px">{{ task.status }}</span>
          <span v-if="task.timed_out" class="badge st-failed" style="margin-left: 6px">超时终止</span>
        </h3>
        <div class="meta-grid">
          <div class="meta-item"><div class="k">扫描模式</div><div class="v">{{ task.scan_mode }}</div></div>
          <div class="meta-item"><div class="k">源码</div><div class="v" style="font-size:12.5px; word-break: break-all">{{ task.source_ref || '-' }}</div></div>
          <div class="meta-item"><div class="k">黑盒地址</div><div class="v" style="font-size:12.5px">{{ task.test_url || '（仅白盒）' }}</div></div>
          <div class="meta-item"><div class="k">模型</div><div class="v">{{ task.model || '-' }}</div></div>
          <div class="meta-item"><div class="k">引擎</div><div class="v">strix {{ task.strix_version }}</div></div>
          <div class="meta-item"><div class="k">耗时</div><div class="v">{{ fmtDur(task.duration_sec) }}</div></div>
          <div class="meta-item"><div class="k">tokens</div><div class="v">{{ task.total_tokens != null ? task.total_tokens.toLocaleString() : '-' }}</div></div>
          <div class="meta-item"><div class="k">重试次数</div><div class="v">{{ task.attempts || 0 }}</div></div>
          <div class="meta-item"><div class="k">提交 / 完成</div><div class="v" style="font-size:12px">{{ fmtTime(task.created_at) }}<br />{{ fmtTime(task.finished_at) }}</div></div>
        </div>
        <div class="toolbar" v-if="task.has_artifacts">
          <span class="hint">产物包含：漏洞 JSON/CSV/MD、SARIF、完整执行日志</span>
          <div class="grow"></div>
          <button @click="download">下载产物 zip</button>
        </div>
        <div class="err" v-if="task.error" style="margin-top: 12px">{{ task.error }}</div>
      </div>

      <div class="card">
        <h3>漏洞发现（{{ task.findings.length }} 条）<span class="hint">AI 辅助测试结果，建议人工复核</span></h3>
        <div v-if="task.findings.length === 0 && task.status === 'done'" class="hint">本次扫描未发现漏洞（退出码 {{ task.exit_code }}）。</div>
        <div class="finding" v-for="f in task.findings" :key="f.id">
          <div class="finding-head" @click="expanded[f.id] = !expanded[f.id]">
            <span class="badge" :class="f.severity">{{ f.severity.toUpperCase() }}</span>
            <span class="title">{{ f.title }}</span>
            <span class="hint">CVSS {{ f.cvss ?? '-' }} · {{ f.cwe || '-' }} · {{ f.has_poc ? '有 PoC' : '无 PoC' }}</span>
            <span class="hint">{{ expanded[f.id] ? '收起 ▲' : '展开 ▼' }}</span>
          </div>
          <div class="finding-body" v-if="expanded[f.id]">
            <h4>端点 / 目标</h4>
            <div>{{ f.endpoint || '-' }}</div>
            <template v-if="f.description">
              <h4>描述</h4>
              <div style="white-space: pre-wrap">{{ f.description }}</div>
            </template>
            <template v-if="f.remediation">
              <h4>修复建议</h4>
              <div style="white-space: pre-wrap">{{ f.remediation }}</div>
            </template>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>执行日志</h3>
        <pre class="log">{{ logText || '（暂无）' }}</pre>
      </div>
    </template>
  </div>
</template>
