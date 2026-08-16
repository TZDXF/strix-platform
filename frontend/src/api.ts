export interface User {
  id: number
  username: string
  role: 'admin' | 'user'
  display_name: string
  is_active: boolean
  has_llm_key: boolean
  email: string
  created_at: string | null
  last_login_at: string | null
}

export interface TestTarget {
  url: string
  note: string
}

export interface GitRepoRef {
  url: string
  note: string
  /** 凭据来源（仅展示，后端给出）：repo=已保存该仓库专属令牌；project=旧版项目级 PAT；none=无凭据 */
  credential?: 'repo' | 'project' | 'none'
  /** 该仓库专属访问令牌：仅创建/编辑提交时携带，保存后后端不回显（编辑留空=保持已存令牌） */
  token?: string
}

export interface RepoBranch {
  url: string
  branch: string
}

export interface Project {
  id: string
  name: string
  description: string
  source_type: 'git' | 'zip'
  git_url: string // 兼容旧字段：首个仓库
  git_repos: GitRepoRef[]
  git_auth_type: '' | 'token'
  has_credentials: boolean
  is_archived: boolean
  default_test_url: string // 兼容旧字段：首个地址
  default_test_targets: TestTarget[]
  created_by: number | null
  created_by_name: string
  created_at: string | null
  tasks_count: number
  uploads_count: number
}

export interface PlatformModel {
  id: number
  name: string
  is_default: boolean
  created_at: string | null
}

export interface UploadRecord {
  id: string
  filename: string
  size_bytes: number
  created_at: string | null
}

export interface TaskSummary {
  id: string
  project_id: string | null
  project_name: string
  created_by: number | null
  created_by_name: string
  created_at: string | null
  status: string
  scan_mode: string
  source_type: 'git' | 'zip'
  source_ref: string
  branch: string
  repo_branches: RepoBranch[]
  test_url: string // 兼容旧字段：首个地址
  test_targets: TestTarget[]
  instruction: string
  report_lang: 'en' | 'zh'
  zh_status: string
  model: string
  findings_count: number
  severity_counts: Record<string, number>
  duration_sec: number | null
  exit_code: number | null
  timed_out: boolean
  total_tokens: number | null
  error: string
}

export interface Finding {
  id: number
  vuln_id: string
  title: string
  title_zh: string
  severity: string
  cvss: number | null
  cwe: string
  endpoint: string
  target: string
  has_poc: boolean
  description: string
  description_zh: string
  remediation: string
  remediation_zh: string
  poc_description: string
  poc_code: string
}

export interface AgentUsage {
  agent_id: string
  agent_name: string
  model: string
  parent: string
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  started_at: string
  finished_at: string
  status: string
}

export interface TaskDetail extends TaskSummary {
  started_at: string | null
  finished_at: string | null
  attempts: number
  run_dir_name: string
  strix_version: string
  input_tokens: number | null
  output_tokens: number | null
  llm_requests: number | null
  agents: AgentUsage[]
  has_artifacts: boolean
  has_report_md: boolean
  report_md: string
  findings: Finding[]
}

export interface StatsTrendPoint {
  date: string
  count: number
  tokens: number
}

export interface StatsTopProject {
  id: string
  name: string
  tasks: number
  findings: number
  severity_counts: Record<string, number>
}

export interface StatsData {
  scope: 'all' | 'mine'
  projects_total: number
  projects_archived: number
  tasks_total: number
  tasks_by_status: Record<string, number>
  tasks_by_mode: Record<string, number>
  tasks_by_model: Record<string, number>
  findings_total: number
  findings_by_severity: Record<string, number>
  avg_duration_sec: number | null
  total_tokens: number
  total_input_tokens: number
  total_output_tokens: number
  llm_requests_total: number
  tokens_by_model: Record<string, number>
  trend: StatsTrendPoint[]
  top_projects: StatsTopProject[]
}

const TOKEN_KEY = 'strix_token'
const USER_KEY = 'strix_user'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function getUser(): User | null {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null') as User | null
  } catch {
    return null
  }
}
export function setSession(token: string, user: User): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}
export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
export function updateStoredUser(user: User): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

interface RequestOptions {
  json?: unknown
  form?: FormData
}

async function request<T>(method: string, url: string, { json, form }: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: 'Bearer ' + getToken() }
  let body: BodyInit | undefined
  if (form) {
    body = form
  } else if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  }
  const resp = await fetch(url, { method, headers, body })
  if (resp.status === 401) {
    clearSession()
    if (location.hash !== '#/login') location.hash = '#/login'
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = ((await resp.json()) as { detail?: string }).detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return (await resp.json()) as T
}

