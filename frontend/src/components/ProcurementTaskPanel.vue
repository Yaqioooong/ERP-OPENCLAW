<template>
  <aside
    ref="sheetRef"
    class="task-sheet"
    role="dialog"
    aria-modal="false"
    aria-labelledby="task-sheet-title"
    aria-describedby="task-sheet-desc"
    tabindex="-1"
  >
    <h2 id="task-sheet-title" class="sr-only">采购任务</h2>
    <p id="task-sheet-desc" class="sr-only">
      查看采购任务状态、审批方案和任务事件
    </p>

    <div v-if="loading || refreshing" class="sheet-progress" aria-hidden="true">
      <span />
    </div>

    <header class="sheet-head">
      <div class="sheet-heading">
        <span class="sheet-eyebrow">PROCUREMENT TASK</span>
        <h2 class="sheet-title">采购任务</h2>
        <p class="sheet-subtitle">分析 → 审批 → 下单 → 核验</p>
      </div>
      <div class="sheet-head-actions">
        <button
          class="icon-btn"
          type="button"
          aria-label="刷新任务状态"
          title="刷新任务状态"
          :disabled="!task || refreshing"
          @click="manualRefresh"
        >
          <RefreshCw :size="16" :class="{ spinning: refreshing }" />
        </button>
        <button
          class="icon-btn"
          type="button"
          aria-label="关闭采购任务面板"
          @click="$emit('close')"
        >
          <X :size="18" />
        </button>
      </div>
    </header>

    <div class="sheet-body">
      <div v-if="!task" class="sheet-empty">
        <div class="sheet-empty-icon" aria-hidden="true">
          <ClipboardList :size="22" />
        </div>
        <h3>当前对话还没有采购任务</h3>
        <p>
          在对话中描述采购需求，Agent 会创建任务；方案生成后，这里会出现审批按钮。
        </p>
        <button type="button" class="btn btn-ghost" @click="$emit('close')">
          返回对话
        </button>
      </div>

      <div v-else class="task-view">
        <div class="task-meta">
          <span class="status-badge" :class="statusClass">
            <span class="status-dot" />
            {{ statusLabel }}
          </span>
          <button type="button" class="task-id" title="复制任务 ID" @click="copyTaskId">
            {{ task.task_id }}
            <Copy :size="11" />
          </button>
        </div>

        <div class="task-origin">
          <MessageSquare :size="13" />
          <span class="task-origin-label">来源对话</span>
          <code v-if="task.conversation_id" class="task-origin-id">
            {{ shortConversationId }}
          </code>
          <span v-else class="task-origin-muted">未关联对话</span>
          <button
            v-if="task.conversation_available"
            type="button"
            class="task-origin-link"
            @click="openOriginConversation"
          >
            返回对话
            <ArrowLeft :size="12" />
          </button>
          <span v-else-if="task.conversation_id" class="task-origin-muted">
            不可访问
          </span>
        </div>

        <ol class="steps" aria-label="任务进度">
          <li
            v-for="(step, index) in steps"
            :key="step.key"
            class="step"
            :class="stepStates[index]"
          >
            <span class="step-dot">
              <Check v-if="stepStates[index] === 'done'" :size="10" />
              <span v-else>{{ index + 1 }}</span>
            </span>
            <span class="step-label">{{ step.label }}</span>
          </li>
        </ol>

        <div v-if="failureMessage" class="hitl-callout danger failure-callout">
          <TriangleAlert :size="14" />
          <div>
            <strong>任务未完成</strong>
            <span v-if="failureCode" class="failure-code">{{ failureCode }}</span>
            <p>{{ failureMessage }}</p>
            <ul v-if="failureDetails.length" class="failure-details">
              <li v-for="(detail, index) in failureDetails" :key="`${detail.code}-${index}`">
                <span v-if="detail.code" class="failure-detail-code">{{ detail.code }}</span>
                <span>{{ detail.message }}</span>
              </li>
            </ul>
            <button type="button" class="btn btn-ghost btn-sm" @click="$emit('close')">
              关闭并在对话中重新发起
            </button>
          </div>
        </div>

        <div v-if="cancelledMessage" class="hitl-callout warn">
          <TriangleAlert :size="14" />
          <div>
            <strong>任务已取消</strong>
            <p>{{ cancelledMessage }}</p>
          </div>
        </div>

        <div v-if="approval" class="panel-section approval-section">
          <div class="panel-section-head">
            <span class="panel-section-title">待审批方案</span>
            <span
              v-if="approval.plan_version || task.interrupt?.plan_version"
              class="chip"
            >
              方案 v{{ approval.plan_version || task.interrupt?.plan_version }}
            </span>
          </div>

          <div class="approval-top">
            <div class="approval-amount">
              <span>方案总金额</span>
              <strong>{{ formatCurrency(totalAmount, approval.currency) }}</strong>
              <em v-if="quantitiesChanged">
                原方案 {{ formatCurrency(Number(approval.total_amount), approval.currency) }}
              </em>
            </div>
            <dl class="hitl-kv approval-facts">
              <dt>供应商</dt>
              <dd>{{ approval.supplier_name || approval.supplier_id || '—' }}</dd>
              <dt>收货仓库</dt>
              <dd>{{ approval.warehouse_location || '—' }}</dd>
              <dt>预计交货</dt>
              <dd>{{ formatDate(approval.expected_delivery_date) || '—' }}</dd>
            </dl>
          </div>

          <div class="hitl-table-wrap">
            <table class="hitl-table approval-table">
              <thead>
                <tr>
                  <th>物料</th>
                  <th class="num">数量</th>
                  <th class="num">小计</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in approvalItems" :key="index">
                  <td>
                    <span class="item-name">{{ item.part_name || `物料 #${item.part_id}` }}</span>
                    <span class="item-price">{{ formatCurrency(item.unit_price, approval.currency) }} / 件</span>
                    <span class="item-id">#{{ item.part_id }}</span>
                  </td>
                  <td class="num">
                    <div class="hitl-qty">
                      <button
                        type="button"
                        aria-label="减少数量"
                        :disabled="loading"
                        @click="stepQty(index, -1)"
                      >
                        −
                      </button>
                      <input
                        v-model.number="quantities[index]"
                        type="number"
                        min="1"
                        aria-label="数量"
                        :disabled="loading"
                      />
                      <button
                        type="button"
                        aria-label="增加数量"
                        :disabled="loading"
                        @click="stepQty(index, 1)"
                      >
                        +
                      </button>
                    </div>
                  </td>
                  <td class="num">{{ formatCurrency(lineTotal(item, index), approval.currency) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <td colspan="3">合计</td>
                  <td class="num">{{ formatCurrency(totalAmount, approval.currency) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div v-if="inventoryGaps.length" class="hitl-callout warn" style="margin-top: 10px">
            <TriangleAlert :size="14" />
            <div>
              <strong>库存提示</strong>
              <div class="gap-list">
                <span v-for="(gap, index) in inventoryGaps" :key="index">{{ gap }}</span>
              </div>
            </div>
          </div>

          <details v-if="approval.rationale" class="hitl-details" style="margin-top: 10px">
            <summary><Sparkles :size="13" /> 选型理由</summary>
            <div class="hitl-details-body">
              <MarkdownRenderer :content="approval.rationale" />
            </div>
          </details>

          <label class="field" style="margin-top: 10px">
            <span>审批意见（可选）</span>
            <textarea
              v-model="approvalComment"
              rows="2"
              placeholder="例如：报价合理，同意下单"
              :disabled="loading"
            />
          </label>

          <p class="hitl-note">
            审批是唯一的执行入口。提交前会自动校验方案版本，若方案已被更新会要求重新确认。
          </p>
          <p v-if="quantitiesChanged" class="hitl-note warn-text">
            数量已调整，保存后会重新生成方案并再次审批，不会直接下单。
          </p>

          <div v-if="rejectOpen" class="hitl-confirm">
            <div class="hitl-confirm-head">
              <TriangleAlert :size="14" />
              确认拒绝这个方案？
            </div>
            <p>拒绝后任务会结束，不会创建订单。</p>
            <div class="hitl-confirm-actions">
              <button type="button" class="btn btn-ghost btn-sm" @click="rejectOpen = false">
                取消
              </button>
              <button
                type="button"
                class="btn btn-danger btn-sm"
                :disabled="loading"
                @click="submitDecision('reject')"
              >
                确认拒绝
              </button>
            </div>
          </div>

          <div v-else class="panel-actions">
            <button
              type="button"
              class="btn btn-danger-ghost"
              :disabled="loading"
              @click="rejectOpen = true"
            >
              拒绝
            </button>
            <button
              v-if="quantitiesChanged"
              type="button"
              class="btn btn-primary"
              :disabled="loading"
              @click="submitDecision('modify')"
            >
              <span v-if="loading" class="spinner" aria-hidden="true" />
              {{ loading ? '正在提交…' : '保存修改并重新审批' }}
            </button>
            <button
              v-else
              type="button"
              class="btn btn-primary"
              :disabled="loading"
              @click="submitDecision('approve')"
            >
              <span v-if="loading" class="spinner" aria-hidden="true" />
              {{ loading ? '正在提交…' : '同意并创建订单' }}
            </button>
          </div>
        </div>

        <div v-if="retryInterrupt" class="panel-section">
          <div class="panel-section-head">
            <span class="panel-section-title">任务需要恢复</span>
          </div>
          <div class="hitl-callout danger">
            <TriangleAlert :size="14" />
            <div>
              <strong>{{ retryInterrupt.last_error?.code || '执行中断' }}</strong>
              <p>{{ retryInterrupt.last_error?.message || '请重试或取消任务。' }}</p>
            </div>
          </div>

          <div v-if="cancelOpen" class="hitl-confirm">
            <div class="hitl-confirm-head">
              <TriangleAlert :size="14" />
              确认取消任务？
            </div>
            <p>取消后任务终止，无法再恢复。</p>
            <div class="hitl-confirm-actions">
              <button type="button" class="btn btn-ghost btn-sm" @click="cancelOpen = false">
                返回
              </button>
              <button
                type="button"
                class="btn btn-danger btn-sm"
                :disabled="loading"
                @click="resumeRetry('cancel')"
              >
                确认取消
              </button>
            </div>
          </div>

          <div v-else class="panel-actions">
            <button
              type="button"
              class="btn btn-danger-ghost"
              :disabled="loading"
              @click="cancelOpen = true"
            >
              取消任务
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="loading"
              @click="resumeRetry('retry')"
            >
              重试
            </button>
          </div>
        </div>

        <div v-if="requiresReview" class="panel-section review-section">
          <div class="panel-section-head">
            <span class="panel-section-title">订单结果需要人工复核</span>
          </div>
          <div class="hitl-callout warn">
            <TriangleAlert :size="14" />
            <div>
              <strong>{{ reviewReason.code || '订单结果不确定' }}</strong>
              <p>{{ reviewReason.message || '后台对账未能确认订单是否与方案一致。' }}</p>
            </div>
          </div>
          <p class="hitl-note">
            执行记录：{{ reviewKey || '未提供' }}
            <span v-if="currentOrderId"> · 当前订单：{{ currentOrderId }}</span>
          </p>
          <label class="hitl-note review-field">
            远端订单号
            <input
              v-model="reviewOrderId"
              type="text"
              placeholder="确认已创建时填写，例如 PO20261105001"
            />
          </label>
          <label class="hitl-note review-field">
            备注
            <input v-model="reviewComment" type="text" placeholder="可选" />
          </label>
          <div class="panel-actions">
            <button
              type="button"
              class="btn btn-danger-ghost"
              :disabled="reviewLoading || !reviewKey"
              @click="submitReview('mark_failed')"
            >
              确认未创建
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="reviewLoading || !reviewKey"
              @click="submitReview('mark_succeeded')"
            >
              <span v-if="reviewLoading" class="spinner" aria-hidden="true" />
              {{ reviewLoading ? '正在提交…' : '确认已创建' }}
            </button>
          </div>
        </div>

        <div v-else-if="reviewResolved" class="panel-section review-section review-resolved">
          <div class="panel-section-head">
            <span class="panel-section-title">订单已确认未创建</span>
          </div>
          <div class="hitl-callout accent">
            <Check :size="14" />
            <div>
              <strong>该任务不会再创建订单</strong>
              <p>{{ reviewReason.message || '已确认远端没有创建这笔订单。' }}</p>
            </div>
          </div>
          <p class="hitl-note">执行记录：{{ reviewKey || '未提供' }}</p>
          <details class="hitl-details review-correction">
            <summary>如后续发现订单已创建，可手动回转</summary>
            <div class="hitl-details-body">
              <label class="hitl-note review-field">
                远端订单号
                <input
                  v-model="reviewOrderId"
                  type="text"
                  placeholder="确认已创建时填写，例如 PO20261105001"
                />
              </label>
              <label class="hitl-note review-field">
                备注
                <input v-model="reviewComment" type="text" placeholder="可选" />
              </label>
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                :disabled="reviewLoading || !reviewKey"
                @click="submitReview('mark_succeeded')"
              >
                <span v-if="reviewLoading" class="spinner" aria-hidden="true" />
                {{ reviewLoading ? '正在提交…' : '确认已创建' }}
              </button>
            </div>
          </details>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            @click="emit('open-conversation')"
          >
            返回对话重新发起
          </button>
        </div>

        <div v-if="needInfo" class="panel-section clarification-section">
          <div class="hitl-callout warn">
            <CircleHelp :size="14" />
            <div>
              <strong>任务在等待补充采购条件</strong>
              <p>请在这里补充缺失字段，提交后会继续当前任务，无需重新创建。</p>
            </div>
          </div>
          <SupplementPanel
            :data="task"
            kind="clarification"
            :submitting="loading"
            @resume="resumeClarification"
          />
        </div>

        <details v-if="report" class="hitl-details">
          <summary><FileText :size="13" /> 分析报告</summary>
          <div class="hitl-details-body">
            <MarkdownRenderer :content="report" />
          </div>
        </details>

        <details v-if="events.length" class="hitl-details">
          <summary>
            <ListChecks :size="13" />
            任务事件 ({{ events.length }})
          </summary>
          <div class="hitl-details-body">
            <div class="event-list">
              <div v-for="event in events" :key="event.event_id" class="event-row">
                <span class="event-dot" :class="eventTone(event.type)" />
                <span class="event-type">{{ eventLabel(event.type) }}</span>
                <span class="event-time">{{ formatClock(event.timestamp) }}</span>
              </div>
            </div>
          </div>
        </details>

        <div v-if="error" class="hitl-callout danger" role="alert">
          <TriangleAlert :size="14" />
          {{ error }}
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import {
  ArrowLeft,
  Check,
  CircleHelp,
  ClipboardList,
  Copy,
  FileText,
  ListChecks,
  MessageSquare,
  RefreshCw,
  Sparkles,
  TriangleAlert,
  X,
} from '@lucide/vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import SupplementPanel from './hitl/SupplementPanel.vue'
import { useToast } from '../composables/useToast'
import { useTaskStore } from '../composables/useTaskStore'
import { listReviewExecutions, resolveExecution } from '../api/procurement'
import { formatClock, formatCurrency, formatDate } from '../utils/format'

const emit = defineEmits(['close', 'open-conversation'])
const { toast } = useToast()
const store = useTaskStore()

const task = store.task
const events = store.events
const loading = store.loading
const error = store.error

const sheetRef = ref(null)
const approvalComment = ref('')
const quantities = ref([])
const rejectOpen = ref(false)
const cancelOpen = ref(false)
const refreshing = ref(false)
const reviewOrderId = ref('')
const reviewComment = ref('')
const reviewLoading = ref(false)
const initialLoading = ref(false)

const approval = computed(() => task.value?.interrupt?.summary || null)
const approvalItems = computed(() => approval.value?.items || [])
const retryInterrupt = computed(() =>
  task.value?.interrupt?.type === 'procurement_retry_required'
    ? task.value.interrupt
    : null,
)
const needInfo = computed(() => task.value?.status === 'need_info' && !approval.value)
const reviewKey = ref('')
const reviewResolved = computed(() => task.value?.status === 'not_created')
const requiresReview = computed(() => {
  const status = task.value?.status
  if (status === 'not_created') return false
  return (
    status === 'awaiting_review' ||
    !!task.value?.state_summary?.requires_manual_review
  )
})
const reviewReason = computed(() => {
  const value = task.value?.state_summary?.last_error
  return value && typeof value === 'object' ? value : {}
})
const currentFailure = computed(() => {
  const candidate =
    task.value?.state_summary?.last_error ||
    task.value?.interrupt?.last_error
  return candidate && typeof candidate === 'object' ? candidate : null
})
const failureCode = computed(() => currentFailure.value?.code || '')
const failureMessage = computed(() => {
  const status = task.value?.status
  if (!['failed', 'rejected', 'orphaned'].includes(status)) return ''
  const error =
    task.value?.state_summary?.last_error || task.value?.interrupt?.last_error
  if (!error) return '任务已结束，请查看任务事件了解详情。'
  if (typeof error === 'string') return error
  return error.message || error.code || '任务执行失败。'
})
const cancelledMessage = computed(() => {
  if (task.value?.status !== 'cancelled') return ''
  if (task.value?.state_summary?.execution_status !== 'unknown') return ''
  return '订单结果仍未确认，订单可能已创建；请人工核对后再决定是否重新发起。'
})
const failureDetails = computed(() => {
  const errors = currentFailure.value?.errors
  if (!Array.isArray(errors)) return []
  return errors
    .map((error) => {
      if (typeof error === 'string') return { code: '', message: error }
      if (!error || typeof error !== 'object') return null
      return {
        code: error.code || '',
        message: error.message || error.code || '',
      }
    })
    .filter((error) => error?.message)
})
const report = computed(
  () =>
    task.value?.state_summary?.analysis_report ||
    task.value?.interrupt?.summary?.analysis_report ||
    '',
)

const quantitiesChanged = computed(() =>
  approvalItems.value.some(
    (item, index) => Math.max(1, Number(item.quantity) || 1) !== Number(quantities.value[index]),
  ),
)

const totalAmount = computed(() =>
  approvalItems.value.reduce((sum, item, index) => sum + lineTotal(item, index), 0),
)

const inventoryGaps = computed(() =>
  (approval.value?.inventory_gaps || []).map((gap) => {
    if (!gap || typeof gap !== 'object') return String(gap)
    const name = gap.part_name || (gap.part_id ? `物料 #${gap.part_id}` : '物料')
    const amount = gap.gap ?? gap.shortage ?? gap.inventory_gap
    if (amount !== undefined && Number(amount) <= 0) return null
    return amount !== undefined ? `${name} 缺口 ${amount}` : `${name} 库存不足`
  }).filter(Boolean),
)

const currentOrderId = computed(
  () => task.value?.state_summary?.order_id || '',
)
watch(
  () => [
    task.value?.task_id,
    task.value?.status,
    task.value?.state_summary?.execution_key,
  ],
  async () => {
    const fromState = task.value?.state_summary?.execution_key
    if (fromState) {
      reviewKey.value = fromState
      return
    }
    reviewKey.value = ''
    if (!requiresReview.value || !task.value?.task_id) return
    try {
      const items = await listReviewExecutions()
      const found = items.find((item) => item.task_id === task.value.task_id)
      if (found) reviewKey.value = found.idempotency_key
    } catch {
      /* 复核记录加载失败时保留手动刷新入口 */
    }
  },
  { immediate: true },
)
watch(
  currentOrderId,
  (value) => {
    if (value) reviewOrderId.value = value
  },
  { immediate: true },
)

const steps = [
  { key: 'analysis', label: '分析' },
  { key: 'approval', label: '审批' },
  { key: 'order', label: '下单' },
  { key: 'verify', label: '核验' },
]

const statusLabel = computed(() => {
  const labels = {
    need_info: '待补充信息',
    parsing: '解析需求',
    analyzing: '分析中',
    awaiting_approval: '待审批',
    awaiting_retry: '待恢复',
    awaiting_review: '待人工复核',
    not_created: '确认未创建',
    approved_pending_execution: '已批准待执行',
    executing: '执行中',
    verifying: '核验中',
    completed: '已完成',
    rejected: '已拒绝',
    failed: '失败',
    cancelled: '已取消',
    orphaned: '已失联',
    running: '运行中',
  }
  return labels[task.value?.status] || task.value?.status || '—'
})

const statusClass = computed(() => {
  const status = task.value?.status || ''
  if (status === 'completed') return 'ok'
  if (['failed', 'rejected', 'cancelled', 'orphaned'].includes(status)) return 'bad'
  if (['awaiting_approval', 'awaiting_retry', 'awaiting_review', 'not_created', 'need_info'].includes(status)) return 'warn'
  return 'run'
})

const stepStates = computed(() => {
  const status = task.value?.status
  const failed = ['failed', 'rejected', 'cancelled', 'orphaned'].includes(status)
  let current = 0
  if (['awaiting_approval', 'approved_pending_execution'].includes(status)) current = 1
  else if (status === 'executing') current = 2
  else if (['verifying', 'completed', 'awaiting_review', 'not_created'].includes(status)) current = 3

  return steps.map((_, index) => {
    if (status === 'completed') return 'done'
    if (failed && index === current) return 'error'
    if (index < current) return 'done'
    if (index === current) return 'current'
    return 'waiting'
  })
})

const shortConversationId = computed(() => {
  const conversationId = task.value?.conversation_id || ''
  return conversationId.length > 12
    ? `${conversationId.slice(0, 8)}…`
    : conversationId
})

watch(
  approvalItems,
  (list) => {
    quantities.value = list.map((item) => Math.max(1, Number(item.quantity) || 1))
  },
  { immediate: true, deep: true },
)

watch(
  () => task.value?.task_id,
  () => {
    approvalComment.value = ''
    rejectOpen.value = false
    cancelOpen.value = false
    reviewOrderId.value = ''
    reviewComment.value = ''
  },
)

function lineTotal(item, index) {
  const price = Number(item.unit_price)
  const quantity = Number(quantities.value[index])
  if (!Number.isFinite(price) || !Number.isFinite(quantity)) return 0
  return price * quantity
}

function stepQty(index, delta) {
  const current = Number(quantities.value[index]) || 1
  quantities.value[index] = Math.max(1, current + delta)
}

useEventListener(window, 'keydown', (event) => {
  if (event.key === 'Escape') emit('close')
})

function openOriginConversation() {
  if (!task.value?.conversation_available || !task.value?.conversation_id) return
  emit('open-conversation', task.value.conversation_id)
}

async function copyTaskId() {
  try {
    await navigator.clipboard.writeText(task.value?.task_id || '')
    toast('任务 ID 已复制', { tone: 'success' })
  } catch {
    toast('复制失败', { tone: 'danger' })
  }
}

async function manualRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await store.refresh()
  } finally {
    refreshing.value = false
  }
}

function approvalSignature(interrupt) {
  if (!interrupt) return ''
  const summary = interrupt.summary || {}
  return [
    interrupt.approval_id || '',
    interrupt.plan_version || '',
    interrupt.plan_hash || '',
    Number(summary.total_amount) || '',
    (summary.items || [])
      .map(
        (item) =>
          `${item.part_id}:${Number(item.quantity)}:${Number(item.unit_price)}`,
      )
      .join(','),
  ].join('|')
}

async function ensureLatestPlan() {
  const before = approvalSignature(task.value?.interrupt)
  const latest = await store.refresh()
  if (!latest) {
    error.value = '无法获取最新任务状态，请稍后重试。'
    return null
  }
  if (latest.status !== 'awaiting_approval' || !latest.interrupt?.summary) {
    error.value = '当前方案已不在待审批状态，已为你刷新最新任务。'
    return null
  }
  const after = approvalSignature(latest.interrupt)
  if (before && after && before !== after) {
    error.value = '方案已更新，已为你刷新到最新版本，请重新确认后再提交。'
    return null
  }
  return latest
}

async function submitDecision(decision) {
  if (loading.value) return
  error.value = ''
  rejectOpen.value = false

  // 刷新最新方案会触发 watcher 重置数量，因此先捕获用户当前的修改
  const pendingQuantities = [...quantities.value]

  const latest = await ensureLatestPlan()
  if (!latest) return

  if (decision === 'modify') {
    const invalid = approvalItems.value.some((_, index) => {
      const value = Number(pendingQuantities[index])
      return !Number.isInteger(value) || value < 1
    })
    if (invalid) {
      error.value = '数量必须是大于 0 的整数。'
      return
    }
  }

  const payload = {
    decision,
    ...(approvalComment.value.trim() ? { comment: approvalComment.value.trim() } : {}),
  }

  if (decision === 'modify') {
    payload.changes = {
      items: approvalItems.value.map((item, index) => ({
        part_id: item.part_id,
        ...(item.part_name ? { part_name: item.part_name } : {}),
        quantity: Number(pendingQuantities[index]),
      })),
    }
  }

  try {
    await store.resume(payload)
    toast(decision === 'modify' ? '方案已更新，等待重新审批' : '审批已提交', {
      tone: 'success',
    })
  } catch {
    /* 错误已写入 store.error */
  }
}

async function resumeRetry(action) {
  if (loading.value) return
  error.value = ''
  cancelOpen.value = false
  const latest = await store.refresh()
  if (!latest) {
    error.value = '无法获取最新任务状态，请稍后重试。'
    return
  }
  if (latest.status !== 'awaiting_retry') {
    error.value = '任务状态已变化，已为你刷新最新状态。'
    return
  }
  try {
    const result = await store.resume({ action })
    if (action === 'cancel' && result?.status === 'awaiting_review') {
      toast('已停止重试；订单结果仍未确认，请人工核对', { tone: 'danger' })
      return
    }
    toast(action === 'cancel' ? '任务已取消' : '已提交重试', { tone: 'success' })
  } catch {
    /* 错误已写入 store.error */
  }
}

async function submitReview(action) {
  if (reviewLoading.value) return
  error.value = ''
  if (!reviewKey.value) {
    error.value = '缺少执行记录编号，请刷新任务或联系管理员。'
    return
  }
  const orderId = reviewOrderId.value.trim()
  if (action === 'mark_succeeded' && !orderId) {
    error.value = '请填写远端订单号后再确认。'
    return
  }
  if (
    action === 'mark_failed' &&
    !window.confirm('确认远端没有创建这笔订单？确认后任务会进入“确认未创建”，仍可人工回转。')
  ) {
    return
  }
  reviewLoading.value = true
  try {
    const result = await resolveExecution(reviewKey.value, {
      action,
      order_id: action === 'mark_succeeded' ? orderId : null,
      comment: reviewComment.value.trim(),
    })
    const latest = await store.refresh()
    if (!latest) return
    const settled = ['completed', 'not_created', 'failed'].includes(latest.status)
    if (result?.task_synced === false && !settled) {
      error.value = '执行结果已提交，但任务状态同步失败，请刷新后重试或联系管理员。'
      return
    }
    toast(action === 'mark_succeeded' ? '订单已标记为创建' : '已确认未创建', {
      tone: 'success',
    })
  } catch (exception) {
    error.value = exception.message || String(exception)
  } finally {
    reviewLoading.value = false
  }
}

async function resumeClarification(payload) {
  if (loading.value) return
  error.value = ''
  try {
    await store.resume(payload)
    toast('补充信息已提交，任务将继续处理', { tone: 'success' })
  } catch {
    /* 错误已写入 store.error */
  }
}

const EVENT_LABELS = {
  'task.started': '任务已启动',
  'task.resumed': '任务已恢复',
  'task.completed': '任务完成',
  'task.failed': '任务失败',
  'task.cancelled': '任务已取消',
  'task.reconciled': '订单对账结果已回写',
  'approval.required': '等待人工审批',
  'approval.decided': '审批已完成',
  'order.creating': '正在创建订单',
  'order.created': '订单已创建',
  'order.verified': '订单已核验',
  'analysis.completed': '分析完成',
  'plan.proposed': '方案已生成',
}

function eventLabel(type) {
  return EVENT_LABELS[type] || type
}

function eventTone(type) {
  if (type?.includes('failed') || type?.includes('cancelled')) return 'bad'
  if (type?.includes('completed') || type?.includes('created') || type?.includes('verified')) {
    return 'ok'
  }
  return ''
}

onMounted(async () => {
  sheetRef.value?.focus({ preventScroll: true })
  if (!store.activeTaskId.value) return
  if (!store.task.value) {
    initialLoading.value = true
    await store.refresh()
    initialLoading.value = false
  }
  store.connect()
})
</script>

<style scoped>
.task-sheet {
  position: fixed;
  z-index: 58;
  top: var(--header-h);
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  width: var(--task-panel-w, 480px);
  outline: none;
  border-left: 1px solid var(--line);
  background: var(--surface);
  box-shadow: -12px 0 36px -28px rgba(25, 24, 23, 0.45);
  animation: sheetIn var(--dur-slow) var(--ease);
}

@keyframes sheetIn {
  from {
    opacity: 0;
    transform: translateX(16px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.sheet-progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 3;
  background: var(--surface-2);
  overflow: hidden;
}

.sheet-progress span {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 34%;
  background: var(--accent);
  animation: sheetProgress 1.2s var(--ease) infinite;
}

@keyframes sheetProgress {
  0% {
    left: -34%;
  }
  100% {
    left: 100%;
  }
}

.sheet-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px 13px;
  border-bottom: 1px solid var(--line-soft);
}

.sheet-head-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.spinning {
  animation: spin 0.9s linear infinite;
}

.sheet-eyebrow {
  display: block;
  color: var(--ink-3);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.1em;
}

.sheet-title {
  margin-top: 2px;
  color: var(--ink);
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.015em;
}

.sheet-subtitle {
  margin-top: 2px;
  color: var(--ink-3);
  font-size: 11.5px;
}

.sheet-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 18px 28px;
}

.sheet-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 64px 20px;
  text-align: center;
}

.sheet-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: 6px;
  border-radius: 14px;
  background: var(--surface-2);
  color: var(--ink-3);
}

