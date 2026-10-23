import { ref, watch } from 'vue'
import { getProcurementTask, resumeProcurementTask } from '../api/procurement'
import { connectTaskEvents } from '../api/taskEvents'

/**
 * 采购任务单例存储
 *
 * 任务面板、对话中的任务卡片共用同一份状态：
 * - 只有一个活动任务，避免面板与对话出现两份不一致的方案
 * - SSE 事件驱动刷新，审批后对话卡片同步更新
 * - 任务与创建它的对话绑定，新对话不会复用旧任务
 */

const STORAGE_KEY = 'erp-active-task'

const activeTaskId = ref(null)
const conversationThreadId = ref(null)
const task = ref(null)
const events = ref([])
const loading = ref(false)
const error = ref('')

let connection = null
let requestSeq = 0
let refreshTimer = null

function persist() {
  try {
    if (!activeTaskId.value) {
      localStorage.removeItem(STORAGE_KEY)
      return
    }
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        task_id: activeTaskId.value,
        thread_id: conversationThreadId.value,
      }),
    )
  } catch {
    /* ignore quota errors */
  }
}

function restore() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (data?.task_id) {
      activeTaskId.value = data.task_id
      conversationThreadId.value = data.thread_id || null
    }
  } catch {
    /* ignore invalid cache */
  }
}

restore()
// 清理旧版本遗留的全局任务 ID，避免新对话误显示历史任务
try {
  localStorage.removeItem('procurement_active_task_id')
} catch {
  /* ignore */
}
watch([activeTaskId, conversationThreadId], persist)

function isForThread(threadId) {
  return (
    !!threadId &&
    !!activeTaskId.value &&
    conversationThreadId.value === threadId
  )
}

function disconnect() {
  connection?.close()
  connection = null
  if (refreshTimer) {
    window.clearTimeout(refreshTimer)
    refreshTimer = null
  }
}

async function refresh() {
  if (!activeTaskId.value) return null
  const seq = ++requestSeq
  try {
    const result = await getProcurementTask(activeTaskId.value)
    if (seq !== requestSeq) return task.value
    task.value = result
    error.value = ''
    return result
  } catch (exception) {
    if (seq === requestSeq) error.value = exception.message || String(exception)
    return null
  }
}

function scheduleRefresh(delay = 120) {
  if (refreshTimer) window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null
    refresh()
  }, delay)
}

function connect() {
  if (!activeTaskId.value) return
  if (connection) return
  disconnect()
  events.value = []
  connection = connectTaskEvents(activeTaskId.value, {
    onEvent(event) {
      const exists = events.value.some(
        (item) =>
          (event.event_id && item.event_id === event.event_id) ||
          (event.sequence !== undefined && item.sequence === event.sequence),
      )
      if (!exists) {
        events.value = [...events.value.slice(-49), event]
      }
      if (task.value && event.data?.status) {
        task.value = { ...task.value, status: event.data.status }
      }

      if (
        [
          'approval.required',
          'approval.decided',
          'order.created',
          'order.verified',
          'task.resumed',
          'task.completed',
          'task.failed',
          'task.cancelled',
        ].includes(event.type)
      ) {
        scheduleRefresh()
      }

      if (['task.completed', 'task.failed', 'task.cancelled'].includes(event.type)) {
        window.setTimeout(disconnect, 1500)
      }
    },
    onError() {
      /* 事件断流时保留手动刷新能力 */
    },
  })
}

async function focusThread(threadId) {
  if (isForThread(threadId)) {
    await refresh()
    connect()
  } else {
    disconnect()
  }
}

async function track(taskId, threadId = null) {
  if (!taskId) return null
  if (activeTaskId.value !== taskId) {
    disconnect()
    activeTaskId.value = taskId
    conversationThreadId.value = threadId || null
    task.value = null
    events.value = []
    error.value = ''
  } else if (threadId && !conversationThreadId.value) {
    conversationThreadId.value = threadId
  }
  const result = await refresh()
  connect()
  return result
}

function bindThread(threadId) {
  if (activeTaskId.value && !conversationThreadId.value && threadId) {
    conversationThreadId.value = threadId
  }
}

async function resume(payload) {
  if (!activeTaskId.value || loading.value) return null
  loading.value = true
  error.value = ''
  try {
    const result = await resumeProcurementTask(activeTaskId.value, payload)
    task.value = result
    return result
  } catch (exception) {
    error.value = exception.message || String(exception)
    throw exception
  } finally {
    loading.value = false
  }
}

function clear() {
  disconnect()
  activeTaskId.value = null
  conversationThreadId.value = null
  task.value = null
  events.value = []
  error.value = ''
}

export function useTaskStore() {
  return {
    activeTaskId,
    conversationThreadId,
    task,
    events,
    loading,
    error,
    isForThread,
    focusThread,
    track,
    bindThread,
    refresh,
    resume,
    connect,
    disconnect,
    clear,
  }
}
