<template>
  <section class="process" :class="{ 'is-open': open, 'is-running': running }">
    <button
      class="process-head"
      type="button"
      :aria-expanded="open"
      @click="toggleOpen"
    >
      <span class="process-icon" :class="{ spinning: running }" aria-hidden="true">
        <LoaderCircle v-if="running" :size="13" />
        <Check v-else :size="13" />
      </span>
      <span class="process-title">
        {{ running ? '正在执行' : '执行过程' }}
        <span class="process-count">{{ items.length }} 步</span>
        <span v-if="durationLabel" class="process-duration">· {{ durationLabel }}</span>
      </span>
      <span v-if="summary" class="process-summary">{{ summary }}</span>
      <ChevronDown :size="14" class="process-chev" aria-hidden="true" />
    </button>

    <div v-show="open" class="steps">
      <div
        v-for="(item, index) in items"
        :key="item.id || index"
        class="step"
        :class="stepState(item)"
      >
        <div class="step-rail" aria-hidden="true">
          <span class="step-dot">
            <LoaderCircle v-if="stepState(item) === 'running'" :size="9" class="spin" />
            <Check v-else :size="9" />
          </span>
        </div>

        <div class="step-main">
          <button
            class="step-head"
            type="button"
            :aria-expanded="isStepOpen(item, index)"
            @click="toggleStep(item, index)"
          >
            <component :is="metaFor(item).icon" :size="13" class="step-tool-icon" />
            <span class="step-name">{{ metaFor(item).label }}</span>
            <span v-if="subAgent(item)" class="step-source">{{ sourceName(item.source) }}</span>
            <span class="step-status">
              {{ stepState(item) === 'running' ? '执行中' : '完成' }}
            </span>
            <ChevronDown
              :size="12"
              class="step-chev"
              :class="{ open: isStepOpen(item, index) }"
            />
          </button>

          <div v-if="stepState(item) === 'running' && !hasContent(item)" class="step-progress">
            <span />
          </div>

          <div v-if="isStepOpen(item, index)" class="step-detail">
            <div class="step-tabs" role="tablist" aria-label="工具调用详情">
              <button
                v-if="item.args"
                type="button"
                role="tab"
                class="step-tab"
                :class="{ active: currentTab(item, index) === 'args' }"
                :aria-selected="currentTab(item, index) === 'args'"
                @click="setTab(index, 'args')"
              >
                参数
              </button>
              <button
                type="button"
                role="tab"
                class="step-tab"
                :class="{ active: currentTab(item, index) === 'result' }"
                :aria-selected="currentTab(item, index) === 'result'"
                @click="setTab(index, 'result')"
              >
                结果
              </button>
              <span class="step-tab-spacer" />
              <button
                v-if="item.text"
                type="button"
                class="step-copy"
                @click="copyResult(item)"
              >
                <Copy :size="11" />
                复制
              </button>
            </div>

            <pre v-if="currentTab(item, index) === 'args'" class="step-code">{{
              prettyJson(item.args)
            }}</pre>

            <div v-else class="step-result">
              <MarkdownRenderer v-if="item.text" :content="cleanToolText(item.text)" />
              <p v-else class="step-empty">没有文本结果</p>

              <div v-if="item.images && item.images.length" class="step-images">
                <button
                  v-for="(image, imageIndex) in item.images"
                  :key="imageIndex"
                  type="button"
                  class="step-image"
                  :aria-label="`查看第 ${imageIndex + 1} 张图片`"
                  @click="openImage(image)"
                >
                  <img :src="image" :alt="`工具结果图 ${imageIndex + 1}`" loading="lazy" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import {
  Check,
  ChevronDown,
  Copy,
  LoaderCircle,
} from '@lucide/vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import { useToast } from '../composables/useToast'
import { formatDuration, prettyJson } from '../utils/format'
import {
  cleanToolText,
  isSubAgent,
  sourceName,
  toolMeta,
} from '../utils/tools'

const props = defineProps({
  items: { type: Array, default: () => [] },
})