.sheet-empty h3 {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.sheet-empty p {
  max-width: 300px;
  color: var(--ink-3);
  font-size: 11.5px;
  line-height: 1.7;
}

.sheet-empty .btn {
  margin-top: 10px;
}

.task-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  border-radius: var(--r-full);
  font-size: 10.5px;
  font-weight: 600;
}

.status-badge .status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.status-badge.ok {
  background: var(--success-soft);
  color: var(--success);
}

.status-badge.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

.status-badge.warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.status-badge.run {
  background: var(--accent-soft);
  color: var(--accent);
}

.task-id {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 10.5px;
}

.task-id:hover {
  color: var(--ink-2);
}

.task-origin {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: -6px 0 14px;
  color: var(--ink-3);
  font-size: 10.5px;
}

.task-origin-label {
  color: var(--ink-3);
}

.task-origin-id {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-2);
}

.task-origin-muted {
  color: var(--ink-3);
}

.task-origin-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 2px;
  padding: 1px 4px;
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
}

.task-origin-link:hover {
  color: var(--accent-strong, var(--accent));
  text-decoration: underline;
}

.steps {
  display: flex;
  margin: 2px 0 18px;
  padding: 0;
  list-style: none;
}

.step {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  gap: 5px;
}

.step::before {
  content: '';
  position: absolute;
  top: 10px;
  left: -50%;
  width: 100%;
  height: 1px;
  background: var(--line);
}

