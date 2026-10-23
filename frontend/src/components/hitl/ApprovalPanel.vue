<template>
  <div class="approval-panel">
    <!-- ============ 旧订单 HITL ============ -->
    <template v-if="kind === 'order'">
      <div
        v-for="(action, index) in normalizedActions"
        :key="index"
        class="hitl-section"
      >
        <div class="hitl-section-head">
          <span class="hitl-section-title">{{ action.label }}</span>
          <span v-if="action.description" class="hitl-section-extra">
            {{ action.description }}
          </span>
        </div>

        <dl v-if="action.fields.length" class="hitl-kv">
          <template v-for="field in action.fields" :key="field.key">
            <dt>{{ field.label }}</dt>
            <dd :class="{ mono: field.mono }">{{ field.value }}</dd>
          </template>
        </dl>

        <div v-if="action.detail.length" class="hitl-table-wrap" style="margin-top: 8px">
          <table class="hitl-table order-table">
            <thead>
              <tr>
                <th>#</th>
                <th>物料 ID</th>
                <th class="num">数量</th>
                <th class="num">单价</th>
                <th class="num">小计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, itemIndex) in action.detail" :key="itemIndex">
                <td class="muted">{{ itemIndex + 1 }}</td>
                <td>{{ item.partId ?? item.part_id ?? '—' }}</td>
                <td class="num">{{ item.quantity ?? '—' }}</td>
                <td class="num">{{ formatCurrency(item.unitPrice ?? item.unit_price) }}</td>
                <td class="num">{{ formatCurrency(item.subtotal) }}</td>
              </tr>
            </tbody>
            <tfoot v-if="action.total > 0">
              <tr>
                <td colspan="4">合计</td>
                <td class="num">{{ formatCurrency(action.total) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      <div v-if="orderTotal > 0" class="hitl-callout accent">
        <ShieldCheck :size="14" />
        本次将创建或修改 1 张采购订单，合计
        <strong>{{ formatCurrency(orderTotal) }}</strong>。确认后才会调用 ERP 接口。
      </div>

      <div v-if="rejectOpen" class="hitl-confirm">
        <div class="hitl-confirm-head">
          <TriangleAlert :size="14" />
          确认拒绝这次操作？
        </div>
        <p>拒绝后本次订单不会执行，Agent 会继续回复并保留当前上下文。</p>
        <div class="hitl-confirm-actions">
          <button type="button" class="btn btn-ghost btn-sm" @click="rejectOpen = false">
            取消
          </button>
          <button
            type="button"
            class="btn btn-danger btn-sm"
            :disabled="submitting"
            @click="submitReject"
          >
            确认拒绝
          </button>
        </div>
      </div>

      <div v-else class="hitl-foot">
        <span class="hitl-foot-note">
          <ShieldCheck :size="12" />
          批准后会立即执行，操作会记入订单记录
        </span>
        <div class="hitl-foot-actions">
          <button
            type="button"
            class="btn btn-danger-ghost"
            :disabled="submitting"
            @click="rejectOpen = true"
          >
            拒绝
          </button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="submitting"
            @click="submitApprove"
          >
            <span v-if="submitting" class="spinner" aria-hidden="true" />
            {{ submitting ? '正在执行…' : '同意并执行' }}
          </button>
        </div>
      </div>
    </template>

    <!-- ============ 采购任务审批 ============ -->
    <template v-else>
      <div class="hitl-section">
        <div class="approval-summary">
          <div class="amount-block">
            <span class="amount-label">方案总金额</span>
            <strong class="amount-value">{{ formatCurrency(currentTotal, currency) }}</strong>
            <span v-if="changed" class="amount-delta">
              原方案 {{ formatCurrency(originalTotal, currency) }}
            </span>
          </div>

          <div class="summary-facts">
            <div>
              <span>供应商</span>
              <strong>{{ supplier || '—' }}</strong>
            </div>
            <div>
              <span>收货仓库</span>
              <strong>{{ summary.warehouse_location || '—' }}</strong>
            </div>
            <div>
              <span>预计交货</span>
              <strong>{{ formatDate(summary.expected_delivery_date) || '—' }}</strong>
            </div>
            <div>
              <span>明细</span>
              <strong>{{ items.length }} 项</strong>
            </div>
          </div>
        </div>

        <div class="hitl-meta" style="margin-top: 10px">
          <span v-if="data.task_id" class="hitl-chip">任务 {{ shortId(data.task_id, 9, 4) }}</span>
          <span v-if="data.plan_version" class="hitl-chip">方案 v{{ data.plan_version }}</span>
          <span v-if="data.approval_id" class="hitl-chip">
            审批 {{ shortId(data.approval_id, 6, 4) }}
          </span>
        </div>
      </div>

      <div class="hitl-section">
        <div class="hitl-section-head">
          <span class="hitl-section-title">采购明细</span>
          <span v-if="canModify" class="hitl-section-extra">可调整数量，改动后需重新审批</span>
        </div>

        <div class="hitl-table-wrap">
          <table class="hitl-table approval-table">
            <thead>
              <tr>
                <th>物料</th>
                <th class="num">单价</th>
                <th class="num">数量</th>
                <th class="num">小计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in items" :key="index">
                <td>
                  <span class="item-name">{{ item.part_name || `物料 #${item.part_id}` }}</span>
                  <span class="item-id">#{{ item.part_id }}</span>
                </td>
                <td class="num">{{ formatCurrency(item.unit_price, currency) }}</td>
                <td class="num">
                  <div v-if="canModify" class="hitl-qty">
                    <button
                      type="button"
                      aria-label="减少数量"
                      :disabled="submitting"
                      @click="stepQty(index, -1)"
                    >
                      −
                    </button>
                    <input
                      v-model.number="quantities[index]"
                      type="number"
                      min="1"
                      aria-label="数量"
                      :disabled="submitting"
                    />
                    <button
                      type="button"
                      aria-label="增加数量"
                      :disabled="submitting"
                      @click="stepQty(index, 1)"
                    >
                      +
                    </button>
                  </div>
                  <span v-else>{{ item.quantity }}</span>
                </td>
                <td class="num">{{ formatCurrency(lineTotal(item, index), currency) }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="3">合计</td>
                <td class="num">{{ formatCurrency(currentTotal, currency) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <p v-if="changed" class="hitl-note">
          数量已调整。保存后会按新数量重新生成方案并再次发起审批，不会直接下单。
        </p>
      </div>

      <div v-if="inventoryGaps.length" class="hitl-section">
        <div class="hitl-callout warn">
          <TriangleAlert :size="14" />
          <div>
            <strong>库存提示</strong>
            <div class="gap-list">
              <span v-for="(gap, index) in inventoryGaps" :key="index">{{ gap }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="rationale" class="hitl-section">
        <details class="hitl-details">
          <summary>
            <Sparkles :size="13" />
            选型理由
          </summary>
          <div class="hitl-details-body">
            <MarkdownRenderer :content="rationale" />
          </div>
        </details>
      </div>

      <div v-if="analysisReport" class="hitl-section">
        <details class="hitl-details">
          <summary>
            <FileText :size="13" />
            查看分析报告
          </summary>
          <div class="hitl-details-body">
            <MarkdownRenderer :content="analysisReport" />
          </div>
        </details>
      </div>

      <div class="hitl-section">
        <label class="hitl-field">
          <span class="hitl-label">审批意见（可选）</span>
          <textarea
            v-model="comment"
            class="hitl-textarea"
            rows="2"
            placeholder="例如：报价合理，同意下单；或说明拒绝原因"
            :disabled="submitting"
          />
        </label>
      </div>

      <p v-if="formError" class="hitl-error-text" role="alert">{{ formError }}</p>

      <div v-if="rejectOpen" class="hitl-confirm">
        <div class="hitl-confirm-head">
          <TriangleAlert :size="14" />
          确认拒绝这个采购方案？
        </div>
        <p>拒绝后任务会结束，不会创建订单。审批意见会一并记录。</p>
        <div class="hitl-confirm-actions">
          <button type="button" class="btn btn-ghost btn-sm" @click="rejectOpen = false">
            取消
          </button>
          <button
            type="button"
            class="btn btn-danger btn-sm"
            :disabled="submitting"
            @click="submitReject"
          >
            确认拒绝
          </button>
        </div>
      </div>

      <div v-else class="hitl-foot">
        <span class="hitl-foot-note">
          <ShieldCheck :size="12" />
          批准后立即创建订单，操作会记录在任务事件中
        </span>
        <div class="hitl-foot-actions">
          <button
            type="button"
            class="btn btn-danger-ghost"
            :disabled="submitting"
            @click="rejectOpen = true"
          >
            拒绝
          </button>
          <button
            v-if="canModify && changed"
            type="button"
            class="btn btn-primary"
            :disabled="submitting"
            @click="submitModify"
          >
            <span v-if="submitting" class="spinner" aria-hidden="true" />
            {{ submitting ? '正在提交…' : '保存修改并重新审批' }}
          </button>
          <button
            v-else
            type="button"
            class="btn btn-primary"
            :disabled="submitting"
            @click="submitApprove"
          >
            <span v-if="submitting" class="spinner" aria-hidden="true" />
            {{ submitting ? '正在提交…' : '同意并创建订单' }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import {
  FileText,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from '@lucide/vue'
import MarkdownRenderer from '../MarkdownRenderer.vue'
import {
  formatCurrency,
  formatDate,
  prettyJson,
  shortId,
} from '../../utils/format'

const props = defineProps({
  data: { type: Object, required: true },
  kind: { type: String, default: 'order' }, // order | procurement
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['resume'])

const comment = ref('')
const rejectOpen = ref(false)
const formError = ref('')

/* ============================================================
   旧订单审批
   ============================================================ */
const actions = computed(() => props.data.action_requests || [])

const FIELD_LABELS = {
  order_id: '订单 ID',
  orderNumber: '订单编号',
  supplierId: '供应商 ID',
  supplierName: '供应商',
  totalAmount: '订单总额',
  expectedDeliveryDate: '预计交货',
  status: '订单状态',
  remark: '备注',
  warehouse: '仓库',
  warehouse_location: '仓库',
  priority: '优先级',
}

const STATUS_LABELS = {
  1: '待审核',
  2: '已审核',
  3: '采购中',
  4: '已入库',
  5: '已取消',
}

function formatFieldName(key) {
  return FIELD_LABELS[key] || key
}

function formatFieldValue(key, value) {
  if (value === null || value === undefined || value === '') return '—'
  if (key === 'status') return STATUS_LABELS[value] ?? String(value)
  if (key === 'expectedDeliveryDate') return formatDate(value)
  if (key === 'totalAmount') return formatCurrency(value)
  if (typeof value === 'object') return prettyJson(value)
  return String(value)
}

function detailTotal(detail) {
  return (detail || []).reduce((sum, item) => {
    const subtotal = Number(item.subtotal)
    if (Number.isFinite(subtotal) && subtotal > 0) return sum + subtotal
    const price = Number(item.unitPrice ?? item.unit_price)
    const quantity = Number(item.quantity)
    return sum + (Number.isFinite(price) && Number.isFinite(quantity) ? price * quantity : 0)
  }, 0)
}

const normalizedActions = computed(() =>
  actions.value.map((action) => {
    const name = action.name || ''
    const args = action.args || {}
    const isUpdate = name === 'order_update' && args.update_data
    const body = isUpdate ? args.update_data || {} : args
    const detail = body.orderDetail || body.order_detail || []

    const fields = []
    if (isUpdate && args.order_id !== undefined) {
      fields.push({
        key: 'order_id',
        label: '订单 ID',
        value: formatFieldValue('order_id', args.order_id),
        mono: false,
      })
    }

    for (const [key, value] of Object.entries(body)) {
      if (/^order_?detail$/i.test(key)) continue
      const isObject = value && typeof value === 'object'
      fields.push({
        key,
        label: formatFieldName(key),
        value: formatFieldValue(key, value),
        mono: isObject,
      })
    }

    return {
      name,
      label:
        {
          order_create: '新建采购订单',
          order_update: '修改采购订单',
        }[name] || '采购订单操作',
      description: action.description || '',
      fields,
      detail,
      total: detailTotal(detail),
    }
  }),
)

const orderTotal = computed(() =>
  normalizedActions.value.reduce((sum, action) => sum + action.total, 0),
)

/* ============================================================
   采购任务审批
   ============================================================ */
const summary = computed(() => props.data.summary || {})
const items = computed(() => summary.value.items || [])
const supplier = computed(
  () => summary.value.supplier_name || summary.value.supplier_id || '',
)
const currency = computed(() => summary.value.currency || 'CNY')

const quantities = ref([])

watch(
  items,
  (list) => {
    quantities.value = list.map((item) => Math.max(1, Number(item.quantity) || 1))
  },
  { immediate: true, deep: true },
)

const originalTotal = computed(() => {
  const total = Number(summary.value.total_amount)
  if (Number.isFinite(total) && total > 0) return total
  return items.value.reduce((sum, item) => {
    const price = Number(item.unit_price)
    const quantity = Number(item.quantity)
    return sum + (Number.isFinite(price) && Number.isFinite(quantity) ? price * quantity : 0)
  }, 0)
})

function lineTotal(item, index) {
  const price = Number(item.unit_price)
  const quantity = Number(quantities.value[index])
  if (!Number.isFinite(price) || !Number.isFinite(quantity)) return 0
  return price * quantity
}

const currentTotal = computed(() =>
  items.value.reduce((sum, item, index) => sum + lineTotal(item, index), 0),
)

const changed = computed(() =>
  items.value.some(
    (item, index) => Math.max(1, Number(item.quantity) || 1) !== Number(quantities.value[index]),
  ),
)

const allowedDecisions = computed(() => {
  const configs = props.data.review_configs || []
  const allowed = configs[0]?.allowed_decisions
  if (Array.isArray(allowed) && allowed.length) return allowed
  return ['approve', 'reject', 'modify']
})

const canModify = computed(() => allowedDecisions.value.includes('modify'))

const inventoryGaps = computed(() =>
  (summary.value.inventory_gaps || []).map((gap) => {
    if (!gap || typeof gap !== 'object') return String(gap)
    const name = gap.part_name || (gap.part_id ? `物料 #${gap.part_id}` : '物料')
    const amount = gap.gap ?? gap.shortage ?? gap.inventory_gap
    if (amount !== undefined && Number(amount) <= 0) return null
    return amount !== undefined ? `${name} 缺口 ${amount}` : `${name} 库存不足`
  }).filter(Boolean),
)

const rationale = computed(() => summary.value.rationale || '')
const analysisReport = computed(() => summary.value.analysis_report || '')

/* ============================================================
   提交
   ============================================================ */
function stepQty(index, delta) {
  const current = Number(quantities.value[index]) || 1
  quantities.value[index] = Math.max(1, current + delta)
}

function submitApprove() {
  if (props.submitting) return
  formError.value = ''
  if (props.kind === 'order') {
    emit('resume', { decisions: [{ type: 'approve' }] })
    return
  }
  emit('resume', {
    decision: 'approve',
    ...(comment.value.trim() ? { comment: comment.value.trim() } : {}),
  })
}

function submitReject() {
  if (props.submitting) return
  rejectOpen.value = false
  if (props.kind === 'order') {
    emit('resume', { decisions: [{ type: 'reject' }] })
    return
  }
  emit('resume', {
    decision: 'reject',
    ...(comment.value.trim() ? { comment: comment.value.trim() } : {}),
  })
}

function submitModify() {
  if (props.submitting) return
  formError.value = ''
  const invalid = items.value.some((_, index) => {
    const value = Number(quantities.value[index])
    return !Number.isInteger(value) || value < 1
  })
  if (invalid) {
    formError.value = '数量必须是大于 0 的整数。'
    return
  }

  const patchItems = items.value.map((item, index) => ({
    part_id: item.part_id,
    ...(item.part_name ? { part_name: item.part_name } : {}),
    quantity: Number(quantities.value[index]),
  }))

  emit('resume', {
    decision: 'modify',
    changes: { items: patchItems },
    ...(comment.value.trim() ? { comment: comment.value.trim() } : {}),
  })
}
</script>

<style scoped>
.approval-summary {
  display: grid;
  grid-template-columns: minmax(150px, 0.9fr) minmax(0, 1.4fr);
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-sm);
  background: var(--surface-2);
}

.amount-block {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  padding-right: 14px;
  border-right: 1px solid var(--line);
}

.amount-label {
  color: var(--ink-3);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.amount-value {
  color: var(--ink);
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}

.amount-delta {
  color: var(--warn);
  font-size: 10.5px;
  text-decoration: line-through;
  text-decoration-color: color-mix(in srgb, var(--warn) 50%, transparent);
}

.summary-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 14px;
  align-content: center;
}

.summary-facts div {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.summary-facts span {
  color: var(--ink-3);
  font-size: 10.5px;
}

.summary-facts strong {
  overflow: hidden;
  color: var(--ink);
  font-size: 12px;
  font-weight: 550;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.item-name {
  color: var(--ink);
  font-weight: 500;
}

.item-id {
  margin-left: 5px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 10px;
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

.hitl-details-body :deep(.md-body) {
  font-size: 12.5px;
  line-height: 1.7;
}

@media (max-width: 640px) {
  .approval-panel .approval-table th:nth-child(2),
  .approval-panel .approval-table td:nth-child(2) {
    display: none;
  }

  .approval-panel .order-table th:nth-child(1),
  .approval-panel .order-table td:nth-child(1),
  .approval-panel .order-table th:nth-child(4),
  .approval-panel .order-table td:nth-child(4) {
    display: none;
  }

  .approval-panel .hitl-table td:first-child {
    white-space: normal;
  }

  .approval-panel .item-name {
    display: block;
  }

  .approval-summary {
    grid-template-columns: 1fr;
  }

  .amount-block {
    padding-right: 0;
    padding-bottom: 10px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .summary-facts {
    grid-template-columns: 1fr;
  }
}
</style>
