export interface User {
  id: number
  username: string
  role: 'admin' | 'user'
  display_name: string
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
}

export interface Project {
  id: string
  name: string
  description: string
  source_type: 'git' | 'zip'
  git_url: string
  git_auth_type: '' | 'token' | 'ssh'
  has_credentials: boolean
  default_test_url: string
  created_by: number | null
  created_by_name: string
  created_at: string | null
  tasks_count: number
  uploads_count: number
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
  test_url: string
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
  has_poc: boolean
  description: string
  description_zh: string
  remediation: string
  remediation_zh: string
  poc_description: string
  poc_code: string
}

export interface TaskDetail extends TaskSummary {
  started_at: string | null
  finished_at: string | null
  attempts: number
  run_dir_name: string
  strix_version: string
  has_artifacts: boolean
  has_report_md: boolean
  report_md: string
  findings: Finding[]
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

export interface SubmitTaskParams {
  scanMode: string
  testUrl?: string
  model?: string
  branch?: string
  uploadId?: string
  reportLang?: string
  file?: File | null
}

export interface UserPatch {
  password?: string
  role?: string
  display_name?: string
  is_active?: boolean
}

export interface ProjectPayload {
  name: string
  description?: string
  source_type?: string
  git_url?: string
  git_auth_type?: string
  git_token?: string
  git_ssh_key?: string
  default_test_url?: string
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

  // 用户管理（超管）
  listUsers: () => request<{ items: User[] }>('GET', '/api/users'),
  createUser: (u: { username: string; password: string; role: string; display_name?: string }) =>
    request<User>('POST', '/api/users', { json: u }),
  patchUser: (id: number, patch: UserPatch) => request<User>('PATCH', `/api/users/${id}`, { json: patch }),
  deleteUser: (id: number) => request<{ ok: boolean }>('DELETE', `/api/users/${id}`),

  // 项目
  listProjects: () => request<{ items: Project[] }>('GET', '/api/projects'),
  createProject: (p: ProjectPayload) => request<Project>('POST', '/api/projects', { json: p }),
  patchProject: (id: string, patch: Partial<ProjectPayload>) =>
    request<Project>('PATCH', `/api/projects/${id}`, { json: patch }),
  deleteProject: (id: string) => request<{ ok: boolean }>('DELETE', `/api/projects/${id}`),
  getProject: (id: string) => request<ProjectDetailData>('GET', `/api/projects/${id}`),
  listBranches: (id: string) => request<{ items: string[] }>('GET', `/api/projects/${id}/branches`),

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
    if (p.testUrl) form.append('test_url', p.testUrl)
    if (p.model) form.append('model', p.model)
    if (p.branch) form.append('branch', p.branch)
    if (p.uploadId) form.append('upload_id', p.uploadId)
    form.append('report_lang', p.reportLang || 'en')
    if (p.file) form.append('file', p.file)
    return request<{ id: string; status: string }>('POST', `/api/projects/${projectId}/tasks`, { form })
  },
  listTasks: (params = '') => request<{ total: number; items: TaskSummary[] }>('GET', '/api/tasks?limit=100' + (params ? '&' + params : '')),
  listModels: () => request<{ default: string; items: string[] }>('GET', '/api/models'),
  getTask: (id: string) => request<TaskDetail>('GET', `/api/tasks/${id}`),
  getLog: (id: string) => request<{ log: string }>('GET', `/api/tasks/${id}/log`),
  downloadArtifacts: (id: string) => download(`/api/tasks/${id}/artifacts`, `${id}-artifacts.zip`),
  downloadPdf: (id: string) => download(`/api/tasks/${id}/report.pdf`, `strix-report-${id.slice(0, 10)}.pdf`),
}