const { toast } = useToast()

const open = ref(false)
const userToggled = ref(false)
const expanded = ref([])
const tabs = reactive({})
const now = ref(Date.now())
let timer = null

const running = computed(() =>
  props.items.some((item) => item.tool_status === 'calling'),
)

const hasImages = computed(() => props.items.some((item) => item.images?.length))

const starts = computed(() =>
  props.items.map((item) => item.started_at).filter((value) => !!value),
)

const durationLabel = computed(() => {
  if (!starts.value.length) return ''
  const start = Math.min(...starts.value)
  const end = running.value
    ? now.value
    : Math.max(...props.items.map((item) => item.finished_at || 0), start)
  if (!end || end <= start) return ''
  return formatDuration(end - start)
})

const summary = computed(() => {
  const sources = [
    ...new Set(
      props.items
        .filter((item) => isSubAgent(item.source))
        .map((item) => sourceName(item.source)),
    ),
  ]
  if (sources.length) return sources.join(' · ')
  const labels = [...new Set(props.items.map((item) => toolMeta(item.tool_name).label))]
  if (labels.length <= 2) return labels.join(' · ')
  return `${props.items.length} 个工具`
})

function startTimer() {
  stopTimer()
  now.value = Date.now()
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

function stopTimer() {
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
}

watch(
  running,
  (value) => {
    if (!userToggled.value) {
      open.value = value || hasImages.value
    }
    if (value) startTimer()
    else stopTimer()
  },
  { immediate: true },
)

watch(
  hasImages,
  (value) => {
    if (!value) return
    open.value = true
    props.items.forEach((item, index) => {
      if (item.images?.length && !expanded.value.includes(index)) {
        expanded.value.push(index)
        tabs[index] = 'result'
      }
    })
  },
  { immediate: true },
)

onUnmounted(stopTimer)

function toggleOpen() {
  userToggled.value = true
  open.value = !open.value
}

function stepState(item) {
  return item.tool_status === 'calling' ? 'running' : 'done'
}

function hasContent(item) {
  return !!(item.text || (item.images && item.images.length))
}

function metaFor(item) {
  return toolMeta(item.tool_name)
}

function subAgent(item) {
  return isSubAgent(item.source)
}

function isStepOpen(item, index) {
  return expanded.value.includes(index)
}

function toggleStep(item, index) {
  const position = expanded.value.indexOf(index)
  if (position === -1) {
    expanded.value.push(index)
    if (!tabs[index]) tabs[index] = item.text ? 'result' : 'args'
  } else {
    expanded.value.splice(position, 1)
  }
}

function currentTab(item, index) {
  if (tabs[index]) return tabs[index]
  return item.text ? 'result' : 'args'
}

function setTab(index, tab) {
  tabs[index] = tab
}

function openImage(src) {
  window.open(src, '_blank', 'noopener')
}

async function copyResult(item) {
  try {
    await navigator.clipboard.writeText(cleanToolText(item.text))
    toast('已复制工具结果', { tone: 'success' })
  } catch {
    toast('复制失败，请手动选择', { tone: 'danger' })
  }
}
</script>

<style scoped>
.process {
  margin: 10px 0 4px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  overflow: hidden;
  transition: border-color var(--dur-fast) var(--ease);
}

.process.is-running {
  border-color: color-mix(in srgb, var(--accent) 26%, var(--line));
}

.process-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 38px;
  padding: 0 12px;
  text-align: left;
  transition: background var(--dur-fast) var(--ease);
}

.process-head:hover {
  background: var(--surface-2);
}

.process-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
}

.process-icon.spinning svg {
  animation: spin 0.9s linear infinite;
}

.process-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 550;
  white-space: nowrap;
}

.process-count {
  color: var(--ink-3);
  font-weight: 450;
}

.process-duration {
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  font-weight: 450;
}

.process-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink-3);
  font-size: 11.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.process-chev {
  flex-shrink: 0;
  color: var(--ink-3);
  transition: transform var(--dur) var(--ease);
}

