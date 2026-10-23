<template>
  <aside
    class="sidebar"
    :class="{ open }"
    aria-label="会话历史"
    :inert="isMobile && !open"
    :aria-hidden="isMobile && !open ? 'true' : null"
  >
    <div class="sidebar-head">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">
          <Boxes :size="17" :stroke-width="2" />
        </div>
        <div class="brand-copy">
          <strong>采购助手</strong>
          <span>ERP WORKSPACE</span>
        </div>
        <button
          class="icon-btn close-mobile"
          type="button"
          aria-label="关闭会话列表"
          @click="$emit('close')"
        >
          <X :size="17" />
        </button>
      </div>

      <button class="new-chat" type="button" @click="$emit('new-chat')">
        <Plus :size="16" />
        <span>新建对话</span>
        <kbd class="kbd" aria-hidden="true">⌘⇧O</kbd>
      </button>

      <div class="search">
        <Search :size="15" class="search-icon" aria-hidden="true" />
        <input
          ref="searchInput"
          v-model="keyword"
          class="search-input"
          type="text"
          placeholder="搜索对话"
          aria-label="搜索对话"
          @keydown.esc="keyword = ''"
        />
        <button
          v-if="keyword"
          class="search-clear"
          type="button"
          aria-label="清空搜索"
          @click="keyword = ''"
        >
          <X :size="13" />
        </button>
        <kbd v-else class="kbd search-kbd" aria-hidden="true">⌘K</kbd>
      </div>
    </div>

    <nav class="sidebar-body" aria-label="会话列表">
      <template v-if="loading">
        <div class="skeleton-group">
          <div class="skeleton-label" />
          <div v-for="n in 5" :key="n" class="skeleton-row">
            <span class="skeleton-line" :style="{ width: `${52 + ((n * 13) % 34)}%` }" />
          </div>
        </div>
      </template>

      <template v-else-if="filteredSessions.length === 0">
        <div class="sidebar-empty">
          <component :is="keyword ? SearchX : Inbox" :size="22" />
          <p>{{ keyword ? '没有找到相关对话' : '还没有对话' }}</p>
          <span>{{ keyword ? '换个关键词试试' : '新建一个对话开始工作' }}</span>
        </div>
      </template>

      <template v-else>
        <section v-for="group in groups" :key="group.key" class="session-group">
          <h2 class="group-label">{{ group.label }}</h2>
          <div class="group-items">
            <div
              v-for="session in group.items"
              :key="session.thread_id"
              class="session-row"
              :class="{ active: session.thread_id === currentThreadId }"
            >
              <button
                class="session-main"
                type="button"
                :aria-current="session.thread_id === currentThreadId ? 'true' : undefined"
                :title="session.title"
                @click="$emit('select-session', session.thread_id)"
              >
                <span class="session-title">{{ session.title || '未命名对话' }}</span>
                <span class="session-time">{{ formatRelativeTime(session.updated_at) }}</span>
              </button>

              <DropdownMenuRoot>
                <DropdownMenuTrigger as-child>
                  <button class="row-menu" type="button" aria-label="会话操作">
                    <MoreHorizontal :size="15" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuPortal>
                  <DropdownMenuContent class="menu-content" :side-offset="4" align="end">
                    <DropdownMenuItem
                      class="menu-item danger"
                      @select="requestDelete(session)"
                    >
                      <Trash2 :size="14" />
                      删除对话
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenuPortal>
              </DropdownMenuRoot>
            </div>
          </div>
        </section>
      </template>
    </nav>

    <div class="sidebar-foot">
      <div class="workspace-pill">
        <span class="workspace-dot" />
        本地工作区
      </div>
      <span class="version">v1.1</span>
    </div>

    <AlertDialogRoot
      :open="!!pendingDelete"
      @update:open="(value) => { if (!value) pendingDelete = null }"
    >
      <AlertDialogPortal>
        <AlertDialogOverlay class="ds-overlay" />
        <AlertDialogContent class="ds-dialog delete-dialog">
          <div class="delete-icon" aria-hidden="true">
            <Trash2 :size="18" />
          </div>
          <AlertDialogTitle class="delete-title">删除这个对话？</AlertDialogTitle>
          <AlertDialogDescription class="delete-desc">
            “{{ pendingDelete?.title || '未命名对话' }}” 的会话记录将被永久移除，无法恢复。
          </AlertDialogDescription>
          <div class="delete-actions">
            <AlertDialogCancel class="btn btn-ghost">取消</AlertDialogCancel>
            <AlertDialogAction class="btn btn-danger" @click="confirmDelete">
              删除
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialogPortal>
    </AlertDialogRoot>
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import {
  Boxes,
  Inbox,
  MoreHorizontal,
  Plus,
  Search,
  SearchX,
  Trash2,
  X,
} from '@lucide/vue'
import {
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuRoot,
  DropdownMenuTrigger,
} from 'reka-ui'
import { formatRelativeTime } from '../utils/format'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentThreadId: { type: String, default: null },
  loading: { type: Boolean, default: false },
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['select-session', 'new-chat', 'delete-session', 'close'])

