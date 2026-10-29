const AUTH_BASE = '/api/auth'
const ADMIN_BASE = '/api/admin'

function notifyIfUnauthorized(response) {
  if (response?.status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('auth:expired'))
  }
}

async function parseError(response) {
  let payload = null
  try {
    payload = await response.json()
  } catch {
    /* ignore */
  }
  const detail = payload?.detail
  const error = new Error(
    typeof detail === 'string'
      ? detail
      : detail?.message || `请求失败: ${response.status}`,
  )
  error.status = response.status
  error.detail = detail
  error.fieldErrors = detail?.field_errors || null
  return error
}

async function request(url, { method = 'GET', body } = {}) {
  const response = await fetch(url, {
    method,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return null
  return response.json()
}

export async function login(username, password) {
  return request(`${AUTH_BASE}/login`, {
    method: 'POST',
    body: { username, password },
  })
}

export async function register(payload) {
  return request(`${AUTH_BASE}/register`, {
    method: 'POST',
    body: payload,
  })
}

export async function getPolicy() {
  return request(`${AUTH_BASE}/policy`)
}

export async function getMe() {
  return request(`${AUTH_BASE}/me`)
}

export async function logout() {
  return request(`${AUTH_BASE}/logout`, { method: 'POST' })
}

export async function logoutAll() {
  return request(`${AUTH_BASE}/logout-all`, { method: 'POST' })
}

export async function changePassword(payload) {
  return request(`${AUTH_BASE}/change-password`, {
    method: 'POST',
    body: payload,
  })
}

export async function requestApproverRole(reason = '') {
  return request(`${AUTH_BASE}/role-requests`, {
    method: 'POST',
    body: { role: 'approver', reason },
  })
}

export async function getMyRoleRequests() {
  return request(`${AUTH_BASE}/role-requests/mine`)
}

export async function devLogin() {
  return request(`${AUTH_BASE}/dev-login`, { method: 'POST' })
}

export async function listUsers({ query = '', isActive = null, limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (query) params.set('query', query)
  if (isActive !== null) params.set('is_active', String(isActive))
  return request(`${ADMIN_BASE}/users?${params.toString()}`)
}

export async function updateUser(userId, payload) {
  return request(`${ADMIN_BASE}/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: payload,
  })
}

export async function resetUserPassword(userId) {
  return request(`${ADMIN_BASE}/users/${encodeURIComponent(userId)}/reset-password`, {
    method: 'POST',
  })
}

export async function listRoleRequests(status = 'pending') {
  return request(`${ADMIN_BASE}/role-requests?status=${encodeURIComponent(status)}`)
}

export async function approveRoleRequest(requestId, comment = '') {
  return request(`${ADMIN_BASE}/role-requests/${encodeURIComponent(requestId)}/approve`, {
    method: 'POST',
    body: { comment },
  })
}

export async function rejectRoleRequest(requestId, comment = '') {
  return request(`${ADMIN_BASE}/role-requests/${encodeURIComponent(requestId)}/reject`, {
    method: 'POST',
    body: { comment },
  })
}

export function notifyIfUnauthorizedResponse(response) {
  notifyIfUnauthorized(response)
}

export { notifyIfUnauthorizedResponse as notifyIfUnauthorized }