async function download(url: string, filename: string): Promise<void> {
  const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + getToken() } })
  if (resp.status === 401) {
    clearSession()
    location.hash = '#/login'
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = ((await resp.json()) as { detail?: string }).detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  const blob = await resp.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

export interface TargetCheckResult {
  allowed: boolean
  reason: string
  reachable: boolean | null
  status_code: number | null
  latency_ms: number | null
  detail: string
}

export interface GitRepoCheckResult {
  reachable: boolean
  detail: string
  branches: string[]
  latency_ms: number | null
}

export interface SubmitTaskParams {
  scanMode: string
  repoBranches?: RepoBranch[]
  testTargets?: TestTarget[]
  instruction?: string
  model?: string
  branch?: string
  uploadId?: string
  file?: File | null
}

export interface UserPatch {
  password?: string
  role?: string
  display_name?: string
  is_active?: boolean
}

export interface GitConfig {
  id: string
  name: string
  base_url: string
  has_token: boolean
  created_at: string | null
}

export interface MailSettings {
  smtp_host: string
  smtp_port: number
  smtp_user: string
  has_password: boolean
  smtp_use_tls: boolean
  smtp_ssl: boolean
  mail_from: string
  mail_sender_name: string
  site_url: string
  notify_done: boolean
  notify_failed: boolean
  configured: boolean
}

export interface MailSettingsPayload {
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password?: string
  clear_password?: boolean
  smtp_use_tls: boolean
  smtp_ssl: boolean
  mail_from: string
  mail_sender_name: string
  site_url: string
  notify_done: boolean
  notify_failed: boolean
}

export interface GitNamespace {
  kind: string // group（组织）| user（个人）
  name: string
  full_path: string
}

export interface GitProject {
  id: number
  name: string
  path_with_namespace: string
  web_url: string
  http_url_to_repo: string
  default_branch: string
  visibility: string
  last_activity_at: string
  namespace: GitNamespace
}

export interface ProjectPayload {
  name: string
  description?: string
  source_type?: string
  git_url?: string
  git_repos?: GitRepoRef[]
  git_auth_type?: string
  git_token?: string
  git_config_id?: string
  default_test_url?: string
  default_test_targets?: TestTarget[]
}

export interface ProjectDetailData extends Project {
  uploads: UploadRecord[]
  tasks: TaskSummary[]
}

export const api = {
  health: () => request<{ status: string }>('GET', '/api/health'),
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>('POST', '/api/auth/login', { json: { username, password } }),
  me: () => request<User>('GET', '/api/auth/me'),
  changePassword: (oldPassword: string, newPassword: string) =>
    request<{ ok: boolean }>('POST', '/api/auth/change-password', { json: { old_password: oldPassword, new_password: newPassword } }),

  // 个人设置
  setLlmKey: (apiKey: string) => request<User>('PUT', '/api/me/llm-key', { json: { api_key: apiKey } }),
  clearLlmKey: () => request<User>('DELETE', '/api/me/llm-key'),
  setEmail: (email: string) => request<User>('PUT', '/api/me/email', { json: { email } }),

  // 个人 Git 配置（GitLab）
  listGitConfigs: () => request<{ items: GitConfig[] }>('GET', '/api/git-configs'),
  createGitConfig: (c: { name?: string; base_url: string; token: string }) =>
    request<GitConfig>('POST', '/api/git-configs', { json: c }),
  updateGitConfig: (id: string, c: { name?: string; base_url?: string; token?: string }) =>
    request<GitConfig>('PATCH', `/api/git-configs/${id}`, { json: c }),
  deleteGitConfig: (id: string) => request<{ ok: boolean }>('DELETE', `/api/git-configs/${id}`),
  listGitProjects: (configId: string) =>
    request<{ items: GitProject[] }>('GET', `/api/git-configs/${configId}/projects`),

  // 用户管理（超管）
  listUsers: () => request<{ items: User[] }>('GET', '/api/users'),
  createUser: (u: { username: string; password: string; role: string; display_name?: string }) =>
    request<User>('POST', '/api/users', { json: u }),
  patchUser: (id: number, patch: UserPatch) => request<User>('PATCH', `/api/users/${id}`, { json: patch }),
  deleteUser: (id: number) => request<{ ok: boolean }>('DELETE', `/api/users/${id}`),

  // 平台模型管理（超管）
  listPlatformModels: () => request<{ items: PlatformModel[] }>('GET', '/api/admin/models'),
  discoverModels: (apiKey: string) =>
    request<{ items: string[] }>('POST', '/api/admin/models/discover', { json: { api_key: apiKey } }),
  addModels: (names: string[], makeDefault?: string) =>
    request<{ items: PlatformModel[] }>('POST', '/api/admin/models', { json: { names, default: makeDefault || '' } }),
  setDefaultModel: (id: number) => request<PlatformModel>('PATCH', `/api/admin/models/${id}`, { json: { is_default: true } }),
  deleteModel: (id: number) => request<{ ok: boolean }>('DELETE', `/api/admin/models/${id}`),

  // 系统设置：邮件提醒（超管）
  getMailSettings: () => request<MailSettings>('GET', '/api/admin/mail-settings'),
  saveMailSettings: (s: MailSettingsPayload) => request<MailSettings>('PUT', '/api/admin/mail-settings', { json: s }),
  testMailSettings: (to: string) =>
    request<{ ok: boolean; detail: string }>('POST', '/api/admin/mail-settings/test', { json: { to } }),

  // 项目
  listProjects: () => request<{ items: Project[] }>('GET', '/api/projects'),
  createProject: (p: ProjectPayload) => request<Project>('POST', '/api/projects', { json: p }),
  patchProject: (id: string, patch: Partial<ProjectPayload>) =>
    request<Project>('PATCH', `/api/projects/${id}`, { json: patch }),
  archiveProject: (id: string) => request<Project>('POST', `/api/projects/${id}/archive`),
  unarchiveProject: (id: string) => request<Project>('POST', `/api/projects/${id}/unarchive`),
  getProject: (id: string) => request<ProjectDetailData>('GET', `/api/projects/${id}`),
  listBranches: (id: string, repoUrl?: string) =>
    request<{ items: string[] }>('GET', `/api/projects/${id}/branches` + (repoUrl ? `?repo_url=${encodeURIComponent(repoUrl)}` : '')),

  // 项目内 zip 上传
  uploadToProject: (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ id: string; filename: string; size_bytes: number }>(
      'POST', `/api/projects/${projectId}/uploads`, { form },
    )
  },
  deleteUpload: (projectId: string, uploadId: string) =>
    request<{ ok: boolean }>('DELETE', `/api/projects/${projectId}/uploads/${uploadId}`),

  // 任务（在项目内发起）
  submitTask: (projectId: string, p: SubmitTaskParams) => {
    const form = new FormData()
    form.append('scan_mode', p.scanMode)
    if (p.repoBranches) form.append('repo_branches', JSON.stringify(p.repoBranches))
    if (p.branch) form.append('branch', p.branch)
    form.append('test_targets', JSON.stringify(p.testTargets || []))
    if (p.instruction) form.append('instruction', p.instruction)
    if (p.model) form.append('model', p.model)
    if (p.branch) form.append('branch', p.branch)
    if (p.uploadId) form.append('upload_id', p.uploadId)
    if (p.file) form.append('file', p.file)
    return request<{ id: string; status: string }>('POST', `/api/projects/${projectId}/tasks`, { form })
  },
  listTasks: (params = '') => request<{ total: number; items: TaskSummary[] }>('GET', '/api/tasks?limit=100' + (params ? '&' + params : '')),
  stats: () => request<StatsData>('GET', '/api/stats'),
  listModels: () => request<{ default: string; items: string[] }>('GET', '/api/models'),
  checkTarget: (url: string) =>
    request<TargetCheckResult>('POST', '/api/targets/check', { json: { url } }),
  checkGitRepo: (gitUrl: string, authType: string, token: string) =>
    request<GitRepoCheckResult>('POST', '/api/sources/check', {
      json: { git_url: gitUrl, auth_type: authType, token },
    }),
  getTask: (id: string) => request<TaskDetail>('GET', `/api/tasks/${id}`),
  getLog: (id: string) => request<{ log: string }>('GET', `/api/tasks/${id}/log`),
  downloadArtifacts: (id: string) => download(`/api/tasks/${id}/artifacts`, `${id}-artifacts.zip`),
  downloadPdf: (id: string) => download(`/api/tasks/${id}/report.pdf`, `strix-report-${id.slice(0, 10)}.pdf`),
}