.step:first-child::before {
  display: none;
}

.step.done::before,
.step.current::before {
  background: var(--accent);
}

.step-dot {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 21px;
  height: 21px;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: var(--surface);
  color: var(--ink-3);
  font-size: 9px;
  font-weight: 600;
}

.step.done .step-dot {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.step.current .step-dot {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.step.error .step-dot {
  border-color: var(--danger);
  background: var(--danger-soft);
  color: var(--danger);
}

.step-label {
  color: var(--ink-3);
  font-size: 10px;
}

.step.current .step-label,
.step.done .step-label {
  color: var(--ink-2);
  font-weight: 550;
}

.panel-section {
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-md);
  background: var(--surface);
}

.panel-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.panel-section-title {
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
}

.approval-top {
  display: grid;
  grid-template-columns: minmax(130px, 0.85fr) minmax(0, 1.3fr);
  gap: 12px;
  margin-bottom: 10px;
}

.approval-amount {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 1px;
  padding-right: 12px;
  border-right: 1px solid var(--line);
}

.approval-amount span {
  color: var(--ink-3);
  font-size: 10px;
  font-weight: 600;
}

.approval-amount strong {
  color: var(--ink);
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.approval-amount em {
  color: var(--warn);
  font-size: 10px;
  font-style: normal;
  text-decoration: line-through;
}

.approval-facts {
  padding: 8px 10px;
  background: var(--surface-2);
}

.approval-table {
  table-layout: fixed;
}

.approval-table th:nth-child(2),
.approval-table td:nth-child(2) {
  width: 78px;
}

.approval-table th:nth-child(3),
.approval-table td:nth-child(3) {
  width: 88px;
}

.approval-table td:first-child {
  white-space: normal;
  word-break: break-word;
}

.approval-facts dt {
  font-size: 10.5px;
}

.approval-facts dd {
  font-size: 11.5px;
}

.item-name {
  color: var(--ink);
  font-weight: 500;
}

.item-id {
  margin-left: 4px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 9.5px;
}

.item-line {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.item-line .item-name {
  min-width: 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.item-price {
  display: block;
  margin-top: 1px;
  color: var(--ink-3);
  font-size: 9.5px;
}

.gap-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.gap-list span {
  padding: 1px 6px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  font-size: 10.5px;
}

.panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
}

.warn-text {
  color: var(--warn);
}

.failure-callout {
  margin-bottom: 14px;
}

.failure-callout p {
  margin-top: 2px;
  font-size: 11.5px;
  line-height: 1.6;
}

.failure-details {
  display: grid;
  gap: 5px;
  margin: 8px 0 10px;
  padding: 0;
  list-style: none;
}

.failure-details li {
  display: grid;
  gap: 2px;
  padding: 7px 8px;
  border: 1px solid color-mix(in srgb, var(--danger) 22%, transparent);
  border-radius: var(--r-xs);
  background: color-mix(in srgb, var(--danger) 6%, transparent);
  color: var(--ink-2);
  font-size: 11px;
  line-height: 1.45;
}

.failure-detail-code {
  color: var(--danger);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 650;
}

.panel-section :deep(.md-body) {
  font-size: 12px;
  line-height: 1.7;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--ink-2);
  font-size: 11.5px;
  font-weight: 550;
}

.field textarea {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 400;
  line-height: 1.6;
  resize: vertical;
}

.field textarea:focus {
  outline: none;
  border-color: color-mix(in srgb, var(--accent) 50%, var(--line));
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.event-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.event-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 0;
  font-size: 11px;
}

.event-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ink-3);
}

.event-dot.ok {
  background: var(--success);
}

.event-dot.bad {
  background: var(--danger);
}

.event-type {
  flex: 1;
  color: var(--ink-2);
}

.event-time {
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}

.review-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 10px 0;
}

.review-field input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm, 6px);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
}

.review-resolved > .btn {
  margin-top: 10px;
}

.review-resolved .hitl-details {
  margin-top: 10px;
}

@media (max-width: 640px) {
  .task-sheet {
    width: 100vw;
  }

  .sheet-head {
    padding: 13px 14px 11px;
  }

  .sheet-body {
    padding: 12px 14px 24px;
  }

  .approval-top {
    grid-template-columns: 1fr;
  }

  .approval-amount {
    padding-right: 0;
    padding-bottom: 8px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .item-name {
    display: block;
  }
}
</style>
