<template>
  <Teleport to="body">
    <div class="toast-viewport" aria-live="polite" aria-atomic="false">
      <TransitionGroup name="toast" tag="div" class="toast-stack">
        <div
          v-for="item in toasts"
          :key="item.id"
          class="toast"
          :class="`toast-${item.tone}`"
          role="status"
        >
          <component :is="toneIcon(item.tone)" :size="15" class="toast-icon" />
          <div class="toast-copy">
            <p class="toast-message">{{ item.message }}</p>
            <p v-if="item.description" class="toast-desc">{{ item.description }}</p>
          </div>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup>
import { CircleCheck, Info, TriangleAlert } from '@lucide/vue'
import { useToast } from '../composables/useToast'

const { toasts } = useToast()

function toneIcon(tone) {
  if (tone === 'success') return CircleCheck
  if (tone === 'danger') return TriangleAlert
  return Info
}
</script>

<style scoped>
.toast-viewport {
  position: fixed;
  left: 50%;
  bottom: 150px;
  z-index: 90;
  transform: translateX(-50%);
  pointer-events: none;
}

.toast-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  max-width: min(420px, calc(100vw - 32px));
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow-float);
  pointer-events: auto;
}

.toast-icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--ink-2);
}

.toast-success .toast-icon {
  color: var(--success);
}

.toast-danger .toast-icon {
  color: var(--danger);
}

.toast-copy {
  min-width: 0;
}

.toast-message {
  font-size: 13px;
  line-height: 1.45;
}

.toast-desc {
  margin-top: 2px;
  color: var(--ink-3);
  font-size: 11.5px;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.98);
}

.toast-move {
  transition: transform var(--dur) var(--ease);
}
</style>
