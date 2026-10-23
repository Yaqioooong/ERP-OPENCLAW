<template>
  <div class="app-shell">
    <Sidebar
      ref="sidebarRef"
      :sessions="sessions"
      :current-thread-id="currentThreadId"
      :loading="sessionsLoading"
      :open="sidebarOpen"
      @select-session="handleSelectSession"
      @new-chat="handleNewChat"
      @delete-session="handleDeleteSession"
      @close="sidebarOpen = false"
    />

    <div
      v-if="sidebarOpen"
      class="sidebar-scrim"
      aria-hidden="true"
      @click="sidebarOpen = false"
    />

    <main
      class="workspace"
      :class="{ 'with-task': showProcurement }"
      :inert="isMobile && sidebarOpen"
    >
      <AppHeader
        :title="currentTitle"
        :status="agentStatus"
        :show-process="showToolCalls"
        :has-active-task="hasTask"
        :task-active="taskRunning"
        :task-pending="taskPending"
        :theme="theme"
        @toggle-process="toggleProcess"
        @open-task="openProcurement"
        @toggle-theme="toggleTheme"
        @toggle-sidebar="openSidebar"
      />

      <ChatArea
        ref="chatRef"
        :messages="messages"
        :streaming="isStreaming"
        :show-process="showToolCalls"
        :loading="messagesLoading"
        :pending="!!interruptData"
        @suggest="applySuggestion"
        @open-task="handleOpenTask"
      >
        <template #pending>
          <InterruptBanner
            v-if="interruptData"
            :key="interruptKey"
            :interrupt-data="interruptData"
            :submitting="isResuming"
            :error="resumeError"
            @resume="handleResume"
          />
        </template>
      </ChatArea>

      <InputArea
        v-if="!interruptData"
        ref="composerRef"
        v-model="draft"
        :streaming="isStreaming"
        :show-process="showToolCalls"
        @send="handleSend"
        @stop="handleStop"
        @toggle-process="toggleProcess"
      />

      <div v-else class="paused-bar">
        <LockKeyhole :size="13" />
        <span>{{ pausedHint }}</span>
      </div>

      <ProcurementTaskPanel v-if="showProcurement" @close="closeProcurement" />

      <ToastHost />
    </main>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { LockKeyhole } from '@lucide/vue'
import { useEventListener, useMediaQuery } from '@vueuse/core'
import Sidebar from './components/Sidebar.vue'
import AppHeader from './components/AppHeader.vue'
import ChatArea from './components/ChatArea.vue'
import InputArea from './components/InputArea.vue'
import InterruptBanner from './components/InterruptBanner.vue'
import ProcurementTaskPanel from './components/ProcurementTaskPanel.vue'
import ToastHost from './components/ToastHost.vue'
import { streamChat, resumeChat } from './api/chat.js'
import { getSessions, getMessages, deleteSession } from './api/history.js'
import { useTheme } from './composables/useTheme'
import { useToast } from './composables/useToast'
import { useTaskStore } from './composables/useTaskStore'

const { theme, toggle } = useTheme()
const { toast } = useToast()
const taskStore = useTaskStore()
const isMobile = useMediaQuery('(max-width: 1023px)')

const sidebarRef = ref(null)
const chatRef = ref(null)
const composerRef = ref(null)

const sessions = ref([])
const sessionsLoading = ref(true)
const currentThreadId = ref(null)
const messages = ref([])
const messagesLoading = ref(false)
const isStreaming = ref(false)
const isResuming = ref(false)
const showToolCalls = ref(true)
const sidebarOpen = ref(false)
const draft = ref('')
const resumeError = ref('')
const interruptData = ref(null)

const showProcurement = ref(false)

let abortController = null
let streamGeneration = 0

const hasTask = computed(() => taskStore.isForThread(currentThreadId.value))

const taskPending = computed(() =>
  ['awaiting_approval', 'awaiting_retry', 'need_info'].includes(
    taskStore.task.value?.status,
  ),
)

