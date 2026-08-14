const TOKEN_KEY = 'strix_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t) {
  localStorage.setItem(TOKEN_KEY, t)
}

async function request(method, url, { json, form } = {}) {
  const headers = { 'X-Api-Token': getToken() }
  let body
  if (form) {
    body = form
  } else if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  }
  const resp = await fetch(url, { method, headers, body })
  if (resp.status === 401) throw new Error('令牌无效，请检查访问令牌')
  if (!resp.ok) {
    let detail = resp.statusText
    try { detail = (await resp.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return resp.json()
}

export const api = {
  health: () => request('GET', '/api/health'),
  listTasks: () => request('GET', '/api/tasks?limit=100'),
  getTask: (id) => request('GET', `/api/tasks/${id}`),
  getLog: (id) => request('GET', `/api/tasks/${id}/log`),
  submit: ({ scanMode, testUrl, gitUrl, file }) => {
    const form = new FormData()
    form.append('scan_mode', scanMode)
    if (testUrl) form.append('test_url', testUrl)
    if (gitUrl) form.append('git_url', gitUrl)
    if (file) form.append('file', file)
    return request('POST', '/api/tasks', { form })
  },
}
