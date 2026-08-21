<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import Login from './views/Login.vue'
import Welcome from './views/Welcome.vue'
import Projects from './views/Projects.vue'
import ProjectDetail from './views/ProjectDetail.vue'
import TaskList from './views/TaskList.vue'
import TaskDetail from './views/TaskDetail.vue'
import Users from './views/Users.vue'
import Settings from './views/Settings.vue'
import AdminSettings from './views/AdminSettings.vue'
import Stats from './views/Stats.vue'
import ToastHost from './components/ToastHost.vue'
import { getToken, getUser, clearSession, type User } from './api'
import { type ThemePref, getThemePref, setThemePref, cycleThemePref, themeLabels } from './theme'

const route = ref(location.hash || '#/')
const user = ref<User | null>(getUser())
const token = ref(getToken())
const themePref = ref<ThemePref>(getThemePref())

function toggleTheme() {
  themePref.value = cycleThemePref(themePref.value)
  setThemePref(themePref.value)
}

function onHash() { route.value = location.hash || '#/' }
window.addEventListener('hashchange', onHash)

function logout() {
  clearSession()
  token.value = ''
  user.value = null
  location.hash = '#/login'
}

function onSessionExpired() {
  token.value = ''
  user.value = null
}
window.addEventListener('session-expired', onSessionExpired)

function onLogin(logged: User) {
  token.value = getToken()
  user.value = logged
  location.hash = '#/welcome'
}

interface View { name: string; id?: string }

const view = computed<View>(() => {
  const r = route.value
  let m: RegExpMatchArray | null
  if ((m = r.match(/^#\/task\/([a-f0-9]+)/))) return { name: 'task-detail', id: m[1] }
  if (r.startsWith('#/tasks')) return { name: 'task-list' }
  if (r.startsWith('#/stats')) return { name: 'stats' }
  if ((m = r.match(/^#\/project\/([a-f0-9]+)/))) return { name: 'project-detail', id: m[1] }
  if (r.startsWith('#/projects')) return { name: 'projects' }
  if (r.startsWith('#/welcome')) return { name: 'welcome' }
  if (r.startsWith('#/admin-settings')) return { name: 'admin-settings' }
  if (r.startsWith('#/settings')) return { name: 'settings' }
  if (r.startsWith('#/users')) return { name: 'users' }
  return { name: token.value ? 'welcome' : 'login' }
})

onMounted(() => {
  if (!token.value && route.value !== '#/login') location.hash = '#/login'
  else if (token.value && (route.value === '#/' || route.value === '#/login')) location.hash = '#/welcome'
})
onUnmounted(() => {
  window.removeEventListener('hashchange', onHash)
  window.removeEventListener('session-expired', onSessionExpired)
})
</script>

<template>
  <div class="mx-auto max-w-[1200px] px-5 pb-15">
    <header
      v-if="token && view.name !== 'login'"
      class="sticky top-0 z-40 -mx-5 mb-5.5 flex items-center gap-3.5 border-b border-border bg-bg/90 px-5 py-3.5 backdrop-blur"
    >
      <div class="text-[17px] font-bold tracking-wide">
        <span class="text-accent">Strix</span> 内部安全测试平台
      </div>
      <nav class="ml-4.5 flex gap-1">
        <a
          href="#/welcome"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'welcome' ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >首页</a>
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
          href="#/stats"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'stats'
            ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >统计汇总</a>
        <a
          v-if="user?.role === 'admin'"
          href="#/users"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'users' ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >用户管理</a>
        <a
          v-if="user?.role === 'admin'"
          href="#/admin-settings"
          class="cursor-pointer rounded-md px-3.5 py-1.5 font-semibold"
          :class="view.name === 'admin-settings' ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-panel2 hover:text-text'"
        >系统设置</a>
      </nav>
      <div class="flex-1"></div>
      <span v-if="user" class="text-xs text-muted">
        {{ user.display_name }}（{{ user.role === 'admin' ? '超管' : '用户' }}）
      </span>
      <a
        v-if="user"
        href="#/settings"
        class="cursor-pointer rounded-md border border-border bg-transparent px-3 py-2 text-xs font-semibold text-muted hover:border-accent hover:text-accent"
      >个人设置</a>
      <button
        class="cursor-pointer rounded-md border border-border bg-transparent px-3 py-2 text-xs font-semibold text-muted hover:border-accent hover:text-accent"
        :title="`当前：${themeLabels[themePref]}，点击切换`"
        @click="toggleTheme"
      >🎨 {{ themeLabels[themePref] }}</button>
      <button
        class="cursor-pointer rounded-md border border-border bg-transparent px-5 py-2 text-sm font-semibold text-text hover:border-accent"
        @click="logout"
      >退出</button>
    </header>

    <Login v-if="view.name === 'login'" @logged-in="onLogin" />
    <Welcome v-else-if="view.name === 'welcome'" :user="user" />
    <Projects v-else-if="view.name === 'projects'" :user="user" />
    <ProjectDetail v-else-if="view.name === 'project-detail'" :project-id="view.id!" :user="user" />
    <TaskList v-else-if="view.name === 'task-list'" :user="user" />
    <Stats v-else-if="view.name === 'stats'" />
    <TaskDetail v-else-if="view.name === 'task-detail'" :task-id="view.id!" />
    <Users v-else-if="view.name === 'users'" />
    <AdminSettings v-else-if="view.name === 'admin-settings'" />
    <Settings v-else-if="view.name === 'settings'" :user="user" />
    <ToastHost />
  </div>
</template>