const taskRunning = computed(() => {
  const status = taskStore.task.value?.status
  return [
    'running',
    'parsing',
    'analyzing',
    'need_info',
    'awaiting_approval',
    'awaiting_retry',
    'approved_pending_execution',
    'executing',
    'verifying',
  ].includes(status)
})


const currentTitle = computed(() => {
  const session = sessions.value.find((item) => item.thread_id === currentThreadId.value)
  return session?.title || '新对话'
})

const toolCalling = computed(() =>
  messages.value.some((message) => message.role === 'tool' && message.tool_status === 'calling'),
)

const agentStatus = computed(() => {
  if (interruptData.value) {
    const type = interruptData.value.interrupt_type
    const supplement =
      type === 'order_info_supplement' || type === 'procurement_task_clarification'
    return { tone: 'warn', text: supplement ? '等待补充' : '等待确认' }
  }
  if (isResuming.value) return { tone: 'run', text: '正在继续' }
  if (isStreaming.value) {
    return { tone: 'run', text: toolCalling.value ? '正在执行工具' : '正在思考' }
  }
  return { tone: 'idle', text: '就绪' }
})

const pausedHint = computed(() => {
  const type = interruptData.value?.interrupt_type
  if (type === 'order_info_supplement' || type === 'procurement_task_clarification') {
    return '请先在上方补充信息，完成后自动继续'
  }
  if (type === 'procurement_task_retry') return '请先在上方选择重试或取消任务'
  return '请先在上方完成确认，完成后自动继续'
})

watch(
  currentTitle,
  (title) => {
    document.title = title && title !== '新对话' ? `${title} · 采购助手` : '采购助手 · ERP'
  },
  { immediate: true },
)

const interruptKey = computed(() => {
  const data = interruptData.value
  if (!data) return 'none'
  return [
    data.interrupt_type,
    data.task_id || '',
    data.approval_id || '',
    data.plan_version || '',
    typeof data.missing_fields === 'string'
      ? data.missing_fields
      : (data.missing_fields || []).join(','),
    data.action_requests?.[0]?.name || '',
  ].join('|')
})

/* ============================================================
   生命周期
   ============================================================ */
onMounted(() => {
  loadSessions()
})

onBeforeUnmount(() => {
  abortController?.abort()
})

/* ============================================================
   会话
   ============================================================ */
async function loadSessions(options = {}) {
  const silent = options.silent === true
  if (!silent) sessionsLoading.value = true
  try {
    const response = await getSessions(1, 100)
    sessions.value = response.sessions || []
  } catch (error) {
    console.error('[App] 加载会话列表失败:', error)
    if (!silent) {
      sessions.value = []
      toast('会话列表加载失败', { tone: 'danger' })
    }
  } finally {
    if (!silent) sessionsLoading.value = false
  }
}

function findLatestTaskId(list) {
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index]
    if (message.role !== 'tool' || message.tool_name !== 'start_procurement_task') {
      continue
    }
    try {
      const data = JSON.parse(message.text || '{}')
      if (data.task_id) return data.task_id
    } catch {
      /* ignore */
    }
  }
  return null
}

function stripInjectedContext(content) {
  if (!content) return content
  const text = content
  if (!text.startsWith('【系统上下文】') && !text.startsWith('[系统通知]')) {
    return content
  }

  let cursor = 0
  const contextEnd = '(recent_suppliers和recent_queries 由系统自动维护，无需手动更新)'
  const contextIndex = text.indexOf(contextEnd)
  if (contextIndex !== -1) cursor = contextIndex + contextEnd.length

  const skillMarkers = [
    '请使用 `ls /skills/` 查看详情',
    '请使用 ls /skills/ 查看详情',
  ]

  function cutAfterSkillBlock(from) {
    for (const marker of skillMarkers) {
      const index = text.indexOf(marker, from)
      if (index === -1) continue
      const end = text.indexOf('。', index)
      if (end !== -1) return end + 1
    }
    return -1
  }

  if (cursor === 0) {
    const end = cutAfterSkillBlock(0)
    if (end === -1) return content
    cursor = end
  } else {
    const rest = text.slice(cursor)
    if (rest.includes('[系统通知]') || rest.includes('以下技能包已更新')) {
      const end = cutAfterSkillBlock(cursor)
      if (end !== -1) cursor = end
    }
  }

  return text.slice(cursor).replace(/^[\s\n]+/, '')
}

