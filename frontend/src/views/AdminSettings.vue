<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api, type PlatformModel, type MailSettings, type ScanSettings, type SkillInfo } from '../api'
import { toast } from '../toast'
import { badge, btn, btnDanger, btnGhost, card, hint, h3, input, label, tableTd, tableTh } from '../ui'

function fmtTime(iso: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '-' }

// ---- 扫描并发 ----
const scanSettings = ref<ScanSettings | null>(null)
const scanConcurrency = ref(2)
const savingScan = ref(false)

async function loadScanSettings() {
  try {
    const s = await api.getScanSettings()
    scanSettings.value = s
    scanConcurrency.value = s.scan_concurrency
  } catch (e) { toast.error((e as Error).message) }
}

async function saveScanSettings() {
  const max = scanSettings.value?.max_concurrency ?? 8
  const n = Number(scanConcurrency.value)
  if (!Number.isInteger(n) || n < 1 || n > max) {
    toast.error(`并行扫描数需为 1-${max} 之间的整数`); return
  }
  savingScan.value = true
  try {
    const s = await api.saveScanSettings({ scan_concurrency: n })
    scanSettings.value = s
    scanConcurrency.value = s.scan_concurrency
    toast.success('扫描并发已保存，立即生效。')
  } catch (e) { toast.error((e as Error).message) } finally { savingScan.value = false }
}

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

// ---- 技能库管理 ----
const skills = ref<SkillInfo[]>([])
const skillBusy = ref(false)
// 编辑区：null=收起；{id:'', ...}=新建；非空 id=编辑既有技能
const skillEdit = ref<{ id: string; name: string; category: string; description: string; content: string } | null>(null)
const savingSkill = ref(false)

async function loadSkills() {
  try { skills.value = (await api.listAdminSkills()).items } catch (e) { toast.error((e as Error).message) }
}

function newSkill() {
  skillEdit.value = { id: '', name: '', category: 'custom', description: '', content: '' }
}

function editSkill(s: SkillInfo) {
  skillEdit.value = { id: s.id, name: s.name, category: s.category, description: s.description, content: s.content || '' }
}

async function importSkillFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  ;(e.target as HTMLInputElement).value = ''
  if (!f) return
  if (!f.name.toLowerCase().endsWith('.md')) { toast.error('请选择 .md 技能文件'); return }
  const content = await f.text()
  skillBusy.value = true
  try {
    const s = await api.importSkill(content, 'custom')
    toast.success(`已导入技能 ${s.full_name}`)
    await loadSkills()
  } catch (err) { toast.error((err as Error).message) } finally { skillBusy.value = false }
}

async function saveSkill() {
  const ed = skillEdit.value
  if (!ed) return
  if (!ed.id) {
    if (!ed.name.trim()) { toast.error('请填写技能名（小写字母/数字/中划线）'); return }
    if (!ed.content.trim()) { toast.error('技能内容不能为空'); return }
    savingSkill.value = true
    try {
      await api.createSkill({ name: ed.name.trim(), category: ed.category.trim() || 'custom', description: ed.description.trim(), content: ed.content })
      toast.success('技能已创建')
      skillEdit.value = null
      await loadSkills()
    } catch (e) { toast.error((e as Error).message) } finally { savingSkill.value = false }
  } else {
    savingSkill.value = true
    try {
      await api.patchSkill(ed.id, { description: ed.description.trim(), content: ed.content })
      toast.success('技能已保存')
      skillEdit.value = null
      await loadSkills()
    } catch (e) { toast.error((e as Error).message) } finally { savingSkill.value = false }
  }
}

async function toggleSkill(s: SkillInfo) {
  skillBusy.value = true
  try {
    await api.patchSkill(s.id, { enabled: !s.enabled })
    await loadSkills()
    toast.success(`已${s.enabled ? '停用' : '启用'} ${s.full_name}${!s.enabled ? '' : '，新发起的扫描即可使用'}`)
  } catch (e) { toast.error((e as Error).message) } finally { skillBusy.value = false }
}

async function removeSkill(s: SkillInfo) {
  if (!confirm(`确定删除技能「${s.full_name}」？删除后新发起的扫描不再注入该技能。`)) return
  skillBusy.value = true
  try {
    await api.deleteSkill(s.id)
    await loadSkills()
    toast.success(`已删除「${s.full_name}」`)
  } catch (e) { toast.error((e as Error).message) } finally { skillBusy.value = false }
}

onMounted(() => {
  loadScanSettings()
  loadModels()
  loadMail()
  loadSkills()
})
</script>

