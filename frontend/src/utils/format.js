/**
 * 通用格式化工具
 */

export function formatRelativeTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour

  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDiff = Math.round((startOfToday - startOfDate) / day)
  if (dayDiff === 1) return '昨天'
  if (dayDiff > 1 && dayDiff < 7) return `${dayDiff} 天前`

  const sameYear = date.getFullYear() === now.getFullYear()
  return sameYear
    ? `${date.getMonth() + 1} 月 ${date.getDate()} 日`
    : `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}

export function formatClock(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

export function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

export function formatCurrency(value, currency = 'CNY') {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (Number.isNaN(number)) return String(value)
  const symbol = currency === 'CNY' || !currency ? '¥' : `${currency} `
  return `${symbol}${number.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (Number.isNaN(number)) return String(value)
  return number.toLocaleString('zh-CN')
}

export function formatDuration(ms) {
  if (!ms || ms < 0) return ''
  if (ms < 1000) return '<1s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

export function shortId(value, head = 6, tail = 4) {
  if (!value) return ''
  const text = String(value)
  if (text.length <= head + tail + 1) return text
  return `${text.slice(0, head)}…${text.slice(-tail)}`
}

export function prettyJson(value) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return String(value)
  }
}