function sanitizeMessages(list) {
  return (list || [])
    .map((message) => {
      if (message.role !== 'assistant' || !message.content) {
        return message
      }
      const content = stripInjectedContext(message.content)
      if (!content) return null
      return { ...message, content }
    })
    .filter(Boolean)
}

async function handleSelectSession(threadId) {
  if (threadId === currentThreadId.value && !interruptData.value) {
    sidebarOpen.value = false
    return
  }

  abortActiveStream()
  sidebarOpen.value = false
  showProcurement.value = false
  currentThreadId.value = threadId
  interruptData.value = null
  resumeError.value = ''
  draft.value = ''
  messages.value = []
  messagesLoading.value = true
  taskStore.focusThread(threadId)

  try {
    const response = await getMessages(threadId)
    messages.value = sanitizeMessages(response.messages)

    const latestTaskId = findLatestTaskId(messages.value)
    if (latestTaskId) {
      taskStore.track(latestTaskId, threadId)
    }
  } catch (error) {
    console.error('[App] 加载会话消息失败:', error)
    toast('会话内容加载失败', { tone: 'danger' })
    messages.value = []
  } finally {
    messagesLoading.value = false
  }
}

function handleNewChat() {
  abortActiveStream()
  showProcurement.value = false
  taskStore.focusThread(null)
  currentThreadId.value = null
  messages.value = []
  messagesLoading.value = false
  interruptData.value = null
  resumeError.value = ''
  draft.value = ''
  sidebarOpen.value = false
  nextTick(() => composerRef.value?.focus())
}

async function handleDeleteSession(threadId) {
  try {
    await deleteSession(threadId)
    toast('对话已删除', { tone: 'success' })
    sessions.value = sessions.value.filter((item) => item.thread_id !== threadId)
    await loadSessions({ silent: true })
    if (currentThreadId.value === threadId) {
      handleNewChat()
    }
  } catch (error) {
    console.error('[App] 删除会话失败:', error)
    toast('删除会话失败，请重试', { tone: 'danger' })
  }
}

function abortActiveStream() {
  // 让旧流的回调失效，避免切换会话后旧 token 污染新会话
  streamGeneration += 1
  if (!abortController) return
  abortController.abort()
  abortController = null
  isStreaming.value = false
  isResuming.value = false
}

/* ============================================================
   主题与过程开关
   ============================================================ */
function toggleTheme() {
  toggle()
}

function toggleProcess() {
  showToolCalls.value = !showToolCalls.value
}

function openSidebar(focusSearch = false) {
  if (isMobile.value) showProcurement.value = false
  sidebarOpen.value = true
  if (focusSearch) nextTick(() => sidebarRef.value?.focusSearch())
}

/* ============================================================
   输入
   ============================================================ */
function applySuggestion(text) {
  draft.value = text
  nextTick(() => composerRef.value?.focus())
}

/* ============================================================
   采购任务
   ============================================================ */
function openProcurement() {
  if (!hasTask.value) {
    toast('当前对话还没有采购任务，请在对话中发起采购任务', { duration: 3000 })
    return
  }
  showProcurement.value = true
}

function closeProcurement() {
  showProcurement.value = false
}

async function handleOpenTask(taskId) {
  if (!taskId) return
  await taskStore.track(taskId, currentThreadId.value)
  showProcurement.value = true
}

