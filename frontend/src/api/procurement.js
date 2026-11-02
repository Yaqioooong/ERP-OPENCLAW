import { notifyIfUnauthorized } from './auth.js'

const API_BASE = '/api/procurement'

function jsonHeaders() {
  return { 'Content-Type': 'application/json' }
}

async function parseError(response, fallback) {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') return new Error(data.detail)
    const detail = data?.detail
    if (detail && typeof detail === 'object') {
      const fieldErrors = Array.isArray(detail.errors) ? detail.errors : []
      const summary = fieldErrors
        .map((item) => {
          const field = item?.field ? `${item.field}：` : ''
          return `${field}${item?.message || ''}`.trim()
        })
        .filter(Boolean)
        .join('；')
      const exception = new Error(
        summary || detail.message || detail.code || `${fallback}: ${response.status}`,
      )
      exception.code = detail.code || ''
      exception.fieldErrors = fieldErrors
      return exception
    }
  } catch {
    /* ignore */
  }
  return new Error(`${fallback}: ${response.status}`)
}

export async function createProcurementTask(request) {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: jsonHeaders(),
    body: JSON.stringify({
      message: '',
      request,
    }),
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '创建采购任务失败')
  return response.json()
}

export async function getProcurementTask(taskId) {
  const response = await fetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, {
    credentials: 'same-origin',
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '查询采购任务失败')
  return response.json()
}

export async function resumeProcurementTask(taskId, resume) {
  const response = await fetch(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/resume`,
    {
      method: 'POST',
      credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({ resume }),
    },
  )
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '恢复采购任务失败')
  return response.json()
}
