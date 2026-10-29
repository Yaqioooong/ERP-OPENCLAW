<template>
  <div class="admin-backdrop" role="presentation">
    <section class="admin-panel" role="dialog" aria-modal="true" aria-labelledby="admin-title">
      <header class="admin-head">
        <div>
          <h2 id="admin-title">用户与权限</h2>
          <p>管理账号状态、角色与审批申请</p>
        </div>
        <button class="icon-btn" type="button" aria-label="关闭" @click="$emit('close')">
          <X :size="18" />
        </button>
      </header>

      <div class="admin-tabs">
        <button :class="{ active: tab === 'users' }" @click="tab = 'users'">用户</button>
        <button :class="{ active: tab === 'requests' }" @click="switchRequests">
          权限申请
          <span v-if="requests.length" class="badge">{{ requests.length }}</span>
        </button>
      </div>

      <div v-if="error" class="admin-error" role="alert">{{ error }}</div>

      <template v-if="tab === 'users'">
        <div class="admin-toolbar">
          <label class="search">
            <Search :size="15" />
            <input v-model="query" placeholder="搜索用户名 / 显示名 / ID" @keyup.enter="loadUsers" />
          </label>
          <button class="btn-ghost" type="button" :disabled="loading" @click="loadUsers">
            <RefreshCw :size="14" />
            刷新
          </button>
        </div>

        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>角色</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.user_id">
                <td>
                  <div class="user-cell">
                    <strong>{{ user.display_name || user.username }}</strong>
                    <span>{{ user.username }} · {{ user.user_id }}</span>
                  </div>
                </td>
                <td>
                  <div class="role-cell">
                    <button
                      v-for="role in roles"
                      :key="role.value"
                      type="button"
                      class="role-pill"
                      :class="{ on: user.roles.includes(role.value) }"
                      :disabled="user.user_id === currentUser?.user_id || busy === user.user_id"
                      :title="`切换 ${role.label}`"
                      @click="toggleRole(user, role.value)"
                    >
                      {{ role.label }}
                    </button>
                  </div>
                </td>
                <td>
                  <span class="status-pill" :class="user.is_active ? 'on' : 'off'">
                    {{ user.is_active ? '启用' : '禁用' }}
                  </span>
                </td>
                <td>
                  <div class="row-actions">
                    <button
                      class="mini-btn"
                      type="button"
                      :disabled="user.user_id === currentUser?.user_id || busy === user.user_id"
                      @click="toggleActive(user)"
                    >
                      {{ user.is_active ? '禁用' : '启用' }}
                    </button>
                    <button
                      class="mini-btn"
                      type="button"
                      :disabled="busy === user.user_id"
                      @click="resetPassword(user)"
                    >
                      <KeyRound :size="13" />
                      重置密码
                    </button>
                  </div>
                </td>
              </tr>
              <tr v-if="!users.length && !loading">
                <td colspan="4" class="empty">没有匹配的用户</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else>
        <div class="admin-toolbar">
          <span class="muted">待审批的 approver 角色申请</span>
          <button class="btn-ghost" type="button" :disabled="loading" @click="loadRequests">
            <RefreshCw :size="14" />
            刷新
          </button>
        </div>

        <div class="request-list">
          <article v-for="item in requests" :key="item.request_id" class="request-card">
            <div>
              <strong>{{ item.display_name || item.username }}</strong>
              <span>{{ item.username }} · {{ item.user_id }}</span>
              <p>{{ item.reason || '未填写申请理由' }}</p>
            </div>
            <div class="request-actions">
              <button class="mini-btn approve" type="button" :disabled="busy === item.request_id" @click="review(item, true)">
                <Check :size="13" />
                通过
              </button>
              <button class="mini-btn" type="button" :disabled="busy === item.request_id" @click="review(item, false)">
                <XCircle :size="13" />
                拒绝
              </button>
            </div>
          </article>
          <p v-if="!requests.length && !loading" class="empty">暂无待审批申请</p>
        </div>
      </template>

      <div v-if="temporary" class="temp-box">
        <strong>临时密码（只显示一次）</strong>
        <code>{{ temporary }}</code>
        <p>请线下告知用户；对方登录后必须先修改密码。</p>
        <button class="btn-ghost" type="button" @click="temporary = ''">已记录</button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import {
  Check,
  KeyRound,
  RefreshCw,
  Search,
  X,
  XCircle,
} from '@lucide/vue'
import {
  approveRoleRequest,
  listRoleRequests,
  listUsers,
  rejectRoleRequest,
  resetUserPassword,
  updateUser,
} from '../api/auth.js'

const props = defineProps({
  currentUser: { type: Object, default: null },
})
defineEmits(['close'])

const roles = [
  { value: 'requester', label: '申请人' },
  { value: 'approver', label: '审批人' },
  { value: 'admin', label: '管理员' },
]

const tab = ref('users')
const users = ref([])
const requests = ref([])
const query = ref('')
const loading = ref(false)
const busy = ref('')
const error = ref('')
const temporary = ref('')

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  error.value = ''
  try {
    const data = await listUsers({ query: query.value, limit: 100 })
    users.value = data.items || []
  } catch (exc) {
    error.value = exc.message || '加载用户失败'
  } finally {
    loading.value = false
  }
}

async function toggleActive(user) {
  busy.value = user.user_id
  error.value = ''
  try {
    await updateUser(user.user_id, { is_active: !user.is_active })
    await loadUsers()
  } catch (exc) {
    error.value = exc.message || '操作失败'
  } finally {
    busy.value = ''
  }
}

