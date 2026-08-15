<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import Login from './views/Login.vue'
import Projects from './views/Projects.vue'
import ProjectDetail from './views/ProjectDetail.vue'
import TaskList from './views/TaskList.vue'
import TaskDetail from './views/TaskDetail.vue'
import Users from './views/Users.vue'
import { getToken, getUser, clearSession, type User } from './api'

const route = ref(location.hash || '#/')
const user = ref<User | null>(getUser())
const token = ref(getToken())

function onHash() { route.value = location.hash || '#/' }
window.addEventListener('hashchange', onHash)

function logout() {
  clearSession()
  token.value = ''
  user.value = null
  location.hash = '#/login'
}

function onLogin(logged: User) {
  token.value = getToken()
  user.value = logged
  location.hash = '#/projects'
}

interface View { name: string; id?: string }

const view = computed<View>(() => {
  const r = route.value
  let m: RegExpMatchArray | null
  if ((m = r.match(/^#\/task\/([a-f0-9]+)/))) return { name: 'task-detail', id: m[1] }
  if (r.startsWith('#/tasks')) return { name: 'task-list' }
  if ((m = r.match(/^#\/project\/([a-f0-9]+)/))) return { name: 'project-detail', id: m[1] }
  if (r.startsWith('#/projects')) return { name: 'projects' }
  if (r.startsWith('#/users')) return { name: 'users' }
  return { name: token.value ? 'projects' : 'login' }
})

onMounted(() => {
  if (!token.value && route.value !== '#/login') location.hash = '#/login'
  else if (token.value && (route.value === '#/' || route.value === '#/login')) location.hash = '#/projects'
})
onUnmounted(() => window.removeEventListener('hashchange', onHash))
</script>

<template>
  <div class="mx-auto max-w-[1200px] px-5 pb-15">
    <header
      v-if="token && view.name !== 'login'"
      class="mb-5.5 flex items-center gap-3.5 border-b border-border py-3.5"
    >
      <div class="text-[17px] font-bold tracking-wide">
        <span class="text-accent">Strix</span> 内部安全测试平台
      </div>
      <nav class="ml-4.5 flex gap-1">
        <a
          href="#/projects"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'projects' || view.name === 'project-detail'
            ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >项目</a>
        <a
          href="#/tasks"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'task-list' || view.name === 'task-detail'
            ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >任务</a>
        <a
          v-if="user?.role === 'admin'"
          href="#/users"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'users' ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >用户管理</a>
      </nav>
      <div class="flex-1"></div>
      <span v-if="user" class="text-xs text-muted">
        {{ user.display_name }}（{{ user.role === 'admin' ? '超管' : '用户' }}）
      </span>
      <button
        class="cursor-pointer rounded-md border border-border bg-transparent px-5 py-2 text-sm font-semibold text-text hover:border-accent"
        @click="logout"
      >退出</button>
    </header>

    <Login v-if="view.name === 'login'" @logged-in="onLogin" />
    <Projects v-else-if="view.name === 'projects'" :user="user" />
    <ProjectDetail v-else-if="view.name === 'project-detail'" :project-id="view.id!" :user="user" />
    <TaskList v-else-if="view.name === 'task-list'" :user="user" />
    <TaskDetail v-else-if="view.name === 'task-detail'" :task-id="view.id!" />
    <Users v-else-if="view.name === 'users'" />
  </div>
</template>
