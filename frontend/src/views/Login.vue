<script setup lang="ts">
import { ref } from 'vue'
import { api, setSession, type User } from '../api'
import { btn, input, label } from '../ui'
import { toast } from '../toast'

const emit = defineEmits<{ 'logged-in': [user: User] }>()
const username = ref('')
const password = ref('')
const busy = ref(false)

async function submit() {
  if (!username.value.trim() || !password.value) {
    toast.error('请输入用户名和密码')
    return
  }
  busy.value = true
  try {
    const res = await api.login(username.value.trim(), password.value)
    setSession(res.token, res.user)
    emit('logged-in', res.user)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex justify-center pt-[10vh]">
    <form class="w-[360px] rounded-[10px] border border-border bg-panel p-7" @submit.prevent="submit">
      <h3 class="mb-3.5 text-base font-semibold text-text">Strix 内部安全测试平台</h3>
      <label :class="label">用户名</label>
      <input v-model="username" type="text" autocomplete="username" placeholder="用户名" :class="input" />
      <label :class="[label, 'mt-3']">密码</label>
      <input v-model="password" type="password" autocomplete="current-password" placeholder="密码" :class="input" />
      <button :class="[btn, 'mt-4.5 w-full']" :disabled="busy">{{ busy ? '登录中…' : '登 录' }}</button>
    </form>
  </div>
</template>
