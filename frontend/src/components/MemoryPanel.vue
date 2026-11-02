<template>
  <div class="memory-backdrop" role="presentation">
    <section class="memory-panel" role="dialog" aria-modal="true" aria-labelledby="memory-title">
      <header class="memory-head">
        <div>
          <h2 id="memory-title">我的记忆</h2>
          <p>这里只包含长期偏好，不包含采购任务、审批或订单</p>
        </div>
        <button class="icon-btn" type="button" aria-label="关闭" @click="$emit('close')">
          <X :size="18" />
        </button>
      </header>

      <p v-if="error" class="memory-error" role="alert">{{ error }}</p>

      <div v-if="loading" class="memory-empty">正在加载…</div>
      <template v-else-if="data">
        <section class="memory-block">
          <h3>长期偏好</h3>
          <dl class="preference-grid">
            <div v-for="item in preferredItems" :key="item.key">
              <dt>{{ item.label }}</dt>
              <dd>{{ item.value || '未设置' }}</dd>
            </div>
          </dl>
        </section>

        <section class="memory-block">
          <div class="block-head">
            <h3>近期供应商</h3>
            <span>{{ data.recent_suppliers.length }} / {{ data.limits.max_suppliers }}</span>
          </div>
          <ul v-if="data.recent_suppliers.length" class="memory-list">
            <li v-for="item in data.recent_suppliers" :key="item.value">
              <div>
                <strong>{{ item.value }}</strong>
                <span>最近使用 {{ formatDate(item.last_seen_at) }} · 过期 {{ formatDate(item.expires_at) }}</span>
              </div>
              <button class="forget-btn" type="button" :disabled="busy === item.value" @click="forget('recent_suppliers', item.value)">
                忘记
              </button>
            </li>
          </ul>
          <p v-else class="memory-empty">暂无近期供应商</p>
        </section>

        <section class="memory-block">
          <div class="block-head">
            <h3>近期查询</h3>
            <span>{{ data.recent_queries.length }} / {{ data.limits.max_queries }}</span>
          </div>
          <ul v-if="data.recent_queries.length" class="memory-list">
            <li v-for="item in data.recent_queries" :key="item.value">
              <div>
                <strong>{{ item.value }}</strong>
                <span>最近使用 {{ formatDate(item.last_seen_at) }} · 过期 {{ formatDate(item.expires_at) }}</span>
              </div>
              <button class="forget-btn" type="button" :disabled="busy === item.value" @click="forget('recent_queries', item.value)">
                忘记
              </button>
            </li>
          </ul>
          <p v-else class="memory-empty">暂无近期查询</p>
        </section>

        <footer class="memory-footer">
          <button class="clear-btn" type="button" :disabled="busy === 'clear'" @click="clearAll">
            清空全部记忆
          </button>
          <span>会删除显式偏好与近期记录，但不会影响采购任务/审批</span>
        </footer>
      </template>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { X } from '@lucide/vue'
import { clearMemory, deleteMemoryItem, getMemory } from '../api/memory.js'

defineEmits(['close'])

const loading = ref(true)
const busy = ref('')
const error = ref('')
const data = ref(null)

const preferredItems = computed(() => [
  { key: 'preferred_output', label: '输出格式', value: labelOutput(data.value?.preferred?.preferred_output) },
  { key: 'preferred_chart_type', label: '图表类型', value: data.value?.preferred?.preferred_chart_type },
  { key: 'preferred_currency', label: '货币', value: data.value?.preferred?.preferred_currency },
  { key: 'preferred_language', label: '语言', value: data.value?.preferred?.preferred_language },
])

function labelOutput(value) {
  if (value === 'table') return '表格'
  if (value === 'chart') return '图表'
  return value || ''
}

function formatDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await getMemory()
  } catch (exc) {
    error.value = exc.message || '加载记忆失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function forget(memoryType, value) {
  if (busy.value) return
  busy.value = value
  error.value = ''
  try {
    await deleteMemoryItem(memoryType, value)
    await load()
  } catch (exc) {
    error.value = exc.message || '删除记忆失败'
  } finally {
    busy.value = ''
  }
}

async function clearAll() {
  const confirmed = window.confirm(
    '清空全部记忆会删除显式偏好与近期供应商/查询，但不会影响采购任务、审批或订单。确定继续？',
  )
  if (!confirmed) return
  busy.value = 'clear'
  error.value = ''
  try {
    await clearMemory()
    await load()
  } catch (exc) {
    error.value = exc.message || '清空记忆失败'
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.memory-backdrop {
  position: fixed;
  inset: 0;
  z-index: 76;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--overlay);
  backdrop-filter: blur(3px);
}

.memory-panel {
  display: flex;
  flex-direction: column;
  width: min(680px, 100%);
  max-height: min(86vh, 760px);
  padding: 24px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg, 0 18px 60px rgba(15, 23, 42, 0.2));
}

.memory-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.memory-head h2 {
  margin: 0;
  color: var(--ink);
  font-size: 16px;
  font-weight: 650;
}

.memory-head p {
  margin: 4px 0 0;
  color: var(--ink-3);
  font-size: 12px;
}

.memory-block {
  margin-top: 18px;
}

.memory-block h3 {
  margin: 0 0 8px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--ink-3);
  font-size: 11px;
}

.preference-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}

.preference-grid div {
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-sm);
}

.preference-grid dt {
  color: var(--ink-3);
  font-size: 11px;
}

.preference-grid dd {
  margin: 3px 0 0;
  color: var(--ink);
  font-size: 12.5px;
}

.memory-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.memory-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-sm);
}

.memory-list strong {
  display: block;
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
}

.memory-list span {
  color: var(--ink-3);
  font-size: 11px;
}

.forget-btn {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink-2);
  font-size: 11.5px;
  cursor: pointer;
}

.forget-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.memory-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}

.clear-btn {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--danger, #dc2626);
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--danger, #dc2626);
  font-size: 12px;
  cursor: pointer;
}

.memory-footer span {
  color: var(--ink-3);
  font-size: 11px;
  line-height: 1.5;
}

.memory-error {
  margin: 12px 0 0;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  background: var(--danger-soft, #fee2e2);
  color: var(--danger, #dc2626);
  font-size: 12px;
}

.memory-empty {
  padding: 10px 0;
  color: var(--ink-3);
  font-size: 12px;
}
</style>
