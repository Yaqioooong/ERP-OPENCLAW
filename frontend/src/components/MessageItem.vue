<template>
  <!-- 用户消息 -->
  <article v-if="isUser" class="turn turn-user">
    <div class="user-stack">
      <div class="user-bubble">{{ message.content }}</div>
      <div class="turn-actions">
        <span v-if="time" class="turn-time">{{ time }}</span>
        <button
          class="act-btn"
          type="button"
          :aria-label="copied ? '已复制' : '复制消息'"
          :title="copied ? '已复制' : '复制消息'"
          @click="copyContent"
        >
          <Check v-if="copied" :size="13" />
          <Copy v-else :size="13" />
        </button>
      </div>
    </div>
  </article>

  <!-- 助手消息 -->
  <article
    v-else
    class="turn turn-assistant"
    :class="{ 'is-sub': subAgent, 'is-streaming': isStreaming, 'is-error': message.is_error }"
  >
    <div v-if="subAgent" class="agent-line">
      <component :is="agentIcon" :size="13" :stroke-width="1.9" />
      <span>{{ agentName }}</span>
      <span v-if="message.is_error" class="agent-error">生成失败</span>
    </div>

    <div class="assistant-body">
      <div v-if="isStreaming && !message.content" class="thinking" aria-label="正在思考">
        <span class="thinking-dot" />
        <span class="thinking-dot" />
        <span class="thinking-dot" />
        <span class="thinking-text">正在思考</span>
      </div>
      <template v-else>
        <MarkdownRenderer :content="message.content || ''" />
        <span v-if="isStreaming && message.content" class="stream-cursor" aria-hidden="true" />
      </template>
    </div>

    <div v-if="!isStreaming && !message.is_error" class="turn-actions">
      <button
        class="act-btn"
        type="button"
        :aria-label="copied ? '已复制' : '复制回答'"
        :title="copied ? '已复制' : '复制回答'"
        @click="copyContent"
      >
        <Check v-if="copied" :size="13" />
        <Copy v-else :size="13" />
      </button>
      <span v-if="time" class="turn-time">{{ time }}</span>
    </div>
  </article>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Check, Copy } from '@lucide/vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { useToast } from '../composables/useToast'
import { formatClock } from '../utils/format'
import { isSubAgent, sourceIcon, sourceName } from '../utils/tools'

const props = defineProps({
  message: { type: Object, required: true },
  isStreaming: { type: Boolean, default: false },
})

const { toast } = useToast()
const copied = ref(false)

const isUser = computed(() => props.message.role === 'user')
const subAgent = computed(() => isSubAgent(props.message.source))
const agentName = computed(() => sourceName(props.message.source))
const agentIcon = computed(() => sourceIcon(props.message.source))
const time = computed(() => formatClock(props.message.created_at))

async function copyContent() {
  const text = props.message.content || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    toast('已复制到剪贴板', { tone: 'success' })
    window.setTimeout(() => {
      copied.value = false
    }, 1600)
  } catch {
    toast('复制失败，请手动选择', { tone: 'danger' })
  }
}
</script>

<style scoped>
.turn {
  animation: fadeUp var(--dur) var(--ease);
}

/* 用户 */
.turn-user {
  display: flex;
  justify-content: flex-end;
  padding: 6px 0;
}

.user-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  max-width: 82%;
}

.user-bubble {
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: 16px 16px 5px 16px;
  background: var(--surface-2);
  color: var(--ink);
  font-size: 14.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

/* 助手 */
.turn-assistant {
  position: relative;
  padding: 14px 0 6px;
}

.assistant-body {
  min-width: 0;
  color: var(--ink);
}

.agent-line {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
  padding-left: 10px;
  border-left: 2px solid var(--line-strong);
  color: var(--ink-2);
  font-size: 11.5px;
  font-weight: 550;
}

.agent-line svg {
  color: var(--ink-3);
}

.agent-error {
  color: var(--danger);
}

.is-sub .assistant-body {
  padding-left: 10px;
  border-left: 2px solid var(--line-strong);
}

.is-error .assistant-body {
  padding: 10px 14px;
  border: 1px solid color-mix(in srgb, var(--danger) 24%, var(--line));
  border-radius: var(--r-sm);
  background: var(--danger-soft);
}

/* 操作条 */
.turn-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease);
}

.turn-user .turn-actions {
  justify-content: flex-end;
}

.turn-assistant:hover .turn-actions,
.turn-assistant:focus-within .turn-actions,
.turn-user:hover .turn-actions,
.turn-user:focus-within .turn-actions {
  opacity: 1;
}

.act-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  color: var(--ink-3);
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.act-btn:hover {
  background: var(--surface-2);
  color: var(--ink-2);
}

.turn-time {
  color: var(--ink-3);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

/* 思考中 */
.thinking {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  color: var(--ink-3);
  font-size: 12.5px;
}

.thinking-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ink-3);
  animation: thinkBounce 1.3s var(--ease) infinite;
}

.thinking-dot:nth-child(2) {
  animation-delay: 0.15s;
}

.thinking-dot:nth-child(3) {
  animation-delay: 0.3s;
}

.thinking-text {
  margin-left: 5px;
}

/* 流式光标 */
.stream-cursor {
  display: inline-block;
  width: 2px;
  height: 1.05em;
  margin-left: 2px;
  border-radius: 1px;
  background: var(--accent);
  vertical-align: -0.16em;
  animation: cursorBlink 1.1s step-end infinite;
}

@keyframes thinkBounce {
  0%,
  60%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@keyframes cursorBlink {
  0%,
  55% {
    opacity: 1;
  }
  56%,
  100% {
    opacity: 0;
  }
}

@media (hover: none) {
  .turn-actions {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .user-stack {
    max-width: 92%;
  }

  .turn-user {
    padding: 4px 0;
  }

  .turn-assistant {
    padding-top: 10px;
  }
}
</style>
