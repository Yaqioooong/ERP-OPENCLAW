<template>
  <div class="chat-area">
    <div
      ref="scrollerRef"
      class="scroller"
      role="log"
      aria-label="对话内容"
      @scroll.passive="onScroll"
    >
      <p class="sr-only" aria-live="polite">
        {{ streaming ? '正在生成回复' : pending ? '有一条待处理事项' : '' }}
      </p>

      <div v-if="loading" class="stream stream-skeleton" aria-hidden="true">
        <div class="sk-user" />
        <div class="sk-assistant">
          <div class="sk-line" style="width: 92%" />
          <div class="sk-line" style="width: 78%" />
          <div class="sk-line" style="width: 56%" />
        </div>
        <div class="sk-user short" />
        <div class="sk-assistant">
          <div class="sk-line" style="width: 86%" />
          <div class="sk-line" style="width: 64%" />
        </div>
      </div>

      <div v-else-if="isEmpty" class="empty-wrap">
        <EmptyState @pick="$emit('suggest', $event)" />
      </div>

      <div v-else class="stream">
        <template v-for="item in renderItems" :key="item.key">
          <MessageItem
            v-if="item.type === 'message'"
            :message="item.message"
            :is-streaming="isStreamingForMessage(item.message)"
          />
          <TaskCard
            v-else-if="item.type === 'task'"
            :task-id="item.taskId"
            @open="$emit('open-task', $event)"
          />
          <ProcessTimeline v-else :items="item.items" />
        </template>

        <slot name="pending" />

        <div class="stream-end" />
      </div>
    </div>

    <Transition name="jump">
      <button
        v-if="!atBottom && !isEmpty"
        class="jump-latest"
        type="button"
        @click="scrollToBottom(true)"
      >
        <ArrowDown :size="13" />
        回到最新
      </button>
    </Transition>
  </div>
</template>

<script setup>
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from 'vue'
import { ArrowDown } from '@lucide/vue'
import EmptyState from './EmptyState.vue'
import MessageItem from './MessageItem.vue'
import ProcessTimeline from './ProcessTimeline.vue'
import TaskCard from './TaskCard.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  showProcess: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
  pending: { type: Boolean, default: false },
})

defineEmits(['suggest', 'open-task'])

const scrollerRef = ref(null)
const atBottom = ref(true)
const stick = ref(true)
let rafId = 0

function extractTaskId(text) {
  if (!text) return null
  try {
    const data = JSON.parse(text)
    return data?.task_id || data?.taskId || null
  } catch {
    return null
  }
}

const isEmpty = computed(
  () => !props.loading && props.messages.length === 0 && !props.pending,
)

const renderItems = computed(() => {
  const result = []
  let buffer = []

  const flush = () => {
    if (!buffer.length) return
    result.push({
      type: 'process',
      key: `process-${buffer[0].id || result.length}`,
      items: [...buffer],
    })
    buffer = []
  }

  for (const message of props.messages) {
    if (message.role === 'tool') {
      const taskId = extractTaskId(message.text)
      if (
        message.tool_name === 'start_procurement_task' &&
        (taskId || message.tool_status === 'calling')
      ) {
        flush()
        result.push({
          type: 'task',
          key: `task-${message.id || result.length}`,
          taskId,
        })
        continue
      }
      if (props.showProcess) buffer.push(message)
      continue
    }
    flush()
    if (message.role === 'user' || message.role === 'assistant') {
      result.push({
        type: 'message',
        key: message.id || `${message.role}-${result.length}`,
        message,
      })
    }
  }
  flush()
  return result
})

const lastAssistant = computed(() => {
  for (let index = props.messages.length - 1; index >= 0; index -= 1) {
    if (props.messages[index].role === 'assistant') return props.messages[index]
  }
  return null
})

const lastSignature = computed(() => {
  const last = props.messages[props.messages.length - 1]
  if (!last) return ''
  return [
    last.id,
    last.role,
    last.content?.length || 0,
    last.text?.length || 0,
    last.args?.length || 0,
    last.tool_status || '',
    last.images?.length || 0,
  ].join(':')
})

function isStreamingForMessage(message) {
  return props.streaming && message === lastAssistant.value
}

function onScroll() {
  const el = scrollerRef.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  atBottom.value = distance < 24
  stick.value = distance < 80
}

function scrollToBottom(smooth = false) {
  const el = scrollerRef.value
  if (!el) return
  stick.value = true
  atBottom.value = true
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

function scheduleScroll() {
  if (!stick.value) return
  if (rafId) cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(() => {
    rafId = 0
    if (!stick.value) return
    const el = scrollerRef.value
    if (!el) return
    el.scrollTop = el.scrollHeight
    atBottom.value = true
  })
}

watch(
  () => props.messages.length,
  async () => {
    const last = props.messages[props.messages.length - 1]
    if (last?.role === 'user') stick.value = true
    await nextTick()
    scheduleScroll()
  },
)

watch(lastSignature, async () => {
  await nextTick()
  scheduleScroll()
})

watch(
  () => props.pending,
  async (value) => {
    if (!value) return
    stick.value = true
    await nextTick()
    scheduleScroll()
  },
)

onMounted(async () => {
  await nextTick()
  scrollToBottom(false)
})

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId)
})

defineExpose({ scrollToBottom })
</script>

<style scoped>
.chat-area {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: var(--surface);
}

.scroller {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
}

.stream {
  width: 100%;
  max-width: var(--reading-w);
  margin: 0 auto;
  padding: 24px 24px 8px;
}

.empty-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 24px 48px;
}

.stream-end {
  height: 18px;
}

/* 回到最新 */
.jump-latest {
  position: absolute;
  left: 50%;
  bottom: 14px;
  z-index: 4;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface);
  color: var(--ink-2);
  font-size: 11.5px;
  font-weight: 550;
  box-shadow: var(--shadow-sm);
  transform: translateX(-50%);
}

.jump-latest:hover {
  border-color: var(--line-strong);
  color: var(--ink);
}

.jump-enter-active,
.jump-leave-active {
  transition: opacity var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
}

.jump-enter-from,
.jump-leave-to {
  opacity: 0;
  transform: translate(-50%, 4px);
}

/* 骨架屏 */
.stream-skeleton {
  padding-top: 40px;
}

.sk-user {
  width: 38%;
  height: 42px;
  margin-left: auto;
  margin-bottom: 28px;
  border-radius: 14px 14px 5px 14px;
  background: var(--surface-2);
  animation: shimmer 1.6s ease-in-out infinite;
}

.sk-user.short {
  width: 28%;
  height: 36px;
}

.sk-assistant {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-bottom: 34px;
}

.sk-line {
  height: 10px;
  border-radius: 5px;
  background: var(--surface-2);
  animation: shimmer 1.6s ease-in-out infinite;
}

@keyframes shimmer {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .stream {
    padding: 16px 16px 4px;
  }

  .empty-wrap {
    padding: 20px 16px 32px;
  }
}
</style>
