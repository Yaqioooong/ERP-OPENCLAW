const TASK_EVENT_TYPES = [
  'task.started',
  'task.progress',
  'approval.required',
  'task.resumed',
  'task.completed',
  'task.failed',
  'task.cancelled',
  'tool.started',
  'tool.completed',
  'task.heartbeat',
]

const TERMINAL = new Set([
  'task.completed',
  'task.failed',
  'task.cancelled',
])

export function connectTaskEvents(taskId, options = {}) {
  const {
    after = 0,
    onEvent = () => {},
    onError = () => {},
    onClose = () => {},
    baseUrl = '',
  } = options

  const url = `${baseUrl}/api/procurement/tasks/${encodeURIComponent(taskId)}/events?after=${after}`
  const source = new EventSource(url)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
    onClose()
  }

  for (const type of TASK_EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      let payload
      try {
        payload = JSON.parse(message.data)
      } catch (error) {
        onError(error)
        return
      }
      onEvent(payload)
      if (TERMINAL.has(payload.type)) {
        close()
      }
    })
  }

  source.onerror = (error) => {
    // EventSource 会自动重连；这里只上报，不主动 close。
    onError(error)
  }

  return {
    close,
    source,
  }
}

export { TASK_EVENT_TYPES, TERMINAL }
