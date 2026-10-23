const API_BASE = '/api/procurement'

function headers() {
  return {
    'Content-Type': 'application/json',
    // 开发身份；生产应换成 JWT/Session 注入。
    'X-User-Id': 'guoyaxing',
    'X-Username': 'guoyaxing',
    'X-Roles': 'requester,approver',
  }
}

export async function createProcurementTask(request) {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({
      message: '',
      request,
    }),
  })
  if (!response.ok) {
    throw new Error(`创建采购任务失败: ${response.status}`)
  }
  return response.json()
}

export async function getProcurementTask(taskId) {
  const response = await fetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, {
    headers: headers(),
  })
  if (!response.ok) {
    throw new Error(`查询采购任务失败: ${response.status}`)
  }
  return response.json()
}

export async function resumeProcurementTask(taskId, resume) {
  const response = await fetch(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/resume`,
    {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ resume }),
    },
  )
  if (!response.ok) {
    throw new Error(`恢复采购任务失败: ${response.status}`)
  }
  return response.json()
}