const isMobile = useMediaQuery('(max-width: 1023px)')
const keyword = ref('')
const searchInput = ref(null)
const pendingDelete = ref(null)

const filteredSessions = computed(() => {
  const list = props.sessions || []
  const query = keyword.value.trim().toLowerCase()
  if (!query) return list
  return list.filter((session) =>
    String(session.title || '').toLowerCase().includes(query),
  )
})

const groups = computed(() => {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const todayStart = startOfToday.getTime()
  const day = 24 * 60 * 60 * 1000

  const buckets = [
    { key: 'today', label: '今天', items: [] },
    { key: 'yesterday', label: '昨天', items: [] },
    { key: 'week', label: '过去 7 天', items: [] },
    { key: 'older', label: '更早', items: [] },
  ]

  for (const session of filteredSessions.value) {
    const time = new Date(session.updated_at || 0).getTime()
    if (Number.isNaN(time) || time < todayStart - 7 * day) {
      buckets[3].items.push(session)
    } else if (time >= todayStart) {
      buckets[0].items.push(session)
    } else if (time >= todayStart - day) {
      buckets[1].items.push(session)
    } else {
      buckets[2].items.push(session)
    }
  }

  return buckets.filter((bucket) => bucket.items.length > 0)
})

function requestDelete(session) {
  pendingDelete.value = session
}

function confirmDelete() {
  if (!pendingDelete.value) return
  emit('delete-session', pendingDelete.value.thread_id)
  pendingDelete.value = null
}

function focusSearch() {
  searchInput.value?.focus()
  searchInput.value?.select()
}

defineExpose({ focusSearch })
</script>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: var(--sidebar-w);
  height: 100%;
  flex-shrink: 0;
  border-right: 1px solid var(--line);
  background: var(--canvas);
}

/* 头部 */
.sidebar-head {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 12px 12px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 2px 6px 4px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--on-accent);
  box-shadow: var(--shadow-xs);
}

.brand-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
  min-width: 0;
}

.brand-copy strong {
  color: var(--ink);
  font-size: 13.5px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.brand-copy span {
  color: var(--ink-3);
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.1em;
}

.close-mobile {
  display: none;
  margin-left: auto;
}

.new-chat {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 38px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink);
  font-size: 13px;
  font-weight: 550;
  box-shadow: var(--shadow-xs);
  transition: border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease);
}

.new-chat:hover {
  border-color: color-mix(in srgb, var(--accent) 30%, var(--line));
  background: var(--accent-softer);
}

.new-chat svg {
  color: var(--accent);
}

.new-chat .kbd {
  margin-left: auto;
  opacity: 0.8;
}

/* 搜索 */
.search {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 8px 0 10px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: var(--canvas-deep);
  transition: background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease);
}

.search:focus-within {
  border-color: var(--line-strong);
  background: var(--surface);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.search-icon {
  flex-shrink: 0;
  color: var(--ink-3);
}

.search-input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
  font-size: 12.5px;
}

.search-input::placeholder {
  color: var(--ink-3);
}

.search-kbd {
  flex-shrink: 0;
}

