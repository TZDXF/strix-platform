<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle,
} from 'reka-ui'
import { api, updateStoredUser, type User, type GitConfig } from '../api'
import { toast } from '../toast'
import { badge, btn, btnDanger, btnGhost, card, hint, h3, input, label, tableTd, tableTh } from '../ui'

const props = defineProps<{ user: User | null }>()
const me = ref<User | null>(props.user)
const changing = ref(false)
const pwd = ref({ old: '', neo: '', confirm: '' })

const savingKey = ref(false)
const apiKey = ref('')
const showKeyInput = ref(false)

const emailInput = ref('')
const showEmailInput = ref(false)
const savingEmail = ref(false)

async function refreshMe() {
  try {
    me.value = await api.me()
    updateStoredUser(me.value)
    if (!me.value.has_llm_key) showKeyInput.value = true
  } catch { /* 保持本地缓存 */ }
}

async function changePassword() {
  if (pwd.value.neo.length < 8) { toast.error('新密码至少 8 位'); return }
  if (pwd.value.neo !== pwd.value.confirm) { toast.error('两次输入的新密码不一致'); return }
  changing.value = true
  try {
    await api.changePassword(pwd.value.old, pwd.value.neo)
    pwd.value = { old: '', neo: '', confirm: '' }
    toast.success('密码已修改，下次登录请使用新密码。')
  } catch (e) { toast.error((e as Error).message) } finally { changing.value = false }
}

async function saveKey() {
  const key = apiKey.value.trim()
  if (!key) { toast.error('密钥不能为空'); return }
  savingKey.value = true
  try {
    me.value = await api.setLlmKey(key)
    updateStoredUser(me.value)
    apiKey.value = ''
    showKeyInput.value = false
    toast.success('AI 密钥已保存。')
  } catch (e) { toast.error((e as Error).message) } finally { savingKey.value = false }
}

async function removeKey() {
  if (!confirm('确定清除个人 AI 密钥？清除后你将无法提交新的扫描任务。')) return
  try {
    me.value = await api.clearLlmKey()
    updateStoredUser(me.value)
    showKeyInput.value = true
    toast.success('AI 密钥已清除。')
  } catch (e) { toast.error((e as Error).message) }
}

async function saveEmail() {
  const email = emailInput.value.trim()
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { toast.error('邮箱格式不正确'); return }
  savingEmail.value = true
  try {
    me.value = await api.setEmail(email)
    updateStoredUser(me.value)
    emailInput.value = ''
    showEmailInput.value = false
    toast.success(email ? '通知邮箱已保存，任务结束时将收到提醒邮件。' : '通知邮箱已清除。')
  } catch (e) { toast.error((e as Error).message) } finally { savingEmail.value = false }
}

async function clearEmail() {
  if (!confirm('确定清除通知邮箱？清除后任务结束将不再发送提醒邮件。')) return
  try {
    me.value = await api.setEmail('')
    updateStoredUser(me.value)
    toast.success('通知邮箱已清除。')
  } catch (e) { toast.error((e as Error).message) }
}

function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

// ---- 个人 Git 配置（GitLab）----
const gitConfigs = ref<GitConfig[]>([])
const gitForm = ref({ name: '', base_url: '', token: '' })
const savingGit = ref(false)
const gitDialogOpen = ref(false)
const editingGit = ref<GitConfig | null>(null) // null = 新增

function openGitCreate() {
  editingGit.value = null
  gitForm.value = { name: '', base_url: '', token: '' }
  gitDialogOpen.value = true
}

function openGitEdit(c: GitConfig) {
  editingGit.value = c
  gitForm.value = { name: c.name, base_url: c.base_url, token: '' }
  gitDialogOpen.value = true
}

async function loadGitConfigs() {
  try {
    gitConfigs.value = (await api.listGitConfigs()).items
    if (!gitConfigs.value.length) openGitCreate()
  } catch (e) { toast.error((e as Error).message) }
}

async function saveGitConfig() {
  if (!gitForm.value.base_url.trim()) { toast.error('请填写 Git 服务地址'); return }
  if (!editingGit.value && !gitForm.value.token.trim()) { toast.error('请填写访问令牌'); return }
  savingGit.value = true
  try {
    if (editingGit.value) {
      await api.updateGitConfig(editingGit.value.id, {
        name: gitForm.value.name.trim(),
        base_url: gitForm.value.base_url.trim(),
        token: gitForm.value.token.trim(),
      })
      toast.success('Git 配置已更新。')
    } else {
      await api.createGitConfig({
        name: gitForm.value.name.trim(),
        base_url: gitForm.value.base_url.trim(),
        token: gitForm.value.token.trim(),
      })
      toast.success('Git 配置已保存（令牌验证通过）。')
    }
    gitDialogOpen.value = false
    await loadGitConfigs()
  } catch (e) { toast.error((e as Error).message) } finally { savingGit.value = false }
}

