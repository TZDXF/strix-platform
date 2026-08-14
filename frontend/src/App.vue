<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import TaskList from './views/TaskList.vue'
import TaskDetail from './views/TaskDetail.vue'
import { getToken, setToken } from './api'

const route = ref(location.hash || '#/')
const token = ref(getToken())
const tokenInput = ref(getToken())

function onHash() { route.value = location.hash || '#/' }
window.addEventListener('hashchange', onHash)

function saveToken() {
  setToken(tokenInput.value.trim())
  token.value = tokenInput.value.trim()
}

onMounted(() => onHash())
onUnmounted(() => window.removeEventListener('hashchange', onHash))

function currentView() {
  const m = route.value.match(/^#\/task\/([a-f0-9]+)/)
  if (m) return { name: 'detail', id: m[1] }
  return { name: 'list' }
}
</script>

<template>
  <div class="container">
    <header>
      <div class="logo"><span>Strix</span> 内部安全测试平台</div>
      <div class="spacer"></div>
      <input v-model="tokenInput" type="text" placeholder="访问令牌" @change="saveToken" />
    </header>

    <TaskList v-if="currentView().name === 'list' && token" />
    <TaskDetail v-else-if="currentView().name === 'detail' && token" :task-id="currentView().id" />
    <div v-else class="card">
      <h3>请先在右上角填写访问令牌</h3>
      <p class="hint">令牌由平台管理员提供（服务端环境变量 API_TOKEN）。</p>
    </div>
  </div>
</template>
