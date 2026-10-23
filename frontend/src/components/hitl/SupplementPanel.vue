<template>
  <div class="supplement-panel">
    <div class="hitl-section">
      <div class="hitl-section-head">
        <span class="hitl-section-title">
          {{ isClarification ? '还缺少这些采购条件' : '缺少字段' }}
        </span>
        <div v-if="hasForm" class="hitl-segmented" role="group" aria-label="补充方式">
          <button
            type="button"
            :class="{ active: mode === 'form' }"
            @click="mode = 'form'"
          >
            表单填写
          </button>
          <button
            type="button"
            :class="{ active: mode === 'text' }"
            @click="mode = 'text'"
          >
            自然语言
          </button>
        </div>
      </div>

      <div class="hitl-chips">
        <span v-for="label in missingChips" :key="label" class="hitl-chip">
          {{ label }}
        </span>
        <span v-if="!missingChips.length" class="hitl-chip">请用自然语言补充</span>
      </div>
    </div>

    <div v-if="isClarification && currentRequest.items?.length" class="hitl-section">
      <div class="hitl-section-head">
        <span class="hitl-section-title">当前需求</span>
        <span class="hitl-section-extra">提交后按新条件重新分析</span>
      </div>
      <div class="hitl-chips">
        <span
          v-for="(item, index) in currentRequest.items"
          :key="index"
          class="hitl-chip neutral"
        >
          {{ item.part_name || (item.part_id ? `物料 #${item.part_id}` : `明细 ${index + 1}`) }}
          <template v-if="item.quantity">× {{ item.quantity }}</template>
        </span>
        <span v-if="currentRequest.budget" class="hitl-chip neutral">
          预算 ¥{{ currentRequest.budget }}
        </span>
        <span v-if="currentRequest.warehouse_location" class="hitl-chip neutral">
          {{ currentRequest.warehouse_location }}
        </span>
        <span v-if="currentRequest.required_by" class="hitl-chip neutral">
          到货 {{ currentRequest.required_by }}
        </span>
      </div>
    </div>

    <form
      v-if="mode === 'form' && hasForm"
      class="hitl-section"
      @submit.prevent="submit"
    >
      <div class="hitl-grid">
        <label
          v-for="field in fields"
          :key="field.key"
          class="hitl-field"
          :class="{ full: field.full }"
        >
          <span class="hitl-label">
            {{ field.label }}
            <span v-if="field.required" class="req" aria-hidden="true">*</span>
          </span>

          <select
            v-if="field.type === 'select'"
            v-model="values[field.key]"
            class="hitl-select"
            :aria-invalid="!!errors[field.key]"
          >
            <option value="" disabled>请选择</option>
            <option
              v-for="option in field.options"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>

          <textarea
            v-else-if="field.type === 'textarea'"
            v-model="values[field.key]"
            class="hitl-textarea"
            rows="2"
            :placeholder="field.placeholder"
            :aria-invalid="!!errors[field.key]"
          />

          <input
            v-else
            v-model="values[field.key]"
            class="hitl-input"
            :type="field.type"
            :min="field.min"
            :step="field.step"
            :placeholder="field.placeholder"
            :aria-invalid="!!errors[field.key]"
          />

          <span v-if="errors[field.key]" class="hitl-error-text">
            {{ errors[field.key] }}
          </span>
        </label>

        <label class="hitl-field full">
          <span class="hitl-label">补充说明（可选）</span>
          <textarea
            v-model="extraText"
            class="hitl-textarea"
            rows="2"
            placeholder="其它需要说明的信息，例如供应商偏好、交期要求…"
          />
        </label>
      </div>
    </form>

    <div v-else class="hitl-section">
      <label class="hitl-field">
        <span class="hitl-label">用自然语言补充</span>
        <textarea
          ref="fallbackRef"
          v-model="textValue"
          class="hitl-textarea"
          rows="4"
          :placeholder="
            isClarification
              ? '例如：数量改为 20，仓库改到 B 区 2 号库，希望 6 月 15 日前到货'
              : '例如：物料 ID 67，数量 10，单价 58 元，预计 6 月 15 日交货'
          "
        />
      </label>
      <p class="hitl-hint" style="margin-top: 6px">
        自然语言会由 Agent 解析，字段较多时建议使用表单填写。
      </p>
    </div>

    <div v-if="collectedEntries.length" class="hitl-section">
      <div class="hitl-section-head">
        <span class="hitl-section-title">已收集的信息</span>
      </div>
      <dl class="hitl-kv">
        <template v-for="entry in collectedEntries" :key="entry.key">
          <dt>{{ entry.label }}</dt>
          <dd :class="{ mono: entry.mono }">{{ entry.value }}</dd>
        </template>
      </dl>
    </div>

    <div v-else-if="collectedPlain" class="hitl-section">
      <div class="hitl-section-head">
        <span class="hitl-section-title">已收集的信息</span>
      </div>
      <pre class="hitl-json">{{ collectedPlain }}</pre>
    </div>

    <div v-if="formError" class="hitl-callout danger" role="alert">
      <TriangleAlert :size="14" />
      {{ formError }}
    </div>

    <div v-if="validationMessages.length" class="hitl-section">
      <div class="hitl-section-head">
        <span class="hitl-section-title">校验提示</span>
      </div>
      <div class="hitl-chips">
        <span
          v-for="(item, index) in validationMessages"
          :key="index"
          class="hitl-chip neutral"
        >
          {{ item }}
        </span>
      </div>
    </div>

    <div class="hitl-foot">
      <span class="hitl-foot-note">
        <LockKeyhole :size="12" />
        提交后 Agent 会从当前步骤继续
      </span>
      <div class="hitl-foot-actions">
        <button
          type="button"
          class="btn btn-primary"
          :disabled="submitting"
          @click="submit"
        >
          <span v-if="submitting" class="spinner" aria-hidden="true" />
          {{ submitting ? '正在提交…' : submitLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { LockKeyhole, TriangleAlert } from '@lucide/vue'
import { prettyJson } from '../../utils/format'

const props = defineProps({
  data: { type: Object, required: true },
  kind: { type: String, default: 'order' }, // order | clarification
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['resume'])

const isClarification = computed(() => props.kind === 'clarification')
const currentRequest = computed(() => props.data.interrupt?.current_request || {})
const validationMessages = computed(() => {
  const errors = props.data.interrupt?.validation_errors || []
  return errors.map((item) => item.message || item.code).filter(Boolean)
})

const mode = ref('form')
const textValue = ref('')
const extraText = ref('')
const formError = ref('')
const values = reactive({})
const errors = reactive({})
const fallbackRef = ref(null)

/* ============================================================
   字段识别
   ============================================================ */
const ORDER_FIELDS = [
  {
    key: 'partId',
    aliases: ['partid', 'part_id', '物料id', '物料 id', '零部件id', '零件id'],
    label: '物料 ID',
    type: 'number',
    min: 1,
    placeholder: '例如 67',
  },
  {
    key: 'quantity',
    aliases: ['quantity', 'qty', '采购数量', '数量'],
    label: '采购数量',
    type: 'number',
    min: 1,
    placeholder: '例如 10',
  },
  {
    key: 'unitPrice',
    aliases: ['unitprice', 'unit_price', '采购单价', '单价'],
    label: '采购单价（元）',
    type: 'number',
    min: 0,
    step: '0.01',
    placeholder: '例如 58.00',
  },
  {
    key: 'partName',
    aliases: ['partname', 'part_name', '物料名称', '零部件名称'],
    label: '物料名称',
    type: 'text',
    placeholder: '例如 NGK 铱金火花塞',
  },
  {
    key: 'partCode',
    aliases: ['partcode', 'part_code', '物料编码', '零件编码'],
    label: '物料编码',
    type: 'text',
  },
  {
    key: 'supplierId',
    aliases: ['supplierid', 'supplier_id', '供应商id', '供应商 id'],
    label: '供应商 ID',
    type: 'number',
    min: 1,
  },
  {
    key: 'expectedDeliveryDate',
    aliases: ['expecteddeliverydate', 'expected_delivery_date', '预计交货', '交货日期'],
    label: '预计交货日期',
    type: 'date',
  },
  {
    key: 'orderNumber',
    aliases: ['ordernumber', 'order_number', '订单编号'],
    label: '订单编号',
    type: 'text',
  },
  {
    key: 'orderId',
    aliases: ['orderid', 'order_id', '订单id', '订单 id'],
    label: '订单 ID',
    type: 'number',
    min: 1,
  },
  {
    key: 'remark',
    aliases: ['remark', '备注', '说明'],
    label: '备注',
    type: 'text',
    full: true,
  },
]

const CLARIFY_FIELDS = {
  warehouse_location: {
    key: 'warehouse_location',
    label: '收货仓库',
    type: 'text',
    placeholder: '例如 C区-3号库',
  },
  budget: {
    key: 'budget',
    label: '预算上限（元）',
    type: 'number',
    min: 0,
    step: '0.01',
    placeholder: '例如 1000.00',
  },
  required_by: {
    key: 'required_by',
    label: '要求到货日期',
    type: 'date',
  },
  preferred_supplier_id: {
    key: 'preferred_supplier_id',
    label: '指定供应商 ID',
    type: 'number',
    min: 1,
    placeholder: '可选',
  },
  priority: {
    key: 'priority',
    label: '选商偏好',
    type: 'select',
    options: [
      { value: 'balanced', label: '综合平衡' },
      { value: 'price', label: '价格优先' },
      { value: 'lead_time', label: '交期优先' },
    ],
  },
  note: {
    key: 'note',
    label: '补充说明',
    type: 'textarea',
    full: true,
    placeholder: '例如：优先选择交期更短的供应商',
  },
}

function detectOrderFields(raw) {
  const text = String(raw || '')
  if (!text) return []
  const lower = text.toLowerCase()
  const result = []
  const seen = new Set()

  for (const definition of ORDER_FIELDS) {
    let position = -1
    for (const alias of definition.aliases) {
      const found = lower.indexOf(alias.toLowerCase())
      if (found !== -1 && (position === -1 || found < position)) position = found
    }
    if (position === -1) continue

    let key = definition.key
    let label = definition.label
    const before = text.slice(Math.max(0, position - 48), position)
    const groupMatch = before.match(/第\s*(\d+)\s*条[^。；;]*$/)
    if (groupMatch) {
      const index = Math.max(0, Number(groupMatch[1]) - 1)
      key = `items[${index}].${definition.key}`
      label = `明细 ${index + 1} · ${definition.label}`
    }

    if (seen.has(key)) continue
    seen.add(key)
    result.push({ ...definition, key, label, required: true })
  }

  return result
}

function humanizePath(path) {
  const itemMatch = String(path).match(/^items\[(\d+)\]\.?(.*)$/)
  if (itemMatch) {
    const index = Number(itemMatch[1])
    const prop = itemMatch[2]
    const item = currentRequest.value.items?.[index] || {}
    const name = item.part_name || (item.part_id ? `物料 #${item.part_id}` : `明细 ${index + 1}`)
    const propLabel =
      {
        quantity: '数量',
        part_id: '物料 ID',
        part_name: '物料名称',
        part_code: '物料编码',
        target_unit_price: '目标单价',
        part: '物料',
      }[prop] || prop
    return `${name} · ${propLabel}`
  }
  return (
    {
      warehouse_location: '收货仓库',
      budget: '预算上限',
      required_by: '要求到货日期',
      preferred_supplier_id: '指定供应商',
      priority: '选商偏好',
      note: '补充说明',
      items: '采购物料',
    }[path] || path
  )
}

function buildClarificationFields(paths) {
  const result = []
  const seen = new Set()

  for (const path of paths || []) {
    const itemMatch = String(path).match(/^items\[(\d+)\]\.?(.*)$/)
    if (itemMatch) {
      const index = Number(itemMatch[1])
      const prop = itemMatch[2]
      const item = currentRequest.value.items?.[index] || {}
      const name = item.part_name || (item.part_id ? `物料 #${item.part_id}` : `明细 ${index + 1}`)
      let definition = null

      if (prop === 'quantity') {
        definition = {
          key: path,
          label: `${name} · 数量`,
          type: 'number',
          min: 1,
          placeholder: '例如 10',
          current: item.quantity,
        }
      } else if (prop === 'part_id' || prop === 'part') {
        definition = {
          key: path,
          label: `${name} · 物料 ID`,
          type: 'number',
          min: 1,
          placeholder: '例如 67',
          current: item.part_id,
        }
      } else if (prop === 'part_name') {
        definition = {
          key: path,
          label: `${name} · 物料名称`,
          type: 'text',
          current: item.part_name,
        }
      } else if (prop === 'part_code') {
        definition = {
          key: path,
          label: `${name} · 物料编码`,
          type: 'text',
          current: item.part_code,
        }
      } else if (prop === 'target_unit_price') {
        definition = {
          key: path,
          label: `${name} · 目标单价（元）`,
          type: 'number',
          min: 0,
          step: '0.01',
          current: item.target_unit_price,
        }
      }

      if (definition && !seen.has(path)) {
        seen.add(path)
        result.push({ ...definition, required: true })
      }
      continue
    }

    const definition = CLARIFY_FIELDS[path]
    if (definition && !seen.has(path)) {
      seen.add(path)
      result.push({
        ...definition,
        current: currentRequest.value[path],
        required: path !== 'note' && path !== 'preferred_supplier_id',
        full: definition.type === 'textarea',
      })
    }
  }

  return result
}

const fields = computed(() =>
  isClarification.value
    ? buildClarificationFields(props.data.interrupt?.missing_fields || [])
    : detectOrderFields(props.data.missing_fields),
)

const hasForm = computed(() => fields.value.length > 0)

const missingChips = computed(() => {
  const labels = fields.value.map((field) => field.label)
  if (!isClarification.value) {
    if (labels.length) return [...new Set(labels)]
    return String(props.data.missing_fields || '')
      .split(/[；;、,，]/)
      .map((item) => item.trim())
      .filter(Boolean)
  }

  const covered = new Set(fields.value.map((field) => field.key))
  const extra = (props.data.interrupt?.missing_fields || [])
    .filter((path) => !covered.has(path))
    .map((path) => humanizePath(path))
  return [...new Set([...labels, ...extra])]
})

watch(
  fields,
  (list) => {
    Object.keys(values).forEach((key) => delete values[key])
    Object.keys(errors).forEach((key) => delete errors[key])
    list.forEach((field) => {
      values[field.key] =
        field.current === null || field.current === undefined ? '' : String(field.current)
    })
    if (!list.length) mode.value = 'text'
  },
  { immediate: true },
)

/* ============================================================
   已收集数据
   ============================================================ */
const collectedObject = computed(() => {
  const raw = props.data.collected_data
  if (!raw) return null
  if (typeof raw === 'object') return raw
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
})

const collectedEntries = computed(() => {
  const object = collectedObject.value
  if (!object) return []
  return Object.entries(object)
    .filter(([key]) => key !== 'orderDetail' && key !== 'order_detail')
    .map(([key, value]) => {
      const isObject = value && typeof value === 'object'
      return {
        key,
        label:
          {
            orderNumber: '订单编号',
            supplierId: '供应商 ID',
            supplierName: '供应商',
            totalAmount: '总金额',
            expectedDeliveryDate: '预计交货',
            order_id: '订单 ID',
            create_data: '订单草稿',
            update_data: '修改内容',
            remark: '备注',
            status: '状态',
          }[key] || key,
        value: isObject ? prettyJson(value) : String(value ?? '—'),
        mono: isObject,
      }
    })
})

const collectedPlain = computed(() => {
  const raw = props.data.collected_data
  if (!raw) return ''
  if (typeof raw === 'string' && !collectedObject.value) return raw
  return ''
})

/* ============================================================
   校验与提交
   ============================================================ */
function validateForm() {
  Object.keys(errors).forEach((key) => delete errors[key])
  let valid = true
  for (const field of fields.value) {
    const value = String(values[field.key] ?? '').trim()
    if (field.required && !value) {
      errors[field.key] = '请填写该字段'
      valid = false
      continue
    }
    if (field.type === 'number' && value) {
      const number = Number(value)
      if (Number.isNaN(number)) {
        errors[field.key] = '请输入数字'
        valid = false
      } else if (field.min !== undefined && number < field.min) {
        errors[field.key] = `不能小于 ${field.min}`
        valid = false
      }
    }
  }
  return valid
}

function buildSupplementText() {
  const topLevel = []
  const groups = new Map()

  for (const field of fields.value) {
    const rawValue = String(values[field.key] ?? '').trim()
    if (!rawValue) continue
    const isMoney = /unitPrice$|budget$/.test(field.key)
    const value =
      isMoney && Number.isFinite(Number(rawValue))
        ? Number(rawValue).toFixed(2)
        : rawValue
    const match = field.key.match(/^items\[(\d+)\]\./)
    if (match) {
      const index = Number(match[1])
      if (!groups.has(index)) groups.set(index, [])
      groups.get(index).push(`${field.label.replace(/^明细 \d+ · /, '')}：${value}`)
    } else {
      topLevel.push(`${field.label}：${value}`)
    }
  }

  const parts = []
  if (topLevel.length) parts.push(topLevel.join('\n'))
  for (const [index, lines] of [...groups.entries()].sort((a, b) => a[0] - b[0])) {
    parts.push(`第 ${index + 1} 条明细：\n${lines.join('\n')}`)
  }
  if (extraText.value.trim()) parts.push(`补充说明：${extraText.value.trim()}`)
  return parts.join('\n\n')
}

function buildClarificationPatch() {
  const patch = {}
  const items = JSON.parse(JSON.stringify(currentRequest.value.items || []))
  let itemsChanged = false
  let changed = false

  for (const field of fields.value) {
    const raw = values[field.key]
    const value = typeof raw === 'string' ? raw.trim() : raw
    if (value === '' || value === null || value === undefined) continue
    const typed = field.type === 'number' ? Number(value) : value

    const match = field.key.match(/^items\[(\d+)\]\.?(.*)$/)
    if (match) {
      const index = Number(match[1])
      const prop = match[2] || 'part_name'
      while (items.length <= index) items.push({})
      const key = prop === 'part' ? 'part_name' : prop
      items[index][key] = typed
      itemsChanged = true
      changed = true
    } else {
      patch[field.key] = typed
      changed = true
    }
  }

  if (itemsChanged) patch.items = items
  return { patch, changed }
}

function submit() {
  if (props.submitting) return
  formError.value = ''

  if (mode.value === 'text' || !hasForm.value) {
    const text = textValue.value.trim()
    if (!text) {
      formError.value = '请先填写需要补充的内容。'
      fallbackRef.value?.focus()
      return
    }
    emit('resume', isClarification.value ? { text } : { supplement: text })
    return
  }

  if (!validateForm()) {
    formError.value = '还有必填字段未完成，请检查标红项。'
    return
  }

  if (isClarification.value) {
    const { patch, changed } = buildClarificationPatch()
    if (!changed || Object.keys(patch).length === 0) {
      formError.value = '没有检测到需要提交的修改。'
      return
    }
    emit('resume', { request_patch: patch })
  } else {
    emit('resume', { supplement: buildSupplementText() })
  }
}

const submitLabel = computed(() =>
  isClarification.value ? '提交补充并重新分析' : '提交补充并继续',
)

onMounted(async () => {
  await nextTick()
  const target = document.querySelector(
    '.supplement-panel .hitl-input, .supplement-panel .hitl-textarea, .supplement-panel .hitl-select',
  )
  target?.focus({ preventScroll: true })
})
</script>

<style scoped>
.form-error {
  margin-top: 12px;
}
</style>
