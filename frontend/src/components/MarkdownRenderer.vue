<template>
  <div ref="rootRef" class="md-body" v-html="renderedContent" />
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import { useToast } from '../composables/useToast'

const LANGUAGES = {
  bash,
  sh: bash,
  shell: bash,
  css,
  java,
  javascript,
  js: javascript,
  json,
  markdown,
  md: markdown,
  python,
  py: python,
  sql,
  typescript,
  ts: typescript,
  html: xml,
  xml,
  yaml,
  yml: yaml,
}

for (const [name, language] of Object.entries(LANGUAGES)) {
  hljs.registerLanguage(name, language)
}

/**
 * Markdown 渲染
 *
 * v-html 生成的内容不受 scoped CSS 影响，排版统一定义在 styles/markdown.css。
 * 渲染后再做一轮增强：代码块语言标签 + 复制按钮、表格横向滚动容器、图片点击预览。
 */
const props = defineProps({
  content: { type: String, default: '' },
})

const rootRef = ref(null)
const renderedContent = ref('')
const { toast } = useToast()

const IMG_INLINE_STYLE =
  'max-width:100%;max-height:420px;width:auto;height:auto;object-fit:contain;display:block;border-radius:10px;margin:16px 0;border:1px solid var(--line);cursor:zoom-in;'

let md = null

function isKnownImageHost(url) {
  const hosts = [
    'mdn.alipayobjects.com',
    'img.alicdn.com',
    'gw.alipayobjects.com',
    'zos.alipayobjects.com',
  ]
  try {
    const hostname = new URL(url).hostname
    return hosts.some((host) => hostname.includes(host))
  } catch {
    return false
  }
}

function shouldRenderAsImage(src) {
  if (!src) return false
  if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)(\?.*)?$/i.test(src)) return true
  if (/^data:image\//i.test(src)) return true
  if (isKnownImageHost(src)) return true
  return false
}

function buildImgTag(src, alt) {
  return `<img src="${src}" alt="${alt || '图片'}" loading="lazy" style="${IMG_INLINE_STYLE}" />`
}

function createRenderer() {
  const instance = new MarkdownIt({
    html: true,
    xhtmlOut: true,
    breaks: true,
    linkify: true,
    typographer: true,
    highlight(str, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(str, { language: lang }).value
        } catch {
          /* ignore */
        }
      }
      return ''
    },
  })

  instance.renderer.rules.image = (tokens, idx) => {
    const token = tokens[idx]
    const src = token.attrGet('src') || ''
    const alt = token.content
    if (shouldRenderAsImage(src)) return buildImgTag(src, alt)
    return `<a href="${src}" target="_blank" rel="noopener noreferrer" class="image-link">图片：${alt || src}</a>`
  }

  const defaultLinkOpen =
    instance.renderer.rules.link_open ||
    ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

  instance.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet('target', '_blank')
    tokens[idx].attrSet('rel', 'noopener noreferrer')
    return defaultLinkOpen(tokens, idx, options, env, self)
  }

  const defaultTextRenderer =
    instance.renderer.rules.text ||
    ((tokens, idx) => instance.utils.escapeHtml(tokens[idx].content))

  instance.renderer.rules.text = (tokens, idx, options, env, self) => {
    const content = tokens[idx].content
    if (/data:image\/[^;]+;base64,/.test(content)) {
      const match = content.match(/(data:image\/[^;]+;base64,[A-Za-z0-9+/=]+)/)
      if (match) return buildImgTag(match[1], '图片')
    }
    for (const host of ['mdn.alipayobjects.com', 'img.alicdn.com', 'gw.alipayobjects.com']) {
      const regex = new RegExp(`(https?://[^\\s]*${host.replace(/\./g, '\\.')}[^\\s]*)`)
      const match = content.match(regex)
      if (match) return buildImgTag(match[1], '图片')
    }
    return defaultTextRenderer(tokens, idx, options, env, self)
  }

  return instance
}

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function render() {
  if (!md || !props.content) {
    renderedContent.value = escapeHtml(props.content || '')
    return
  }
  try {
    renderedContent.value = md.render(props.content)
  } catch (error) {
    console.error('[MarkdownRenderer] 渲染失败:', error)
    renderedContent.value = escapeHtml(props.content)
  }
}

async function decorate() {
  const root = rootRef.value
  if (!root) return
  await nextTick()

  root.querySelectorAll('table').forEach((table) => {
    if (table.parentElement?.classList.contains('md-table-wrap')) return
    const wrapper = document.createElement('div')
    wrapper.className = 'md-table-wrap'
    table.parentNode.insertBefore(wrapper, table)
    wrapper.appendChild(table)
  })

  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.dataset.enhanced === '1') return
    const code = pre.querySelector('code')
    if (!code) return
    pre.dataset.enhanced = '1'

    const langClass = [...code.classList].find((name) => name.startsWith('language-'))
    if (langClass) {
      const label = document.createElement('span')
      label.className = 'md-lang'
      label.textContent = langClass.replace('language-', '')
      pre.appendChild(label)
    }

    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'md-copy'
    button.textContent = '复制'
    button.setAttribute('aria-label', '复制代码')
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.innerText)
        button.textContent = '已复制'
        toast('已复制代码')
        window.setTimeout(() => {
          button.textContent = '复制'
        }, 1600)
      } catch {
        toast('复制失败，请手动选择', { tone: 'danger' })
      }
    })
    pre.appendChild(button)
  })

  root.querySelectorAll('img').forEach((img) => {
    if (img.dataset.zoom === '1') return
    img.dataset.zoom = '1'
    img.addEventListener('click', () => {
      window.open(img.getAttribute('src'), '_blank', 'noopener')
    })
  })
}

onMounted(async () => {
  md = createRenderer()
  render()
  await decorate()
})

watch(
  () => props.content,
  async () => {
    render()
    await nextTick()
    decorate()
  },
)
</script>
