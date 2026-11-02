import { notifyIfUnauthorized } from './auth.js'

const API_BASE = '/api/memory/preferences'

async function parseError(response, fallback) {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') return new Error(data.detail)
  } catch {
    /* ignore */
  }
  return new Error(`${fallback}: ${response.status}`)
}

export async function getMemory() {
  const response = await fetch(API_BASE, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '加载记忆失败')
  return response.json()
}

export async function deleteMemoryItem(memoryType, value) {
  const response = await fetch(`${API_BASE}/items`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ memory_type: memoryType, value }),
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '删除记忆失败')
  return response.json()
}

export async function clearMemory() {
  const response = await fetch(API_BASE, {
    method: 'DELETE',
    credentials: 'same-origin',
  })
  notifyIfUnauthorized(response)
  if (!response.ok) throw await parseError(response, '清空记忆失败')
  return response.json()
}
