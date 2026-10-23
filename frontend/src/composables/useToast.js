import { ref } from 'vue'

const toasts = ref([])
let seq = 0

export function useToast() {
  function dismiss(id) {
    const index = toasts.value.findIndex((item) => item.id === id)
    if (index !== -1) toasts.value.splice(index, 1)
  }

  function toast(message, options = {}) {
    const now = Date.now()
    const duplicate = toasts.value.find(
      (item) => item.message === message && now - item.createdAt < 1800,
    )
    if (duplicate) return duplicate.id

    const id = ++seq
    const item = {
      id,
      message,
      description: options.description || '',
      tone: options.tone || 'default',
      createdAt: now,
    }
    toasts.value.push(item)
    if (toasts.value.length > 4) toasts.value.shift()
    const duration = options.duration ?? 2600
    if (duration > 0) window.setTimeout(() => dismiss(id), duration)
    return id
  }

  return { toasts, toast, dismiss }
}
