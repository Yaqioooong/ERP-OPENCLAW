<template>
  <section class="task-card" :class="tone">
    <div class="task-card-icon" aria-hidden="true">
      <ClipboardList :size="16" />
    </div>

    <div class="task-card-body">
      <div class="task-card-top">
        <span class="task-card-kicker">采购任务</span>
        <span class="task-card-status">
          <span class="task-dot" :class="{ pulse }" />
          {{ statusLabel }}
        </span>
      </div>

      <p class="task-card-summary">{{ summary }}</p>

      <div class="task-card-meta">
        <code v-if="taskId">{{ shortId(taskId, 9, 4) }}</code>
        <span v-else>正在创建…</span>
        <span v-if="awaiting" class="task-card-hint">审批在任务面板中完成</span>
      </div>
    </div>

    <button
      type="button"
      class="task-card-btn"
      :class="awaiting ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'"
      :disabled="!taskId"
      @click="taskId && $emit('open', taskId)"
    >
      {{ actionLabel }}
      <ArrowUpRight :size="12" />
    </button>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { ArrowUpRight, ClipboardList } from '@lucide/vue'
import { useTaskStore } from '../composables/useTaskStore'
import { formatCurrency, shortId } from '../utils/format'

const props = defineProps({
  taskId: { type: String, default: null },
})

defineEmits(['open'])

const store = useTaskStore()

const isCurrent = computed(
  () => !!props.taskId && store.activeTaskId.value === props.taskId,
)
const current = computed(() => (isCurrent.value ? store.task.value : null))
const status = computed(() => current.value?.status || (props.taskId ? 'created' : 'creating'))

const STATUS = {
  creating: '创建中',
  created: '已创建',
  running: '分析中',
  parsing: '解析需求',
  analyzing: '分析中',
  need_info: '待补充',
  awaiting_approval: '待审批',
  awaiting_retry: '待恢复',
  approved_pending_execution: '已批准',
  executing: '下单中',
  verifying: '核验中',
  completed: '已完成',
  failed: '失败',
  rejected: '已拒绝',
  cancelled: '已取消',
}

const statusLabel = computed(() => STATUS[status.value] || '已创建')
const awaiting = computed(() => status.value === 'awaiting_approval')
const pending = computed(() => ['need_info', 'awaiting_retry'].includes(status.value))

const pulse = computed(() =>
  ['creating', 'running', 'parsing', 'analyzing', 'executing', 'verifying'].includes(
    status.value,
  ),
)

const tone = computed(() => {
  if (status.value === 'completed') return 'is-done'
  if (['failed', 'rejected', 'cancelled'].includes(status.value)) return 'is-error'
  if (awaiting.value || pending.value) return 'is-pending'
  return 'is-running'
})

const summary = computed(() => {
  const data = current.value
  const plan = data?.interrupt?.summary
  if (plan) {
    const bits = []
    if (plan.supplier_name) bits.push(plan.supplier_name)
    if (plan.total_amount) {
      bits.push(formatCurrency(plan.total_amount, plan.currency))
    }
    if (plan.items?.length) bits.push(`${plan.items.length} 项物料`)
    if (bits.length) return bits.join(' · ')
  }

  if (status.value === 'completed') {
    const orderId =
      data?.state_summary?.order_id || data?.state_summary?.order_number
    return orderId ? `订单已创建：${orderId}` : '采购流程已完成'
  }
  if (status.value === 'failed' || status.value === 'rejected' || status.value === 'cancelled') {
    return (
      data?.interrupt?.last_error?.message ||
      data?.state_summary?.error?.message ||
      '任务已结束，点击查看详情'
    )
  }
  if (status.value === 'need_info') return '还缺少采购条件，需要补充'
  if (status.value === 'awaiting_approval') return '方案已生成，等待你审批'
  if (status.value === 'awaiting_retry') return '任务中断，需要选择重试或取消'
  if (!props.taskId) return '正在把采购需求交给分析代理…'
  if (!isCurrent.value) return '点击查看任务进度与审批'
  return '正在分析需求，生成采购方案…'
})

const actionLabel = computed(() => {
  if (awaiting.value || pending.value) return '打开处理'
  if (status.value === 'completed') return '查看结果'
  return '查看进度'
})
</script>

<style scoped>
.task-card {
  display: flex;
  align-items: center;
  gap: 11px;
  margin: 12px 0 4px;
  padding: 12px 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  animation: fadeUp var(--dur) var(--ease);
}

.task-card.is-pending {
  border-color: color-mix(in srgb, var(--warn) 34%, var(--line));
  background: color-mix(in srgb, var(--warn-soft) 45%, var(--surface));
}

.task-card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
}

.task-card.is-pending .task-card-icon {
  background: var(--warn-soft);
  color: var(--warn);
}

.task-card.is-done .task-card-icon {
  background: var(--success-soft);
  color: var(--success);
}

.task-card.is-error .task-card-icon {
  background: var(--danger-soft);
  color: var(--danger);
}

.task-card-body {
  flex: 1;
  min-width: 0;
}

.task-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-card-kicker {
  color: var(--ink-2);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.task-card-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-3);
  font-size: 10px;
}

.task-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ink-3);
}

.task-dot.pulse {
  background: var(--accent);
  animation: taskPulse 1.6s var(--ease) infinite;
}

.task-card.is-pending .task-card-status {
  color: var(--warn);
}

.task-card.is-pending .task-dot {
  background: var(--warn);
}

.task-card.is-done .task-card-status {
  color: var(--success);
}

.task-card.is-done .task-dot {
  background: var(--success);
}

.task-card.is-error .task-card-status {
  color: var(--danger);
}

.task-card.is-error .task-dot {
  background: var(--danger);
}

.task-card-summary {
  margin-top: 1px;
  overflow: hidden;
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.task-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  color: var(--ink-3);
  font-size: 10px;
}

.task-card-meta code {
  font-family: var(--font-mono);
  font-size: 9.5px;
}

.task-card-hint {
  color: var(--warn);
}

.task-card-btn {
  flex-shrink: 0;
}

@keyframes taskPulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.4;
    transform: scale(0.8);
  }
}

@media (max-width: 640px) {
  .task-card {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .task-card-btn {
    width: 100%;
    margin-top: 2px;
  }

  .task-card-summary {
    white-space: normal;
  }
}
</style>
