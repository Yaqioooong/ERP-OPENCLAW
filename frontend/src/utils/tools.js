/**
 * 工具与代理的展示元数据
 */
import {
  BarChart3,
  Bot,
  ClipboardList,
  Database,
  FileText,
  GitBranch,
  Globe,
  Handshake,
  ListChecks,
  PackageSearch,
  PenLine,
  Search,
  ShoppingCart,
  Terminal,
  Wrench,
} from '@lucide/vue'

const EXACT = {
  task: { label: '委派子代理', icon: GitBranch },
  write_todos: { label: '更新任务清单', icon: ListChecks },
  read_file: { label: '读取文件', icon: FileText },
  write_file: { label: '写入文件', icon: PenLine },
  edit_file: { label: '编辑文件', icon: PenLine },
  execute_python: { label: '执行 Python', icon: Terminal },
  web_search: { label: '网络搜索', icon: Search },
  start_procurement_task: { label: '启动采购任务', icon: ShoppingCart },
  get_procurement_task: { label: '查询采购任务', icon: ClipboardList },
  resume_procurement_task: { label: '恢复采购任务', icon: ClipboardList },
  order_create: { label: '创建采购订单', icon: ShoppingCart },
  order_update: { label: '修改采购订单', icon: ShoppingCart },
  order_query: { label: '查询采购订单', icon: ClipboardList },
  product_query: { label: '查询物料', icon: PackageSearch },
  inventory_query: { label: '查询库存', icon: Database },
  supplier_query: { label: '查询供应商', icon: Handshake },
}

const RULES = [
  { test: /chart|图表|viz|visual/i, label: '生成图表', icon: BarChart3 },
  { test: /search|搜索|query_web/i, label: '网络搜索', icon: Search },
  { test: /inventory|库存/i, label: '查询库存', icon: Database },
  { test: /supplier|供应商/i, label: '查询供应商', icon: Handshake },
  { test: /order|订单/i, label: '订单操作', icon: ShoppingCart },
  { test: /product|part|物料|零件/i, label: '查询物料', icon: PackageSearch },
  { test: /read|读取/i, label: '读取文件', icon: FileText },
  { test: /write|edit|写入|编辑/i, label: '写入文件', icon: PenLine },
  { test: /execute|python|run|执行/i, label: '执行代码', icon: Terminal },
  { test: /search|web|搜索/i, label: '网络搜索', icon: Search },
  { test: /task|委派|代理/i, label: '委派子代理', icon: GitBranch },
  { test: /todo|清单/i, label: '更新任务清单', icon: ListChecks },
]

const SOURCES = {
  main: '采购助手',
  'procurement-analyst': '采购分析',
  'procurement-order': '订单操作',
  'chart-agent': '图表代理',
  researcher: '研究代理',
  'model-agent': '模型代理',
  general: '通用代理',
}

const SOURCE_ICONS = {
  'procurement-analyst': BarChart3,
  'procurement-order': ShoppingCart,
  'chart-agent': BarChart3,
  researcher: Globe,
  'model-agent': Bot,
  general: Bot,
}

export function toolMeta(name) {
  if (!name) return { label: '工具调用', icon: Wrench }
  if (EXACT[name]) return EXACT[name]
  for (const rule of RULES) {
    if (rule.test.test(name)) return { label: rule.label, icon: rule.icon }
  }
  return { label: name, icon: Wrench }
}

export function toolLabel(name) {
  return toolMeta(name).label
}

export function sourceName(source) {
  if (!source) return SOURCES.main
  return SOURCES[source] || source
}

export function sourceIcon(source) {
  return SOURCE_ICONS[source] || Bot
}

export function isSubAgent(source) {
  return !!source && source !== 'main'
}

export function cleanToolText(text) {
  if (!text) return ''
  return String(text)
    .replace(/,?\s*'id':\s*'[^']*'/g, '')
    .replace(/,?\s*"id":\s*"[^"]*"/g, '')
}
