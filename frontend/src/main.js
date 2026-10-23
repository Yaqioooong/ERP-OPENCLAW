/**
 * Vue 应用入口文件
 *
 * 创建并挂载 Vue 应用
 */

import { createApp } from 'vue'
import App from './App.vue'
import './styles/main.css'
import './styles/markdown.css'
import './styles/hitl.css'

// 支持通过 ?theme=dark|light 预览主题（也方便分享带主题的链接）
const requestedTheme = new URLSearchParams(window.location.search).get('theme')
if (import.meta.env.DEV && (requestedTheme === 'dark' || requestedTheme === 'light')) {
  localStorage.setItem('erp-theme', requestedTheme)
  document.documentElement.dataset.theme = requestedTheme
}

// 创建 Vue 应用实例
const app = createApp(App)

// 挂载到 #app 元素
app.mount('#app')