function trackProcurement(tool) {
  if (tool?.name !== 'start_procurement_task' || !tool.text) return
  try {
    const data = JSON.parse(tool.text)
    if (data.task_id) {
      taskStore.track(data.task_id, currentThreadId.value)
    }
  } catch (error) {
    console.warn('[App] 无法解析采购任务工具结果', error)
  }
}

/* ============================================================
   流式回调
   ============================================================ */
function markToolsDone() {
  const now = Date.now()
  for (const message of messages.value) {
    if (message.role === 'tool' && message.tool_status === 'calling') {
      message.tool_status = 'done'
      message.finished_at = message.finished_at || now
    }
  }
}

function dropEmptyAssistant() {
  messages.value = messages.value.filter(
    (message) => !(message.role === 'assistant' && !message.content),
  )
}

function isInjectedContext(text) {
  if (!text) return false
  const value = text.trimStart()
  return (
    value.startsWith('【系统上下文】') ||
    value.startsWith('[系统通知]') ||
    value.includes('以下技能包已更新') ||
    value.includes('用户偏好文件路径: /memories/')
  )
}

function createCallbacks(isCurrent = () => true) {
  const callbacks = {
    onToken(content, source) {
      // 兼容未热更新后端：中间件注入的上下文不应出现在助手回复里
      if (isInjectedContext(content)) return
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant') {
        last.content += content
        last.source = source || last.source
      } else {
        messages.value.push({
          id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          role: 'assistant',
          content,
          source: source || 'main',
          created_at: new Date().toISOString(),
        })
      }
    },

    onToolStart(tool) {
      messages.value.push({
        id: tool.id,
        role: 'tool',
        tool_name: tool.name,
        args: '',
        text: '',
        images: [],
        source: tool.source || 'main',
        tool_status: 'calling',
        started_at: Date.now(),
        created_at: new Date().toISOString(),
      })
    },

    onToolArgs(args) {
      for (let index = messages.value.length - 1; index >= 0; index -= 1) {
        const message = messages.value[index]
        if (message.role === 'tool' && message.tool_status === 'calling') {
          message.args += args
          break
        }
      }
    },

    onToolResult(tool) {
      for (let index = messages.value.length - 1; index >= 0; index -= 1) {
        const message = messages.value[index]
        if (message.role === 'tool' && message.id === tool.id) {
          message.text = tool.text || ''
          message.images = tool.images || []
          message.tool_status = 'done'
          message.finished_at = Date.now()
          break
        }
      }
      trackProcurement(tool)
    },

    onToolEnd(tool) {
      const now = Date.now()
      if (tool) {
        for (let index = messages.value.length - 1; index >= 0; index -= 1) {
          const message = messages.value[index]
          if (message.role === 'tool' && message.id === tool.id) {
            message.tool_status = 'done'
            message.finished_at = message.finished_at || now
            break
          }
        }
      }
      markToolsDone()
    },

    onInterrupt(data) {
      if (data.thread_id) {
        if (!currentThreadId.value) currentThreadId.value = data.thread_id
        taskStore.bindThread(currentThreadId.value)
        loadSessions({ silent: true })
      }
      markToolsDone()
      dropEmptyAssistant()
      resumeError.value = ''
      interruptData.value = data
    },

    onDone(data) {
      markToolsDone()
      dropEmptyAssistant()

      if (data.aborted) {
        const last = messages.value[messages.value.length - 1]
        if (last && last.role === 'assistant' && !last.content) {
          last.content = '（对话已停止）'
        }
      }

      if (!data.interrupted && !data.aborted) {
        interruptData.value = null
        resumeError.value = ''
      }

      if (data.thread_id) {
        if (!currentThreadId.value) currentThreadId.value = data.thread_id
        taskStore.bindThread(currentThreadId.value)
        loadSessions({ silent: true })
      }
    },

    onError(error) {
      const message = error?.message || '未知错误'
      console.error('[App] 对话错误:', error)

      if (isResuming.value && interruptData.value) {
        resumeError.value = message
        toast('提交失败，请重试', { tone: 'danger' })
        return
      }

      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && !last.content) {
        last.content = `抱歉，发生了错误：${message}`
        last.is_error = true
      } else {
        messages.value.push({
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          content: `抱歉，发生了错误：${message}`,
          source: 'main',
          is_error: true,
          created_at: new Date().toISOString(),
        })
      }
      toast('对话出错，请重试', { tone: 'danger' })
    },
  }

  const guarded = {}
  for (const [name, handler] of Object.entries(callbacks)) {
    guarded[name] = (...args) => {
      if (!isCurrent()) return
      handler(...args)
    }
  }
  return guarded
}

