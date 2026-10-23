import { computed, watch } from 'vue'
import { useLocalStorage, usePreferredDark } from '@vueuse/core'

const THEME_KEY = 'erp-theme'
const stored = useLocalStorage(THEME_KEY, 'auto')
const prefersDark = usePreferredDark()

const resolved = computed(() =>
  stored.value === 'auto' ? (prefersDark.value ? 'dark' : 'light') : stored.value,
)

watch(
  resolved,
  (theme) => {
    if (typeof document === 'undefined') return
    document.documentElement.dataset.theme = theme
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#111110' : '#f6f5f1')
  },
  { immediate: true },
)

export function useTheme() {
  function toggle() {
    stored.value = resolved.value === 'dark' ? 'light' : 'dark'
  }

  function setTheme(value) {
    stored.value = value
  }

  return {
    preference: stored,
    theme: resolved,
    toggle,
    setTheme,
  }
}
