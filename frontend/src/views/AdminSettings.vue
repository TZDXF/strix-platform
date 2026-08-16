<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api, type PlatformModel, type MailSettings } from '../api'
import { toast } from '../toast'
import { badge, btn, btnDanger, btnGhost, card, hint, h3, input, label, tableTd, tableTh } from '../ui'

function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

// ---- 平台模型管理 ----
const models = ref<PlatformModel[]>([])
const modelNames = computed(() => new Set(models.value.map((m) => m.name)))
const discoverKey = ref('')
const discovering = ref(false)
const discovered = ref<string[]>([])
const checked = ref<Record<string, boolean>>({})
const defaultPick = ref('')
const adding = ref(false)
const modelBusy = ref(false)

async function loadModels() {
  try {
    models.value = (await api.listPlatformModels()).items
  } catch (e) { toast.error((e as Error).message) }
}

async function discover() {
  const key = discoverKey.value.trim()
  if (!key) { toast.error('请先填写网关密钥'); return }
  discovering.value = true
  try {
    discovered.value = (await api.discoverModels(key)).items
    checked.value = {}
    defaultPick.value = ''
    if (discovered.value.length) toast.info(`查询到 ${discovered.value.length} 个可用模型，勾选后加入平台。`)
    else toast.info('网关未返回任何模型。')
  } catch (e) { toast.error((e as Error).message) } finally { discovering.value = false }
}

const checkedNames = computed(() => discovered.value.filter((n) => checked.value[n]))

async function addSelected() {
  const names = checkedNames.value
  if (!names.length) { toast.error('请先勾选要添加的模型'); return }
  adding.value = true
  try {
    const res = await api.addModels(names, defaultPick.value || undefined)
    models.value = res.items
    discovered.value = discovered.value.filter((n) => !res.items.some((m) => m.name === n))
    checked.value = {}
    defaultPick.value = ''
    toast.success(`已添加 ${names.length} 个模型。`)
  } catch (e) { toast.error((e as Error).message) } finally { adding.value = false }
}

async function setDefault(m: PlatformModel) {
  try {
    await api.setDefaultModel(m.id)
    await loadModels()
    toast.success(`已将「${m.name}」设为平台默认模型。`)
  } catch (e) { toast.error((e as Error).message) }
}

async function removeModel(m: PlatformModel) {
  if (!confirm(`确定从平台移除模型「${m.name}」？移除后用户将无法在选择该模型发起任务。`)) return
  modelBusy.value = true
  try {
    await api.deleteModel(m.id)
    await loadModels()
    toast.success(`已移除「${m.name}」。`)
  } catch (e) { toast.error((e as Error).message) } finally { modelBusy.value = false }
}

// ---- 邮件提醒（SMTP） ----
// 加密方式三选一：starttls（587 常见）/ ssl（465 常见）/ none（内网明文）
type TlsMode = 'starttls' | 'ssl' | 'none'
const mail = ref<MailSettings | null>(null)
const tlsMode = ref<TlsMode>('starttls')
const smtpPassword = ref('')
const savingMail = ref(false)
const testTo = ref('')
const testing = ref(false)

function applySettings(s: MailSettings) {
  mail.value = s
  tlsMode.value = s.smtp_ssl ? 'ssl' : (s.smtp_use_tls ? 'starttls' : 'none')
}

async function loadMail() {
  try {
    applySettings(await api.getMailSettings())
  } catch (e) { toast.error((e as Error).message) }
}

async function saveMail(silent = false): Promise<boolean> {
  const m = mail.value
  if (!m) return false
  if (m.smtp_host.trim() && !(1 <= m.smtp_port && m.smtp_port <= 65535)) {
    toast.error('SMTP 端口需在 1-65535 之间'); return false
  }
  savingMail.value = true
  try {
    applySettings(await api.saveMailSettings({
      smtp_host: m.smtp_host.trim(),
      smtp_port: m.smtp_port,
      smtp_user: m.smtp_user.trim(),
      smtp_password: smtpPassword.value,
      smtp_use_tls: tlsMode.value === 'starttls',
      smtp_ssl: tlsMode.value === 'ssl',
      mail_from: m.mail_from.trim(),
      mail_sender_name: m.mail_sender_name.trim(),
      site_url: m.site_url.trim(),
      notify_done: m.notify_done,
      notify_failed: m.notify_failed,
    }))
    smtpPassword.value = ''
    if (!silent) toast.success('邮件设置已保存。')
    return true
  } catch (e) {
    toast.error((e as Error).message)
    return false
  } finally { savingMail.value = false }
}