.process.is-open .process-chev {
  transform: rotate(180deg);
}

/* 时间线 */
.steps {
  position: relative;
  padding: 2px 12px 10px;
  border-top: 1px solid var(--line-soft);
}

.step {
  position: relative;
  display: flex;
  gap: 8px;
  padding: 3px 0;
}

.step-rail {
  position: relative;
  display: flex;
  justify-content: center;
  width: 20px;
  flex-shrink: 0;
  padding-top: 6px;
}

.step-rail::after {
  content: '';
  position: absolute;
  top: 24px;
  bottom: -6px;
  left: 50%;
  width: 1px;
  background: var(--line);
}

.step:last-child .step-rail::after {
  display: none;
}

.step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
}

.step-dot .spin {
  animation: spin 0.9s linear infinite;
}

.step.running .step-dot {
  background: var(--accent);
  color: var(--on-accent);
  animation: dotPulse 1.6s var(--ease) infinite;
}

.step-main {
  flex: 1;
  min-width: 0;
  padding-top: 1px;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 26px;
  padding: 2px 4px;
  border-radius: 6px;
  text-align: left;
}

.step-head:hover {
  background: var(--surface-2);
}

.step-tool-icon {
  flex-shrink: 0;
  color: var(--ink-3);
}

.step-name {
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-source {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--r-full);
  background: var(--surface-2);
  color: var(--ink-3);
  font-size: 10px;
  white-space: nowrap;
}

.step-status {
  margin-left: auto;
  color: var(--ink-3);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.step.running .step-status {
  color: var(--accent);
}

.step-chev {
  flex-shrink: 0;
  color: var(--ink-3);
  transition: transform var(--dur) var(--ease);
}

.step-chev.open {
  transform: rotate(180deg);
}

/* 进度条 */
.step-progress {
  position: relative;
  height: 2px;
  margin: 4px 4px 2px;
  border-radius: 2px;
  background: var(--surface-2);
  overflow: hidden;
}

.step-progress span {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 36%;
  border-radius: 2px;
  background: var(--accent);
  animation: indeterminate 1.4s var(--ease) infinite;
}

/* 详情 */
.step-detail {
  margin: 4px 0 4px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-xs);
  background: var(--surface-2);
  overflow: hidden;
}

.step-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 5px 6px;
  border-bottom: 1px solid var(--line-soft);
}

.step-tab {
  height: 22px;
  padding: 0 8px;
  border-radius: 5px;
  color: var(--ink-3);
  font-size: 10.5px;
  font-weight: 550;
}

.step-tab:hover {
  color: var(--ink-2);
}

.step-tab.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow-xs);
}

.step-tab-spacer {
  flex: 1;
}

.step-copy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 7px;
  border-radius: 5px;
  color: var(--ink-3);
  font-size: 10.5px;
}

.step-copy:hover {
  background: var(--surface);
  color: var(--ink-2);
}

.step-code {
  margin: 0;
  padding: 10px 12px;
  max-height: 260px;
  overflow: auto;
  background: transparent;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.step-result {
  padding: 10px 12px 12px;
}

.step-result :deep(.md-body) {
  font-size: 13px;
  line-height: 1.7;
}

.step-result :deep(.md-body table) {
  font-size: 12px;
}

.step-empty {
  color: var(--ink-3);
  font-size: 12px;
}

.step-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.step-image {
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-xs);
  background: var(--surface);
  overflow: hidden;
}

.step-image img {
  display: block;
  max-width: 100%;
  max-height: 280px;
  width: auto;
  height: auto;
  object-fit: contain;
}

.step-image:hover {
  border-color: var(--line-strong);
}

@keyframes dotPulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 34%, transparent);
  }
  50% {
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 0%, transparent);
  }
}

@keyframes indeterminate {
  0% {
    left: -36%;
  }
  100% {
    left: 100%;
  }
}

@media (max-width: 640px) {
  .process-summary {
    display: none;
  }
}
</style>
