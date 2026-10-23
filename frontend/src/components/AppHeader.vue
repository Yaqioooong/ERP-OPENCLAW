<template>
  <header class="app-header">
    <div class="header-left">
      <button
        class="icon-btn mobile-only"
        type="button"
        aria-label="打开会话列表"
        title="会话列表"
        @click="$emit('toggle-sidebar')"
      >
        <PanelLeft :size="18" />
      </button>

      <div class="title-block">
        <h1 class="session-title">{{ title || '新对话' }}</h1>
        <span class="status" :class="`status-${status.tone}`">
          <span class="status-dot" />
          {{ status.text }}
        </span>
      </div>
    </div>

    <div class="header-actions">
      <button
        class="icon-btn"
        type="button"
        :aria-pressed="showProcess"
        :title="showProcess ? '隐藏执行过程' : '显示执行过程'"
        aria-label="显示或隐藏执行过程"
        @click="$emit('toggle-process')"
      >
        <ListTree :size="17" />
      </button>

      <button
        class="task-entry"
        type="button"
        :class="{ active: hasActiveTask }"
        :disabled="!hasActiveTask"
        :title="
          hasActiveTask
            ? taskPending
              ? '采购任务待审批'
              : '采购任务面板'
            : '当前对话暂无采购任务'
        "
        @click="$emit('open-task')"
      >
        <ClipboardList :size="16" />
        <span class="task-entry-label">采购任务</span>
        <span
          v-if="taskActive || taskPending"
          class="live-dot"
          :class="{ pending: taskPending }"
          :aria-label="taskPending ? '采购任务待处理' : '有进行中的任务'"
        />
      </button>

      <button
        class="icon-btn"
        type="button"
        :title="theme === 'dark' ? '切换到浅色' : '切换到深色'"
        :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
        @click="$emit('toggle-theme')"
      >
        <Sun v-if="theme === 'dark'" :size="17" />
        <Moon v-else :size="17" />
      </button>
    </div>
  </header>
</template>

<script setup>
import { ClipboardList, ListTree, Moon, PanelLeft, Sun } from '@lucide/vue'

defineProps({
  title: { type: String, default: '' },
  status: {
    type: Object,
    default: () => ({ tone: 'idle', text: '就绪' }),
  },
  showProcess: { type: Boolean, default: true },
  hasActiveTask: { type: Boolean, default: false },
  taskActive: { type: Boolean, default: false },
  taskPending: { type: Boolean, default: false },
  theme: { type: String, default: 'light' },
})

defineEmits(['toggle-process', 'open-task', 'toggle-theme', 'toggle-sidebar'])
</script>

<style scoped>
.app-header {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: var(--header-h);
  padding: 0 18px 0 24px;
  border-bottom: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface) 86%, transparent);
  backdrop-filter: blur(12px);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.title-block {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.session-title {
  max-width: 42vw;
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.005em;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: var(--r-full);
  color: var(--ink-3);
  font-size: 11.5px;
  white-space: nowrap;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-3);
}

.status-run {
  background: var(--accent-soft);
  color: var(--accent);
}

.status-run .status-dot {
  background: var(--accent);
  animation: statusPulse 1.6s var(--ease) infinite;
}

.status-warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.status-warn .status-dot {
  background: var(--warn);
}

.status-done {
  background: var(--success-soft);
  color: var(--success);
}

.status-done .status-dot {
  background: var(--success);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.task-entry {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  margin: 0 2px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 550;
  transition: background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.task-entry:hover:not(:disabled) {
  border-color: var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
}

.task-entry:disabled {
  border-color: transparent;
  background: transparent;
  color: var(--ink-3);
  opacity: 0.55;
  cursor: not-allowed;
}

.task-entry.active {
  border-color: color-mix(in srgb, var(--accent) 34%, var(--line));
  background: var(--accent-soft);
  color: var(--accent);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
}

.live-dot.pending {
  background: var(--warn);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn) 22%, transparent);
  animation: dotBlink 1.6s var(--ease) infinite;
}

@keyframes dotBlink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

.mobile-only {
  display: none;
}

@keyframes statusPulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.82);
  }
}

@media (max-width: 1023px) {
  .mobile-only {
    display: inline-flex;
  }
}

@media (max-width: 640px) {
  .app-header {
    padding: 0 10px 0 12px;
  }

  .session-title {
    max-width: 34vw;
  }

  .status {
    display: none;
  }

  .task-entry-label {
    display: none;
  }

  .task-entry {
    width: 32px;
    justify-content: center;
    padding: 0;
    border-color: transparent;
    background: transparent;
  }
}
</style>