async function removeGitConfig(c: GitConfig) {
  if (!confirm(`确定删除 Git 配置「${c.name}」？已创建项目的凭据不受影响。`)) return
  try {
    await api.deleteGitConfig(c.id)
    toast.success('Git 配置已删除。')
    await loadGitConfigs()
  } catch (e) { toast.error((e as Error).message) }
}

onMounted(() => {
  refreshMe()
  loadGitConfigs()
})
</script>

<template>
  <div class="grid gap-4">
    <!-- 账号信息 -->
    <div :class="card">
      <h3 :class="h3">个人信息</h3>
      <div class="grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))">
        <div class="rounded-lg bg-panel2 px-3 py-2.5">
          <div class="text-xs text-muted">用户名</div>
          <div class="mt-0.5 text-[13.5px] font-semibold">{{ me?.username || '-' }}</div>
        </div>
        <div class="rounded-lg bg-panel2 px-3 py-2.5">
          <div class="text-xs text-muted">角色</div>
          <div class="mt-0.5 text-[13.5px] font-semibold">{{ me?.role === 'admin' ? '超管' : '用户' }}</div>
        </div>
        <div class="rounded-lg bg-panel2 px-3 py-2.5">
          <div class="text-xs text-muted">创建时间</div>
          <div class="mt-0.5 text-[13.5px] font-semibold">{{ fmtTime(me?.created_at || null) }}</div>
        </div>
        <div class="rounded-lg bg-panel2 px-3 py-2.5">
          <div class="text-xs text-muted">最近登录</div>
          <div class="mt-0.5 text-[13.5px] font-semibold">{{ fmtTime(me?.last_login_at || null) }}</div>
        </div>
      </div>
    </div>

    <!-- 修改密码 -->
    <div :class="card">
      <h3 :class="h3">修改密码</h3>
      <div class="grid max-w-[520px] gap-3.5">
        <div>
          <label :class="label">原密码</label>
          <input v-model="pwd.old" type="password" placeholder="当前密码" :class="input" />
        </div>
        <div>
          <label :class="label">新密码（至少 8 位）</label>
          <input v-model="pwd.neo" type="password" placeholder="新密码" :class="input" />
        </div>
        <div>
          <label :class="label">确认新密码</label>
          <input v-model="pwd.confirm" type="password" placeholder="再输入一次新密码" :class="input" @keyup.enter="changePassword" />
        </div>
      </div>
      <div class="mt-3.5">
        <button :class="btn" :disabled="changing" @click="changePassword">{{ changing ? '提交中…' : '修改密码' }}</button>
      </div>
    </div>

    <!-- AI 密钥 -->
    <div :class="card">
      <h3 :class="h3">AI 密钥（LLM 网关）</h3>
      <div class="mt-2 flex items-center gap-2.5">
        <span class="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold"
          :class="me?.has_llm_key ? 'bg-ok/15 text-ok' : 'bg-border/30 text-muted'">
          {{ me?.has_llm_key ? '已配置' : '未配置' }}
        </span>
        <div class="flex-1"></div>
        <template v-if="me?.has_llm_key">
          <button :class="btnGhost" @click="showKeyInput = !showKeyInput">{{ showKeyInput ? '取消' : '更换密钥' }}</button>
          <button :class="btnDanger" @click="removeKey">清除密钥</button>
        </template>
      </div>
      <div v-if="showKeyInput" class="mt-3 grid max-w-[520px] gap-3.5">
        <div>
          <label :class="label">API Key</label>
          <input
            v-model="apiKey" type="password" placeholder="sk-…"
            class="w-full rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent"
            @keyup.enter="saveKey"
          />
        </div>
        <div><button :class="btn" :disabled="savingKey" @click="saveKey">{{ savingKey ? '保存中…' : '保存密钥' }}</button></div>
      </div>
    </div>

    <!-- 通知邮箱 -->
    <div :class="card">
      <h3 :class="h3">通知邮箱（任务提醒）</h3>
      <div class="mt-2 flex items-center gap-2.5">
        <span :class="badge + (me?.email ? ' bg-ok/15 text-ok' : ' bg-border/30 text-muted')">
          {{ me?.email || '未设置' }}
        </span>
        <div class="flex-1"></div>
        <template v-if="me?.email">
          <button :class="btnGhost" @click="showEmailInput = !showEmailInput">{{ showEmailInput ? '取消' : '更换邮箱' }}</button>
          <button :class="btnDanger" @click="clearEmail">清除邮箱</button>
        </template>
        <button v-else :class="btnGhost" @click="showEmailInput = true">设置邮箱</button>
      </div>
      <p :class="hint">扫描任务完成或失败时，平台将向该邮箱发送提醒邮件；需管理员先在「系统设置」中配置邮件服务。</p>
      <div v-if="showEmailInput" class="mt-3 grid max-w-[520px] gap-3.5">
        <div>
          <label :class="label">通知邮箱</label>
          <input v-model="emailInput" type="email" placeholder="you@example.com" :class="input" @keyup.enter="saveEmail" />
        </div>
        <div>
          <button :class="btn" :disabled="savingEmail" @click="saveEmail">{{ savingEmail ? '保存中…' : '保存邮箱' }}</button>
        </div>
      </div>
    </div>

    <!-- 个人 Git 配置（GitLab） -->
    <div :class="card">
      <h3 :class="h3">Git 配置（GitLab）</h3>
      <div class="mt-2 flex items-center gap-2.5">
        <span :class="badge + (gitConfigs.length ? ' bg-ok/15 text-ok' : ' bg-border/30 text-muted')">
          {{ gitConfigs.length ? `已配置 ${gitConfigs.length} 个` : '未配置' }}
        </span>
        <div class="flex-1"></div>
        <button :class="btnGhost" @click="openGitCreate">添加配置</button>
      </div>
      <div v-if="gitConfigs.length" class="mt-3.5">
        <table class="w-full border-collapse">
          <thead>
            <tr>
              <th :class="tableTh">名称</th>
              <th :class="tableTh">服务地址</th>
              <th :class="tableTh">添加时间</th>
              <th :class="tableTh"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in gitConfigs" :key="c.id">
              <td :class="tableTd + ' font-semibold'">{{ c.name }}</td>
              <td :class="tableTd"><span class="font-mono text-[12.5px]">{{ c.base_url }}</span></td>
              <td :class="tableTd + ' text-muted'">{{ fmtTime(c.created_at) }}</td>
              <td :class="tableTd + ' text-right'">
                <div class="inline-flex items-center justify-end gap-1.5">
                  <button :class="btnGhost + ' !px-3 !py-1 !text-xs'" @click="openGitEdit(c)">编辑</button>
                  <button :class="btnDanger + ' !px-3 !py-1 !text-xs'" @click="removeGitConfig(c)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Git 配置 新增/编辑 弹窗 -->
    <DialogRoot v-model:open="gitDialogOpen">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
        <DialogContent class="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[560px] max-w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[10px] border border-border bg-panel p-5">
          <DialogTitle class="mb-4 text-base font-semibold text-text">{{ editingGit ? '编辑 Git 配置' : '添加 Git 配置' }}</DialogTitle>
          <div class="grid gap-3.5">
            <div>
              <label :class="label">配置名称（可选，默认取令牌用户名）</label>
              <input v-model="gitForm.name" type="text" placeholder="内网 GitLab" :class="input" />
            </div>
            <div>
              <label :class="label">Git 服务地址</label>
              <input v-model="gitForm.base_url" type="text" placeholder="http://192.168.1.3:12580" :class="input" />
            </div>
            <div>
              <label :class="label">访问令牌（Personal Access Token）</label>
              <input
                v-model="gitForm.token" type="password"
                :placeholder="editingGit ? '不修改请留空' : 'glpat-xxxx'"
                :class="input" @keyup.enter="saveGitConfig"
              />
              <div v-if="editingGit" class="mt-1 text-xs text-muted">留空表示继续使用已保存的令牌；修改地址或令牌时会重新验证。</div>
            </div>
            <div class="mt-1 flex items-center justify-end gap-2.5">
              <button :class="btnGhost" :disabled="savingGit" @click="gitDialogOpen = false">取消</button>
              <button :class="btn" :disabled="savingGit" @click="saveGitConfig">
                {{ savingGit ? '验证并保存中…' : (editingGit ? '保存修改' : '保存配置') }}
              </button>
            </div>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>