async function sendTest() {
  const to = testTo.value.trim()
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) { toast.error('请填写正确的收件邮箱'); return }
  testing.value = true
  try {
    if (!(await saveMail(true))) return // 测试前先保存当前表单，保证测的就是所见配置
    const res = await api.testMailSettings(to)
    toast.success(res.detail || '测试邮件已发送。')
  } catch (e) { toast.error((e as Error).message) } finally { testing.value = false }
}

onMounted(() => {
  loadModels()
  loadMail()
})
</script>

<template>
  <div class="grid gap-4">
    <!-- 邮件提醒设置 -->
    <div :class="card">
      <div class="flex items-center gap-2.5">
        <h3 :class="[h3, 'mb-0']">邮件提醒（SMTP）</h3>
        <span :class="badge + (mail?.configured ? ' bg-ok/15 text-ok' : ' bg-border/30 text-muted')">
          {{ mail?.configured ? '已配置' : '未配置' }}
        </span>
      </div>
      <p :class="hint">配置后，扫描任务完成/失败时平台会向用户在「个人设置」里填写的通知邮箱发送提醒。</p>

      <div v-if="mail" class="mt-3.5 grid gap-3.5" style="grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))">
        <div>
          <label :class="label">SMTP 服务器地址</label>
          <input v-model="mail.smtp_host" type="text" placeholder="smtp.example.com（留空则停用邮件提醒）" :class="input" />
        </div>
        <div>
          <label :class="label">端口</label>
          <input v-model.number="mail.smtp_port" type="number" placeholder="587" :class="input" />
        </div>
        <div>
          <label :class="label">加密方式</label>
          <select v-model="tlsMode" :class="input">
            <option value="starttls">STARTTLS（587 常用）</option>
            <option value="ssl">SSL/TLS 直连（465 常用）</option>
            <option value="none">不加密（内网中继）</option>
          </select>
        </div>
        <div>
          <label :class="label">SMTP 用户名</label>
          <input v-model="mail.smtp_user" type="text" placeholder="内网中继可留空" :class="input" />
        </div>
        <div>
          <label :class="label">SMTP 密码{{ mail.has_password ? '（已保存，不修改请留空）' : '' }}</label>
          <input v-model="smtpPassword" type="password" placeholder="留空 = 保持不变" :class="input" />
        </div>
        <div>
          <label :class="label">发件人邮箱（留空用 SMTP 用户名）</label>
          <input v-model="mail.mail_from" type="text" placeholder="strix@example.com" :class="input" />
        </div>
        <div>
          <label :class="label">发件人显示名（留空用「Strix 平台」）</label>
          <input v-model="mail.mail_sender_name" type="text" placeholder="Strix 安全测试平台" :class="input" />
        </div>
        <div>
          <label :class="label">平台访问地址（用于邮件内任务链接）</label>
          <input v-model="mail.site_url" type="text" placeholder="http://192.168.1.10:8080" :class="input" />
        </div>
      </div>

      <div v-if="mail" class="mt-3.5 flex flex-wrap items-center gap-5">
        <label class="flex cursor-pointer items-center gap-2 text-[13px]">
          <input v-model="mail.notify_done" type="checkbox" class="accent-accent" /> 任务完成时通知
        </label>
        <label class="flex cursor-pointer items-center gap-2 text-[13px]">
          <input v-model="mail.notify_failed" type="checkbox" class="accent-accent" /> 任务失败时通知
        </label>
      </div>

      <div class="mt-4 flex flex-wrap items-end gap-2.5">
        <button :class="btn" :disabled="savingMail" @click="saveMail()">{{ savingMail ? '保存中…' : '保存设置' }}</button>
        <div class="w-[260px]">
          <label :class="label">发送测试邮件</label>
          <input v-model="testTo" type="email" placeholder="收件邮箱" :class="input" @keyup.enter="sendTest" />
        </div>
        <button :class="btnGhost" :disabled="testing" @click="sendTest">{{ testing ? '发送中…' : '发送测试' }}</button>
        <span :class="hint">测试会先保存当前表单，再按该配置真实发送一封邮件。</span>
      </div>
    </div>

    <!-- 平台模型管理 -->
    <div :class="card">
      <h3 :class="h3">平台模型管理</h3>

      <!-- 第一步：密钥查询 -->
      <div class="mt-3 flex max-w-[640px] items-end gap-2.5">
        <div class="flex-1">
          <label :class="label">网关密钥（API Key）</label>
          <input
            v-model="discoverKey" type="password" placeholder="sk-…"
            class="w-full rounded-md border border-border bg-panel2 px-2.5 py-2 text-[13.5px] text-text outline-none focus:border-accent"
            @keyup.enter="discover"
          />
        </div>
        <button :class="btn" :disabled="discovering" @click="discover">{{ discovering ? '查询中…' : '查询模型' }}</button>
      </div>

      <!-- 第二步：勾选添加 -->
      <div v-if="discovered.length" class="mt-3.5">
        <div class="max-h-56 overflow-auto rounded-lg border border-border">
          <div
            v-for="name in discovered" :key="name"
            class="flex items-center gap-2.5 border-b border-border px-3 py-2 last:border-b-0 hover:bg-panel2"
          >
            <label class="flex flex-1 cursor-pointer items-center gap-2.5" :class="modelNames.has(name) ? 'pointer-events-none opacity-60' : ''">
              <input v-model="checked[name]" type="checkbox" class="accent-accent" :disabled="modelNames.has(name)" />
              <span class="font-mono text-[13px]">{{ name }}</span>
            </label>
            <span v-if="modelNames.has(name)" :class="badge + ' bg-ok/15 text-ok'">已添加</span>
            <label v-else class="flex cursor-pointer items-center gap-1 text-xs text-muted">
              <input v-model="defaultPick" :value="name" type="radio" class="accent-accent" /> 设为默认
            </label>
          </div>
        </div>
        <div class="mt-2.5 flex items-center gap-2.5">
          <button :class="btn" :disabled="adding || !checkedNames.length" @click="addSelected">
            {{ adding ? '添加中…' : `添加所选（${checkedNames.length}）` }}
          </button>
          <span v-if="defaultPick" class="text-xs text-muted">默认模型：{{ defaultPick }}</span>
        </div>
      </div>

      <!-- 当前列表 -->
      <div class="mt-4">
        <div class="mb-1.5 text-[12.5px] text-muted">平台当前可用模型：</div>
        <div v-if="!models.length" :class="hint">暂无模型；请先通过密钥查询并添加，否则用户无法提交任务。</div>
        <table v-else class="w-full border-collapse">
          <thead>
            <tr>
              <th :class="tableTh">模型</th>
              <th :class="tableTh">默认</th>
              <th :class="tableTh">添加时间</th>
              <th :class="tableTh"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in models" :key="m.id">
              <td :class="tableTd"><span class="font-mono text-[13px]">{{ m.name }}</span></td>
              <td :class="tableTd">
                <span v-if="m.is_default" :class="badge + ' bg-accent/15 text-accent'">默认</span>
                <button v-else :class="btnGhost + ' !px-3 !py-1 !text-xs'" @click="setDefault(m)">设为默认</button>
              </td>
              <td :class="tableTd + ' text-muted'">{{ fmtTime(m.created_at) }}</td>
              <td :class="tableTd + ' text-right'">
                <button :class="btnDanger + ' !px-3 !py-1 !text-xs'" :disabled="modelBusy" @click="removeModel(m)">移除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
