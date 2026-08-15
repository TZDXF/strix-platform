<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
} from 'reka-ui'
import { api, type User } from '../api'
import {
  btn, btnGhost, card, err as errCls, hint, h3, input, label, tableTd, tableTh,
} from '../ui'

const users = ref<User[]>([])
const error = ref('')
const showCreate = ref(false)
const form = ref({ username: '', password: '', role: 'user', display_name: '' })
const creating = ref(false)
const resetOpen = ref(false)
const resetFor = ref<User | null>(null)
const resetPwd = ref('')
const resetInfo = ref('')

async function refresh() {
  try {
    users.value = (await api.listUsers()).items
    error.value = ''
  } catch (e) { error.value = (e as Error).message }
}

async function create() {
  error.value = ''
  creating.value = true
  try {
    await api.createUser(form.value)
    showCreate.value = false
    form.value = { username: '', password: '', role: 'user', display_name: '' }
    refresh()
  } catch (e) { error.value = (e as Error).message } finally { creating.value = false }
}

async function toggleActive(u: User) {
  error.value = ''
  try {
    await api.patchUser(u.id, { is_active: !u.is_active })
    refresh()
  } catch (e) { error.value = (e as Error).message }
}

async function changeRole(u: User) {
  error.value = ''
  try {
    await api.patchUser(u.id, { role: u.role === 'admin' ? 'user' : 'admin' })
    refresh()
  } catch (e) { error.value = (e as Error).message }
}

async function removeUser(u: User) {
  if (!confirm(`确定删除用户 ${u.username}？其创建的项目与任务将保留归属。`)) return
  error.value = ''
  try { await api.deleteUser(u.id); refresh() } catch (e) { error.value = (e as Error).message }
}

function openReset(u: User) {
  resetFor.value = u
  resetPwd.value = ''
  resetInfo.value = ''
  resetOpen.value = true
}

async function saveReset() {
  resetInfo.value = ''
  if (resetPwd.value.length < 8) { resetInfo.value = '密码至少 8 位'; return }
  if (!resetFor.value) return
  try {
    await api.patchUser(resetFor.value.id, { password: resetPwd.value })
    resetOpen.value = false
  } catch (e) { resetInfo.value = (e as Error).message }
}

function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

onMounted(refresh)
</script>

<template>
  <div :class="card">
    <div class="mb-3.5 flex items-center gap-2.5">
      <h3 :class="[h3, 'mb-0']">用户管理（仅超管可见）</h3>
      <div class="flex-1"></div>
      <button :class="btn" @click="showCreate = !showCreate">{{ showCreate ? '收起' : '创建用户' }}</button>
    </div>

    <div v-if="showCreate" class="mb-4 rounded-lg border border-border bg-panel2 p-3.5">
      <div class="grid grid-cols-2 gap-3.5">
        <div>
          <label :class="label">用户名（2-64 位字母/数字/_.-）</label>
          <input v-model="form.username" type="text" placeholder="zhangsan" :class="input" />
        </div>
        <div>
          <label :class="label">初始密码（至少 8 位）</label>
          <input v-model="form.password" type="text" placeholder="至少 8 位" :class="input" />
        </div>
        <div>
          <label :class="label">角色</label>
          <select v-model="form.role" :class="input">
            <option value="user">普通用户</option>
            <option value="admin">超管</option>
          </select>
        </div>
        <div>
          <label :class="label">显示名（可选）</label>
          <input v-model="form.display_name" type="text" placeholder="张三" :class="input" />
        </div>
      </div>
      <div class="mt-3.5 flex items-center gap-2.5">
        <span :class="hint">账号只能由超管创建，创建后请线下把密码交给使用者。</span>
        <div class="flex-1"></div>
        <button :class="btn" :disabled="creating" @click="create">{{ creating ? '创建中…' : '创建' }}</button>
      </div>
    </div>

    <div v-if="error" :class="errCls">{{ error }}</div>

    <table v-if="users.length" class="w-full border-collapse">
      <thead>
        <tr>
          <th :class="tableTh">ID</th><th :class="tableTh">用户名</th><th :class="tableTh">显示名</th>
          <th :class="tableTh">角色</th><th :class="tableTh">状态</th><th :class="tableTh">创建时间</th>
          <th :class="tableTh">最近登录</th><th :class="tableTh">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="u in users" :key="u.id" class="hover:bg-accent/5">
          <td :class="tableTd">{{ u.id }}</td>
          <td :class="[tableTd, 'font-semibold']">{{ u.username }}</td>
          <td :class="tableTd">{{ u.display_name || '-' }}</td>
          <td :class="tableTd">
            <span class="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold"
              :class="u.role === 'admin' ? 'bg-crit/15 text-crit' : 'bg-border/30 text-muted'">
              {{ u.role === 'admin' ? '超管' : '用户' }}
            </span>
          </td>
          <td :class="tableTd">
            <span class="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold"
              :class="u.is_active ? 'bg-ok/15 text-ok' : 'bg-crit/15 text-crit'">
              {{ u.is_active ? '启用' : '停用' }}
            </span>
          </td>
          <td :class="tableTd">{{ fmtTime(u.created_at) }}</td>
          <td :class="tableTd">{{ fmtTime(u.last_login_at) }}</td>
          <td :class="[tableTd, 'space-x-2.5']">
            <a href="javascript:void(0)" class="text-accent" @click="openReset(u)">重置密码</a>
            <a href="javascript:void(0)" class="text-accent" @click="changeRole(u)">{{ u.role === 'admin' ? '降为用户' : '升为超管' }}</a>
            <a href="javascript:void(0)" class="text-accent" @click="toggleActive(u)">{{ u.is_active ? '停用' : '启用' }}</a>
            <a href="javascript:void(0)" class="text-crit" @click="removeUser(u)">删除</a>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else :class="hint">暂无用户。</p>

    <!-- 重置密码弹窗（reka-ui Dialog） -->
    <DialogRoot v-model:open="resetOpen">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
        <DialogContent
          class="fixed top-1/2 left-1/2 z-50 w-[380px] -translate-x-1/2 -translate-y-1/2 rounded-[10px] border border-border bg-panel p-[18px]"
        >
          <DialogTitle class="mb-3.5 text-sm font-semibold text-muted">重置密码：{{ resetFor?.username }}</DialogTitle>
          <label :class="label">新密码（至少 8 位）</label>
          <input v-model="resetPwd" type="text" placeholder="新密码" :class="input" />
          <div v-if="resetInfo" :class="errCls">{{ resetInfo }}</div>
          <div class="mt-3.5 flex items-center gap-2.5">
            <div class="flex-1"></div>
            <button :class="btnGhost" @click="resetOpen = false">取消</button>
            <button :class="btn" @click="saveReset">保存</button>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>