<template>
  <div class="grid gap-4">
    <!-- 扫描并发 -->
    <div :class="card">
      <h3 :class="h3">扫描并发</h3>
      <p :class="hint">
        允许同时执行的扫描任务数（默认 2）。排队超出的任务自动等待空位（列表中保持「排队中」，
        任务日志会提示槽位占用），调整立即生效，无需重启 worker。上限为 worker 线程池硬顶
        {{ scanSettings?.max_concurrency ?? 8 }}；并发越高，宿主机 CPU/内存与 LLM 网关压力越大。
      </p>
      <div class="mt-3 flex flex-wrap items-end gap-2.5">
        <div class="w-[220px]">
          <label :class="label">最大并行扫描数（1-{{ scanSettings?.max_concurrency ?? 8 }}）</label>
          <input
            v-model.number="scanConcurrency" type="number" min="1"
            :max="scanSettings?.max_concurrency ?? 8" :class="input"
          />
        </div>
        <button :class="btn" :disabled="savingScan" @click="saveScanSettings">{{ savingScan ? '保存中…' : '保存' }}</button>
      </div>
    </div>

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

    <!-- 技能库管理 -->
    <div :class="card">
      <div class="flex flex-wrap items-center gap-2.5">
        <h3 :class="[h3, 'mb-0']">技能库</h3>
        <span :class="badge + (skills.some(s => s.enabled) ? ' bg-ok/15 text-ok' : ' bg-border/30 text-muted')">
          {{ skills.filter(s => s.enabled).length }} / {{ skills.length }} 启用
        </span>
        <div class="flex-1"></div>
        <label :class="btnGhost + ' !px-3 !py-1.5 !text-xs cursor-pointer'">
          导入 .md 文件
          <input type="file" accept=".md" class="hidden" :disabled="skillBusy" @change="importSkillFile" />
        </label>
        <button :class="btnGhost + ' !px-3 !py-1.5 !text-xs'" @click="newSkill">新建技能</button>
      </div>
      <p :class="hint">
        技能是注入扫描引擎的知识包（Markdown + frontmatter）：引擎按任务相关性自动加载（每个智能体最多 5 个），
        也可由智能体经 load_skill 显式加载。同路径技能可遮蔽内置技能；启停/编辑对新发起的扫描即时生效。
        「联网搜索」即平台种子技能（custom/web_search_internal），任务勾选联网搜索时依赖它处于启用状态。
      </p>

      <!-- 新建/编辑表单 -->
      <div v-if="skillEdit" class="mt-3.5 rounded-lg border border-accent/40 bg-accent/[.04] p-3.5">
        <div class="grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))">
          <div v-if="!skillEdit.id">
            <label :class="label">技能名（小写字母/数字/中划线）</label>
            <input v-model="skillEdit.name" type="text" placeholder="internal-api-testing" :class="input" />
          </div>
          <div v-if="!skillEdit.id">
            <label :class="label">类目</label>
            <input v-model="skillEdit.category" type="text" placeholder="custom" :class="input" />
          </div>
          <div class="!col-span-1" style="grid-column: 1 / -1">
            <label :class="label">描述（frontmatter description，引擎按它判断相关性）</label>
            <input v-model="skillEdit.description" type="text" placeholder="Testing techniques for the company internal API gateway" :class="input" />
          </div>
        </div>
        <div class="mt-3">
          <label :class="label">技能正文（Markdown；缺 frontmatter 时保存会按技能名/描述自动补齐）</label>
          <textarea
            v-model="skillEdit.content" rows="10"
            placeholder="---&#10;name: internal-api-testing&#10;description: ...&#10;---&#10;&#10;# 技能标题&#10;&#10;测试方法、命令、校验要点……"
            class="w-full resize-y rounded-md border border-border bg-panel2 px-2.5 py-2 font-mono text-[12.5px] leading-relaxed text-text outline-none focus:border-accent"
          ></textarea>
        </div>
        <div class="mt-3 flex items-center gap-2.5">
          <button :class="btn" :disabled="savingSkill" @click="saveSkill">{{ savingSkill ? '保存中…' : '保存技能' }}</button>
          <button :class="btnGhost" @click="skillEdit = null">取消</button>
        </div>
      </div>

      <!-- 技能列表 -->
      <table v-if="skills.length" class="mt-3.5 w-full border-collapse">
        <thead>
          <tr>
            <th :class="tableTh">技能</th>
            <th :class="tableTh">描述</th>
            <th :class="tableTh">状态</th>
            <th :class="tableTh">更新时间</th>
            <th :class="tableTh"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in skills" :key="s.id">
            <td :class="tableTd">
              <span class="font-mono text-[13px]">{{ s.full_name }}</span>
              <span v-if="s.builtin" :class="badge + ' ml-1.5 bg-accent/15 text-accent'" title="平台种子技能（随系统配置自动生成）">内置</span>
            </td>
            <td :class="tableTd + ' max-w-[360px] truncate text-muted'" :title="s.description">{{ s.description || '-' }}</td>
            <td :class="tableTd">
              <span :class="badge + (s.enabled ? ' bg-ok/15 text-ok' : ' bg-border/30 text-muted')">{{ s.enabled ? '启用' : '停用' }}</span>
            </td>
            <td :class="tableTd + ' text-muted'">{{ fmtTime(s.updated_at) }}</td>
            <td :class="tableTd + ' text-right'">
              <div class="flex justify-end gap-1.5">
                <button :class="btnGhost + ' !px-2.5 !py-1 !text-xs'" @click="editSkill(s)">编辑</button>
                <button :class="btnGhost + ' !px-2.5 !py-1 !text-xs'" :disabled="skillBusy" @click="toggleSkill(s)">{{ s.enabled ? '停用' : '启用' }}</button>
                <button v-if="!s.builtin" :class="btnDanger + ' !px-2.5 !py-1 !text-xs'" :disabled="skillBusy" @click="removeSkill(s)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else :class="hint" class="mt-2">技能库为空。可「新建技能」沉淀公司内部测试方法，或「导入 .md 文件」复用社区技能。</div>
    </div>
  </div>
</template>
