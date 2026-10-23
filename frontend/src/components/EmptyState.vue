<template>
  <div class="empty-state">
    <div class="empty-mark" aria-hidden="true">
      <Boxes :size="24" :stroke-width="1.8" />
    </div>

    <h1 class="empty-title">今天想采购什么？</h1>
    <p class="empty-sub">
      我可以帮你比价、查库存、创建订单，或者跑完整个采购流程。
    </p>

    <div class="suggestion-grid">
      <button
        v-for="item in suggestions"
        :key="item.title"
        class="suggestion"
        type="button"
        :title="`填入：${item.prompt}`"
        @click="$emit('pick', item.prompt)"
      >
        <span class="suggestion-icon" aria-hidden="true">
          <component :is="item.icon" :size="16" :stroke-width="1.9" />
        </span>
        <span class="suggestion-copy">
          <strong>{{ item.title }}</strong>
          <em>{{ item.desc }}</em>
        </span>
        <ArrowUpRight :size="14" class="suggestion-arrow" aria-hidden="true" />
      </button>
    </div>

    <p class="empty-foot">
      <Sparkles :size="13" />
      多代理协作 · 关键操作会先请你确认
    </p>
  </div>
</template>

<script setup>
import {
  ArrowUpRight,
  Boxes,
  Handshake,
  ShoppingCart,
  Sparkles,
  TriangleAlert,
  Workflow,
} from '@lucide/vue'

defineEmits(['pick'])

const suggestions = [
  {
    title: '供应商比价',
    desc: '对比报价与交期，给出选型建议',
    prompt: '对比 NGK 铱金火花塞的供应商报价和交期',
    icon: Handshake,
  },
  {
    title: '库存预警',
    desc: '找出低于安全库存的物料',
    prompt: '查一下当前低于安全库存的物料',
    icon: TriangleAlert,
  },
  {
    title: '创建订单',
    desc: '补齐字段后提交人工审批',
    prompt: '帮我创建一笔采购订单：物料 67，数量 10',
    icon: ShoppingCart,
  },
  {
    title: '完整采购任务',
    desc: '分析 → 审批 → 下单 → 核验',
    prompt: '启动完整采购任务，分析方案后等我审批',
    icon: Workflow,
  },
]
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 700px;
  margin: 0 auto;
  padding: 24px 8px;
  text-align: center;
  animation: fadeUp var(--dur-slow) var(--ease);
}

.empty-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  margin-bottom: 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 18%, var(--line));
  border-radius: 16px;
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: var(--shadow-xs);
}

.empty-title {
  color: var(--ink);
  font-size: 25px;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.3;
}

.empty-sub {
  max-width: 460px;
  margin-top: 8px;
  color: var(--ink-2);
  font-size: 13.5px;
  line-height: 1.7;
}

.suggestion-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
  margin-top: 30px;
  text-align: left;
}

.suggestion {
  display: flex;
  text-align: left;
  align-items: flex-start;
  gap: 10px;
  padding: 13px 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  transition: border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease);
}

.suggestion:hover {
  border-color: color-mix(in srgb, var(--accent) 34%, var(--line));
  background: var(--accent-softer);
  box-shadow: var(--shadow-xs);
}

.suggestion-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--ink-2);
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.suggestion:hover .suggestion-icon {
  background: var(--accent-soft);
  color: var(--accent);
}

.suggestion-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.suggestion-copy strong {
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
}

.suggestion-copy em {
  color: var(--ink-3);
  font-size: 11.5px;
  font-style: normal;
  line-height: 1.5;
}

.suggestion-arrow {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--ink-3);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
}

.suggestion:hover .suggestion-arrow {
  opacity: 1;
  transform: translate(1px, -1px);
}

.empty-foot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 22px;
  color: var(--ink-3);
  font-size: 11.5px;
}

@media (max-width: 640px) {
  .empty-state {
    padding: 8px 0;
  }

  .empty-title {
    font-size: 22px;
  }

  .empty-sub {
    font-size: 13px;
  }

  .suggestion-grid {
    grid-template-columns: 1fr;
    margin-top: 22px;
  }

  .suggestion:nth-child(n + 3) {
    display: none;
  }
}
</style>
