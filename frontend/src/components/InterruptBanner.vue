<template>
  <section
    ref="cardRef"
    class="hitl-card"
    :class="`tone-${config.tone}`"
    :aria-busy="submitting"
    role="group"
    :aria-labelledby="titleId"
    tabindex="-1"
  >
    <div v-if="submitting" class="hitl-progress" aria-hidden="true">
      <span />
    </div>

    <header class="hitl-head">
      <div class="hitl-icon" aria-hidden="true">
        <component :is="config.icon" :size="17" :stroke-width="1.9" />
      </div>

      <div class="hitl-heading">
        <span class="hitl-eyebrow">{{ config.eyebrow }}</span>
        <h2 :id="titleId" class="hitl-title">{{ config.title }}</h2>
        <p class="hitl-subtitle">{{ config.subtitle }}</p>
      </div>

      <span class="hitl-status">
        <span class="hitl-status-dot" aria-hidden="true" />
        {{ config.chip }}
      </span>
    </header>

    <div class="hitl-body">
      <SupplementPanel
        v-if="config.panel === 'supplement'"
        :data="interruptData"
        :kind="config.supplementKind"
        :submitting="submitting"
        @resume="$emit('resume', $event)"
      />

      <ApprovalPanel
        v-else-if="config.panel === 'approval'"
        :data="interruptData"
        :kind="config.approvalKind"
        :submitting="submitting"
        @resume="$emit('resume', $event)"
      />

      <RetryPanel
        v-else-if="config.panel === 'retry'"
        :data="interruptData"
        :submitting="submitting"
        @resume="$emit('resume', $event)"
      />

      <div v-else class="unknown-panel">
        <p class="hitl-note">
          当前前端还不认识这种中断类型，原始数据如下。你可以复制后交给管理员处理。
        </p>
        <pre class="hitl-json">{{ rawValue }}</pre>
        <div class="hitl-foot">
          <span class="hitl-foot-note">
            <CircleHelp :size="12" />
            未执行任何操作
          </span>
          <div class="hitl-foot-actions">
            <button type="button" class="btn btn-ghost" @click="copyRaw">
              <Copy :size="13" />
              复制原始数据
            </button>
          </div>
        </div>
      </div>

      <div v-if="error" class="hitl-callout danger submit-error" role="alert">
        <TriangleAlert :size="14" />
        <div>
          <strong>提交没有成功</strong>
          <p>{{ error }}</p>
          <p class="submit-error-hint">可以检查网络后重试，当前填写内容不会丢失。</p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  CircleHelp,
  ClipboardCheck,
  ClipboardList,
  Copy,
  FilePenLine,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from '@lucide/vue'
import SupplementPanel from './hitl/SupplementPanel.vue'
import ApprovalPanel from './hitl/ApprovalPanel.vue'
import RetryPanel from './hitl/RetryPanel.vue'
import { useToast } from '../composables/useToast'
import { prettyJson } from '../utils/format'