.search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 5px;
  color: var(--ink-3);
}

.search-clear:hover {
  background: var(--surface-2);
  color: var(--ink);
}

/* 会话列表 */
.sidebar-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 8px 12px;
}

.session-group + .session-group {
  margin-top: 14px;
}

.group-label {
  padding: 6px 8px 4px;
  color: var(--ink-3);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.group-items {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.session-row {
  position: relative;
  display: flex;
  align-items: center;
  border-radius: var(--r-xs);
  transition: background var(--dur-fast) var(--ease);
}

.session-row:hover {
  background: var(--surface-2);
}

.session-row.active {
  background: var(--surface);
  box-shadow: inset 0 0 0 1px var(--line), var(--shadow-xs);
}

.session-row.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  bottom: 9px;
  width: 2px;
  border-radius: 2px;
  background: var(--accent);
}

.session-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 6px 0 10px;
  border-radius: var(--r-xs);
  text-align: left;
}

.session-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12.8px;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.session-row.active .session-title {
  color: var(--ink);
  font-weight: 550;
}

.session-time {
  flex-shrink: 0;
  color: var(--ink-3);
  font-size: 10.5px;
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease);
}

.session-row:hover .session-time,
.session-row.active .session-time,
.session-row:focus-within .session-time {
  opacity: 1;
}

.row-menu {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  border-radius: 6px;
  color: var(--ink-3);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease);
}

.session-row:hover .row-menu,
.session-row:focus-within .row-menu,
.row-menu[data-state='open'] {
  opacity: 1;
}

.row-menu:hover {
  background: var(--surface-3);
  color: var(--ink);
}

/* 骨架屏 */
.skeleton-group {
  padding: 8px 4px;
}

.skeleton-label {
  width: 44px;
  height: 8px;
  margin: 6px 6px 10px;
  border-radius: 4px;
  background: var(--surface-3);
  animation: shimmer 1.6s ease-in-out infinite;
}

.skeleton-row {
  display: flex;
  align-items: center;
  height: 36px;
  padding: 0 8px;
}

.skeleton-line {
  display: block;
  height: 9px;
  border-radius: 4px;
  background: var(--surface-3);
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

/* 空状态 */
.sidebar-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 48px 16px;
  color: var(--ink-3);
  text-align: center;
}

.sidebar-empty p {
  margin-top: 6px;
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 550;
}

.sidebar-empty span {
  font-size: 11.5px;
}

/* 底部 */
.sidebar-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px 12px;
  border-top: 1px solid var(--line-soft);
}

.workspace-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ink-3);
  font-size: 11px;
}

.workspace-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
}

.version {
  color: var(--ink-3);
  font-size: 10.5px;
  letter-spacing: 0.02em;
}

/* 删除确认 */
.delete-dialog {
  top: 50%;
  left: 50%;
  width: min(400px, calc(100vw - 32px));
  padding: 22px;
  border-radius: var(--r-lg);
  transform: translate(-50%, -50%);
  animation: dialogCenter var(--dur) var(--ease);
}

@keyframes dialogCenter {
  from {
    opacity: 0;
    transform: translate(-50%, calc(-50% + 6px)) scale(0.99);
  }
  to {
    opacity: 1;
    transform: translate(-50%, -50%) scale(1);
  }
}

.delete-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  margin-bottom: 12px;
  border-radius: 10px;
  background: var(--danger-soft);
  color: var(--danger);
}

.delete-title {
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

.delete-desc {
  margin-top: 6px;
  color: var(--ink-2);
  font-size: 12.5px;
  line-height: 1.65;
}

.delete-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

@media (hover: none) {
  .row-menu,
  .session-time {
    opacity: 1;
  }
}

/* 移动端 */
@media (max-width: 1023px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 55;
    width: min(300px, 86vw);
    border-right: 1px solid var(--line);
    box-shadow: var(--shadow-float);
    transform: translateX(-102%);
    transition: transform var(--dur-slow) var(--ease);
  }

  .sidebar.open {
    transform: translateX(0);
  }

  .close-mobile {
    display: inline-flex;
  }
}
</style>
