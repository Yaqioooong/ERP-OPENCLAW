<template>
  <div class="composer-area">
    <div
      class="composer"
      :class="{ focused: isFocused, streaming }"
    >
      <textarea
        ref="textareaRef"
        class="composer-input"
        :value="modelValue"
        rows="1"
        placeholder="给采购助手发消息，或描述采购需求…"
        aria-label="输入消息"
        @input="onInput"
        @focus="isFocused = true"
        @blur="isFocused = false"
        @keydown="onKeydown"
      />

      <div class="composer-bar">
        <div class="composer-left">
          <button
            class="composer-tool"
            type="button"
            :aria-pressed="showProcess"
            title="显示或隐藏执行过程"
            @click="$emit('toggle-process')"
          >
            <ListTree :size="14" />
            <span>执行过程</span>
            <span class="tool-state">{{ showProcess ? '开' : '关' }}</span>
          </button>
        </div>

        <div class="composer-right">
          <span class="composer-hint">
            {{ streaming ? 'Esc 停止' : 'Enter 发送' }}
          </span>

          <button
            v-if="!streaming"
            class="send-btn"
            type="button"
            :disabled="!canSend"
            aria-label="发送消息"
            title="发送消息 (Enter)"
            @click="handleSend"
          >
            <ArrowUp :size="16" :stroke-width="2.2" />
          </button>

          <button
            v-else
            class="stop-btn"
            type="button"
            aria-label="停止生成"
            title="停止生成 (Esc)"
            @click="$emit('stop')"
          >
            <Square :size="10" fill="currentColor" />
          </button>
        </div>
      </div>
    </div>

    <p class="composer-footnote">
      <template v-if="streaming">
        正在生成，仍可继续输入 · <kbd class="kbd">Esc</kbd> 停止
      </template>
      <template v-else>
        <kbd class="kbd">Enter</kbd> 发送 · <kbd class="kbd">Shift + Enter</kbd> 换行 ·
        关键操作会先请你确认
      </template>
    </p>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ArrowUp, ListTree, Square } from '@lucide/vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  showProcess: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue', 'send', 'stop', 'toggle-process'])

const textareaRef = ref(null)
const isFocused = ref(false)

const canSend = computed(() => props.modelValue.trim().length > 0)

function autoResize() {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.min(textarea.scrollHeight, 168)}px`
}

function onInput(event) {
  emit('update:modelValue', event.target.value)
  autoResize()
}

function onKeydown(event) {
  if (event.key !== 'Enter') return
  if (event.isComposing) return
  if (event.shiftKey) return
  if (event.metaKey || event.ctrlKey) {
    event.preventDefault()
    handleSend()
    return
  }
  event.preventDefault()
  handleSend()
}

function handleSend() {
  if (props.streaming) return
  const text = props.modelValue.trim()
  if (!text) return
  emit('update:modelValue', '')
  emit('send', text)
  nextTick(() => {
    autoResize()
    textareaRef.value?.focus()
  })
}

function focus() {
  textareaRef.value?.focus()
}

defineExpose({ focus })

watch(
  () => props.modelValue,
  () => nextTick(autoResize),
)

onMounted(() => {
  autoResize()
})
</script>

<style scoped>
.composer-area {
  position: relative;
  flex-shrink: 0;
  padding: 10px 24px 14px;
  background: var(--surface);
}

.composer {
  max-width: var(--reading-w);
  margin: 0 auto;
  padding: 10px 10px 8px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  transition: border-color var(--dur-fast) var(--ease),
    box-shadow var(--dur-fast) var(--ease);
}

.composer.focused {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
  box-shadow: 0 0 0 3px var(--accent-ring), var(--shadow-sm);
}

.composer.streaming {
  border-color: color-mix(in srgb, var(--accent) 22%, var(--line));
}

.composer-input {
  display: block;
  width: 100%;
  max-height: 168px;
  padding: 2px 2px 4px;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
  font-size: 14.5px;
  line-height: 1.65;
  resize: none;
  overflow-y: auto;
}

.composer-input::placeholder {
  color: var(--ink-3);
}

.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
}

.composer-left,
.composer-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.composer-tool {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 7px;
  border-radius: 6px;
  color: var(--ink-3);
  font-size: 11.5px;
  font-weight: 500;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.composer-tool:hover {
  background: var(--surface-2);
  color: var(--ink-2);
}

.composer-tool[aria-pressed="true"] {
  background: var(--accent-soft);
  color: var(--accent);
}

.tool-state {
  font-size: 10px;
  opacity: 0.7;
}

.composer-hint {
  color: var(--ink-3);
  font-size: 10.5px;
  white-space: nowrap;
}

.send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--on-accent);
  transition: background var(--dur-fast) var(--ease), opacity var(--dur-fast) var(--ease),
    transform var(--dur-fast) var(--ease);
}

.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.send-btn:active:not(:disabled) {
  transform: scale(0.96);
}

.send-btn:disabled {
  background: var(--surface-3);
  color: var(--ink-3);
  cursor: not-allowed;
}

.stop-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--ink);
  color: var(--surface);
  transition: opacity var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
}

.stop-btn:hover {
  opacity: 0.86;
}

.stop-btn:active {
  transform: scale(0.96);
}

.composer-footnote {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  flex-wrap: wrap;
  max-width: var(--reading-w);
  margin: 7px auto 0;
  color: var(--ink-3);
  font-size: 10.5px;
  text-align: center;
}

.composer-footnote .kbd {
  height: 16px;
  font-size: 9.5px;
}

@media (max-width: 640px) {
  .composer-area {
    padding: 8px 12px 10px;
  }

  .composer {
    padding: 8px 8px 6px 12px;
    border-radius: var(--r-md);
  }

  .composer-hint {
    display: none;
  }

  .composer-footnote {
    font-size: 10px;
  }
}
</style>
