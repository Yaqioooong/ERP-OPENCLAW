<template>
  <div class="retry-panel">
    <div class="hitl-section">
      <div class="retry-error">
        <div class="retry-error-icon" aria-hidden="true">
          <TriangleAlert :size="16" />
        </div>
        <div class="retry-error-copy">
          <div class="retry-error-title">{{ errorTitle }}</div>
          <p class="retry-error-message">{{ errorMessage }}</p>
          <div class="hitl-meta" style="margin-top: 6px">
            <span v-if="errorCode" class="hitl-chip">错误码 {{ errorCode }}</span>
            <span v-if="retryFrom" class="hitl-chip">中断于 {{ retryFrom }}</span>
            <span v-if="data.task_id" class="hitl-chip">任务 {{ shortId(data.task_id, 9, 4) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="hitl-section">
      <div class="hitl-callout">
        <RotateCcw :size="14" />
        <div>
          <strong>重试</strong>会从失败步骤继续，已经完成的步骤不会重复执行；
          <strong>取消</strong>会终止这笔采购任务，之后无法再恢复。
        </div>
      </div>
    </div>

    <details v-if="rawDetails" class="hitl-details">
      <summary>
        <FileText :size="13" />
        查看技术详情
      </summary>
      <div class="hitl-details-body">
        <pre class="hitl-json">{{ rawDetails }}</pre>
      </div>
    </details>

    <div v-if="cancelOpen" class="hitl-confirm">
      <div class="hitl-confirm-head">
        <TriangleAlert :size="14" />
        确认取消这笔采购任务？
      </div>
      <p>取消后任务会进入终止状态，需要重新创建任务才能继续采购。</p>
      <div class="hitl-confirm-actions">
        <button type="button" class="btn btn-ghost btn-sm" @click="cancelOpen = false">
          返回
        </button>
        <button
          type="button"
          class="btn btn-danger btn-sm"
          :disabled="submitting"
          @click="submitCancel"
        >
          确认取消任务
        </button>
      </div>
    </div>

    <div v-else class="hitl-foot">
      <span class="hitl-foot-note">
        <ShieldCheck :size="12" />
        失败步骤可安全重试
      </span>
      <div class="hitl-foot-actions">
        <button
          v-if="canCancel"
          type="button"
          class="btn btn-danger-ghost"
          :disabled="submitting"
          @click="cancelOpen = true"
        >
          取消任务
        </button>
        <button
          v-if="canRetry"
          type="button"
          class="btn btn-primary"
          :disabled="submitting"
          @click="submitRetry"
        >
          <span v-if="submitting" class="spinner" aria-hidden="true" />
          {{ submitting ? '正在恢复…' : '重试' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import {
  FileText,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from '@lucide/vue'
import { prettyJson, shortId } from '../../utils/format'

const props = defineProps({
  data: { type: Object, required: true },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['resume'])
const cancelOpen = ref(false)

const errorObject = computed(() => {
  const raw = props.data.last_error
  if (!raw) return {}
  if (typeof raw === 'object') return raw
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : { message: String(raw) }
  } catch {
    return { message: String(raw) }
  }
})

const errorCode = computed(() => errorObject.value.code || errorObject.value.error_code || '')
const retryFrom = computed(() => errorObject.value.retry_from || props.data.retry_from || '')

const CODE_LABELS = {
  ORDER_CREATE_FAILED: '创建订单失败',
  ORDER_UPDATE_FAILED: '修改订单失败',
  ORDER_EXECUTION_FAILED: '订单执行失败',
  RECONCILIATION_FAILED: '订单核验失败',
  APPROVAL_EXPIRED: '审批已过期',
  CHECKPOINT_MISSING: '任务检查点缺失',
  TASK_NOT_FOUND: '任务不存在',
  MCP_ERROR: 'ERP 接口调用失败',
  TOOL_ERROR: '工具调用失败',
}

const errorTitle = computed(() => {
  if (errorCode.value && CODE_LABELS[errorCode.value]) return CODE_LABELS[errorCode.value]
  return '任务在执行中断'
})

const errorMessage = computed(() => {
  const message = errorObject.value.message || errorObject.value.detail
  if (message) return message
  return '上一次执行没有完成。你可以重试，或取消这笔任务。'
})

const rawDetails = computed(() => {
  const raw = props.data.last_error
  if (!raw) return ''
  return prettyJson(raw)
})

const options = computed(() => props.data.options || ['retry', 'cancel'])
const canRetry = computed(() => options.value.includes('retry'))
const canCancel = computed(() => options.value.includes('cancel'))

function submitRetry() {
  if (props.submitting) return
  emit('resume', { action: 'retry' })
}

function submitCancel() {
  if (props.submitting) return
  cancelOpen.value = false
  emit('resume', { action: 'cancel' })
}
</script>

<style scoped>
.retry-error {
  display: flex;
  gap: 11px;
  padding: 13px;
  border: 1px solid color-mix(in srgb, var(--danger) 22%, var(--line));
  border-radius: var(--r-sm);
  background: var(--danger-soft);
}

.retry-error-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger) 14%, transparent);
  color: var(--danger);
}

.retry-error-copy {
  min-width: 0;
}

.retry-error-title {
  color: var(--danger);
  font-size: 12.5px;
  font-weight: 600;
}

.retry-error-message {
  margin-top: 3px;
  color: color-mix(in srgb, var(--danger) 78%, var(--ink));
  font-size: 11.5px;
  line-height: 1.65;
  word-break: break-word;
}
</style>