/* ============================================================
   发送与恢复
   ============================================================ */
async function handleSend(text) {
  if (isStreaming.value) {
    toast('正在生成回复，请稍候', { tone: 'default' })
    return
  }

  messages.value.push({
    id: `user-${Date.now()}`,
    role: 'user',
    content: text,
    created_at: new Date().toISOString(),
  })

  interruptData.value = null
  resumeError.value = ''
  isStreaming.value = true
  abortController = new AbortController()
  const generation = ++streamGeneration

  try {
    await streamChat(
      text,
      currentThreadId.value,
      createCallbacks(() => generation === streamGeneration),
      abortController.signal,
    )
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('[App] 发送消息失败:', error)
    }
  } finally {
    isStreaming.value = false
    abortController = null
  }
}

function handleStop() {
  if (!abortController) return
  abortController.abort()
  abortController = null
  isStreaming.value = false
  isResuming.value = false
  toast('已停止生成')
}

async function handleResume(resumeData) {
  if (isResuming.value) return
  if (!currentThreadId.value) {
    toast('缺少会话 ID，无法继续', { tone: 'danger' })
    return
  }

  isResuming.value = true
  resumeError.value = ''
  abortController = new AbortController()
  const generation = ++streamGeneration

  try {
    await resumeChat(
      currentThreadId.value,
      resumeData,
      createCallbacks(() => generation === streamGeneration),
      abortController.signal,
    )
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('[App] 恢复请求失败:', error)
    }
  } finally {
    isResuming.value = false
    abortController = null
  }
}

/* ============================================================
   快捷键
   ============================================================ */
useEventListener(window, 'keydown', (event) => {
  const meta = event.metaKey || event.ctrlKey
  const key = event.key.toLowerCase()

  if (meta && key === 'k') {
    event.preventDefault()
    if (isMobile.value) openSidebar(true)
    else nextTick(() => sidebarRef.value?.focusSearch())
    return
  }

  if (meta && event.shiftKey && key === 'o') {
    event.preventDefault()
    handleNewChat()
    return
  }

  if (event.key === 'Escape') {
    if (sidebarOpen.value) {
      sidebarOpen.value = false
      return
    }
    if (isStreaming.value && !interruptData.value) {
      handleStop()
    }
  }
})
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: var(--canvas);
}

.workspace {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  height: 100%;
  background: var(--surface);
  transition: padding-right var(--dur-slow) var(--ease);
}

@media (min-width: 1280px) {
  .workspace.with-task {
    padding-right: var(--task-panel-w);
  }
}

.sidebar-scrim {
  position: fixed;
  inset: 0;
  z-index: 54;
  background: var(--overlay);
  backdrop-filter: blur(2px);
  animation: fadeIn var(--dur) var(--ease);
}

@media (min-width: 1024px) {
  .sidebar-scrim {
    display: none;
  }
}

.paused-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex-shrink: 0;
  min-height: 54px;
  padding: 12px 20px;
  border-top: 1px solid var(--line-soft);
  background: var(--surface);
  color: var(--ink-3);
  font-size: 11.5px;
}

.paused-bar svg {
  color: var(--warn);
}
</style>