const props = defineProps({
  interruptData: { type: Object, required: true },
  submitting: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

defineEmits(['resume'])

const { toast } = useToast()
const cardRef = ref(null)
const titleId = `hitl-title-${Math.random().toString(36).slice(2, 9)}`

const config = computed(() => {
  const type = props.interruptData.interrupt_type
  if (type === 'order_info_supplement') {
    return {
      panel: 'supplement',
      supplementKind: 'order',
      tone: 'warn',
      icon: FilePenLine,
      eyebrow: 'DATA SUPPLEMENT',
      title: '订单数据还差几项',
      subtitle: '补齐后 Agent 会继续执行，不会猜测缺失字段。',
      chip: '需补充',
    }
  }
  if (type === 'procurement_task_clarification') {
    return {
      panel: 'supplement',
      supplementKind: 'clarification',
      tone: 'warn',
      icon: ClipboardList,
      eyebrow: 'PROCUREMENT BRIEF',
      title: '补充采购条件',
      subtitle: '提交后会按新条件重新分析，再进入审批。',
      chip: '需补充',
    }
  }
  if (type === 'hitl_approval') {
    const action = props.interruptData.action_requests?.[0]?.name
    return {
      panel: 'approval',
      approvalKind: 'order',
      tone: 'accent',
      icon: ShieldCheck,
      eyebrow: 'HUMAN REVIEW',
      title: action === 'order_update' ? '确认修改采购订单' : '确认创建采购订单',
      subtitle: '字段已完整，确认后才会调用 ERP 接口。',
      chip: '待确认',
    }
  }
  if (type === 'procurement_task_approval') {
    return {
      panel: 'approval',
      approvalKind: 'procurement',
      tone: 'accent',
      icon: ClipboardCheck,
      eyebrow: 'PURCHASE APPROVAL',
      title: '采购方案待你审批',
      subtitle: '批准后创建订单并进入核验，数量可以调整。',
      chip: '待审批',
    }
  }
  if (type === 'procurement_task_retry') {
    return {
      panel: 'retry',
      tone: 'danger',
      icon: RotateCcw,
      eyebrow: 'TASK RECOVERY',
      title: '采购任务需要恢复',
      subtitle: '上一次执行中断，可以重试或终止任务。',
      chip: '待处理',
    }
  }
  return {
    panel: 'unknown',
    tone: 'neutral',
    icon: CircleHelp,
    eyebrow: 'AGENT PAUSED',
    title: 'Agent 已暂停',
    subtitle: '收到一个当前前端未识别的中断类型。',
    chip: '未知状态',
  }
})

const rawValue = computed(() => {
  const value =
    props.interruptData.interrupt_value ??
    props.interruptData.interrupt ??
    props.interruptData
  if (typeof value === 'string') return value
  return prettyJson(value)
})

async function copyRaw() {
  try {
    await navigator.clipboard.writeText(rawValue.value)
    toast('已复制原始数据', { tone: 'success' })
  } catch {
    toast('复制失败，请手动选择', { tone: 'danger' })
  }
}

onMounted(async () => {
  if (config.value.panel === 'supplement') return
  await nextTick()
  cardRef.value?.focus({ preventScroll: true })
})
</script>

<style scoped>
.hitl-card {
  position: relative;
  margin: 14px 0 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  overflow: hidden;
  animation: fadeUp var(--dur-slow) var(--ease);
  outline: none;
}

.hitl-card.tone-warn {
  --tone: var(--warn);
  --tone-soft: var(--warn-soft);
  box-shadow: inset 3px 0 0 var(--tone), var(--shadow-xs);
}

.hitl-card.tone-accent {
  --tone: var(--accent);
  --tone-soft: var(--accent-soft);
  box-shadow: inset 3px 0 0 var(--tone), var(--shadow-xs);
}

.hitl-card.tone-danger {
  --tone: var(--danger);
  --tone-soft: var(--danger-soft);
  box-shadow: inset 3px 0 0 var(--tone), var(--shadow-xs);
}

.hitl-card.tone-neutral {
  --tone: var(--ink-3);
  --tone-soft: var(--surface-2);
}

.hitl-progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--surface-2);
  overflow: hidden;
  z-index: 2;
}

.hitl-progress span {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 32%;
  background: var(--tone);
  animation: hitlProgress 1.2s var(--ease) infinite;
}

.hitl-head {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 17px 20px 14px;
}

.hitl-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 10px;
  background: var(--tone-soft);
  color: var(--tone);
}

.hitl-heading {
  flex: 1;
  min-width: 0;
}

.hitl-eyebrow {
  display: block;
  color: var(--ink-3);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.1em;
}

.hitl-title {
  margin-top: 2px;
  color: var(--ink);
  font-size: 15.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.35;
}

.hitl-subtitle {
  margin-top: 3px;
  color: var(--ink-2);
  font-size: 12px;
  line-height: 1.6;
}

.hitl-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  margin-top: 2px;
  flex-shrink: 0;
  border-radius: var(--r-full);
  background: var(--tone-soft);
  color: var(--tone);
  font-size: 10.5px;
  font-weight: 600;
  white-space: nowrap;
}

.hitl-status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.hitl-body {
  padding: 0 20px 18px;
}

.unknown-panel .hitl-json {
  margin-top: 10px;
}

.submit-error {
  margin-top: 14px;
}

.submit-error p {
  margin-top: 2px;
  color: inherit;
  font-size: 11.5px;
  line-height: 1.6;
}

.submit-error-hint {
  opacity: 0.75;
}

@keyframes hitlProgress {
  0% {
    left: -32%;
  }
  100% {
    left: 100%;
  }
}

@media (max-width: 640px) {
  .hitl-head {
    padding: 14px 14px 12px;
  }

  .hitl-body {
    padding: 0 14px 14px;
  }

  .hitl-status {
    display: none;
  }

  .hitl-subtitle {
    font-size: 11.5px;
  }
}
</style>