async function toggleRole(user, role) {
  if (user.user_id === props.currentUser?.user_id) return
  const current = new Set(user.roles || [])
  if (current.has(role)) current.delete(role)
  else current.add(role)
  if (!current.size) {
    error.value = '用户至少需要一个角色'
    return
  }
  busy.value = user.user_id
  error.value = ''
  try {
    await updateUser(user.user_id, { roles: [...current] })
    await loadUsers()
  } catch (exc) {
    error.value = exc.message || '修改角色失败'
  } finally {
    busy.value = ''
  }
}

async function resetPassword(user) {
  if (!window.confirm(`确认重置 ${user.display_name || user.username} 的密码？`)) return
  busy.value = user.user_id
  error.value = ''
  try {
    const data = await resetUserPassword(user.user_id)
    temporary.value = data.temporary_password
  } catch (exc) {
    error.value = exc.message || '重置密码失败'
  } finally {
    busy.value = ''
  }
}

async function switchRequests() {
  tab.value = 'requests'
  await loadRequests()
}

async function loadRequests() {
  loading.value = true
  error.value = ''
  try {
    const data = await listRoleRequests('pending')
    requests.value = data.requests || []
  } catch (exc) {
    error.value = exc.message || '加载申请失败'
  } finally {
    loading.value = false
  }
}

async function review(item, approve) {
  const comment = window.prompt(approve ? '审批备注（可选）' : '拒绝原因（可选）', '')
  if (comment === null) return
  busy.value = item.request_id
  error.value = ''
  try {
    if (approve) await approveRoleRequest(item.request_id, comment)
    else await rejectRoleRequest(item.request_id, comment)
    await loadRequests()
    await loadUsers()
  } catch (exc) {
    error.value = exc.message || '审批失败'
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.admin-backdrop {
  position: fixed;
  inset: 0;
  z-index: 75;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--overlay);
  backdrop-filter: blur(3px);
}

.admin-panel {
  display: flex;
  flex-direction: column;
  width: min(980px, 100%);
  max-height: min(86vh, 760px);
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg, 0 18px 60px rgba(15, 23, 42, 0.2));
}

.admin-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.admin-head h2 {
  margin: 0;
  color: var(--ink);
  font-size: 16px;
  font-weight: 650;
}

.admin-head p {
  margin: 4px 0 0;
  color: var(--ink-3);
  font-size: 12px;
}

.admin-tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  margin: 18px 0 14px;
  border-radius: var(--r-sm);
  background: var(--canvas);
}

.admin-tabs button {
  height: 32px;
  padding: 0 16px;
  border: 0;
  border-radius: calc(var(--r-sm) - 2px);
  background: transparent;
  color: var(--ink-3);
  font-size: 12.5px;
  font-weight: 550;
  cursor: pointer;
}

.admin-tabs button.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.badge {
  display: inline-grid;
  place-items: center;
  min-width: 16px;
  height: 16px;
  margin-left: 5px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--warn-soft);
  color: var(--warn);
  font-size: 10px;
}

.admin-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  max-width: 360px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  color: var(--ink-3);
}

.search input {
  flex: 1;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
  outline: none;
}

.admin-table-wrap {
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-sm);
}

.admin-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.admin-table th {
  position: sticky;
  top: 0;
  padding: 10px 12px;
  background: var(--canvas);
  color: var(--ink-3);
  font-size: 11.5px;
  font-weight: 600;
  text-align: left;
}

.admin-table td {
  padding: 10px 12px;
  border-top: 1px solid var(--line-soft);
  vertical-align: middle;
}

.user-cell {
  display: grid;
  gap: 2px;
}

.user-cell strong {
  color: var(--ink);
  font-weight: 600;
}

.user-cell span {
  color: var(--ink-3);
  font-size: 11px;
}

.role-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.role-pill {
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--ink-3);
  font-size: 10.5px;
  cursor: pointer;
}

.role-pill.on {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.status-pill {
  padding: 2px 7px;
  border-radius: var(--r-full);
  font-size: 10.5px;
}

.status-pill.on {
  background: var(--success-soft);
  color: var(--success);
}

.status-pill.off {
  background: var(--warn-soft);
  color: var(--warn);
}

.row-actions,
.request-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink-2);
  font-size: 11.5px;
  cursor: pointer;
}

.mini-btn.approve {
  border-color: var(--success);
  color: var(--success);
}

.mini-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.request-list {
  display: grid;
  gap: 8px;
  overflow: auto;
}

.request-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--r-sm);
}

.request-card strong {
  display: block;
  color: var(--ink);
  font-size: 12.5px;
}

.request-card span {
  color: var(--ink-3);
  font-size: 11px;
}

.request-card p {
  margin: 4px 0 0;
  color: var(--ink-2);
  font-size: 12px;
}

.admin-error {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  background: var(--danger-soft, #fee2e2);
  color: var(--danger, #dc2626);
  font-size: 12px;
}

.empty {
  padding: 18px;
  color: var(--ink-3);
  font-size: 12px;
  text-align: center;
}

.muted {
  color: var(--ink-3);
  font-size: 12px;
}

.temp-box {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--warn);
  border-radius: var(--r-sm);
  background: var(--warn-soft);
}

.temp-box strong {
  display: block;
  color: var(--warn);
  font-size: 12px;
}

.temp-box code {
  display: block;
  margin: 6px 0;
  color: var(--ink);
  font-size: 15px;
  letter-spacing: 0.04em;
}

.temp-box p {
  margin: 0 0 8px;
  color: var(--ink-2);
  font-size: 11.5px;
}
</style>
