<template>
  <div class="dialog-backdrop" role="presentation">
    <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="password-title">
      <header class="dialog-head">
        <div>
          <h2 id="password-title">{{ forced ? '请先修改密码' : '修改密码' }}</h2>
          <p>{{ forced ? '管理员已重置你的密码，修改后才能继续使用。' : '修改后其他设备会被退出登录。' }}</p>
        </div>
        <button v-if="!forced" class="icon-btn" type="button" aria-label="关闭" @click="$emit('close')">
          <X :size="17" />
        </button>
      </header>

      <form class="dialog-form" @submit.prevent="submit">
        <label>
          <span>当前密码</span>
          <input
            v-model="form.current_password"
            type="password"
            autocomplete="current-password"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.current_password">{{ fieldErrors.current_password }}</em>
        </label>
        <label>
          <span>新密码</span>
          <input
            v-model="form.new_password"
            type="password"
            autocomplete="new-password"
            placeholder="至少 8 位，包含字母和数字"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.new_password">{{ fieldErrors.new_password }}</em>
        </label>
        <label>
          <span>确认新密码</span>
          <input
            v-model="form.confirm_password"
            type="password"
            autocomplete="new-password"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.confirm_password">{{ fieldErrors.confirm_password }}</em>
        </label>

        <p v-if="error" class="dialog-error" role="alert">{{ error }}</p>

        <div class="dialog-actions">
          <button v-if="!forced" class="btn-ghost" type="button" :disabled="loading" @click="$emit('close')">
            取消
          </button>
          <button class="btn-primary" type="submit" :disabled="loading">
            {{ loading ? '提交中…' : '确认修改' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { X } from '@lucide/vue'
import { changePassword } from '../api/auth.js'

defineProps({
  forced: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'changed'])

const form = reactive({
  current_password: '',
  new_password: '',
  confirm_password: '',
})
const loading = ref(false)
const error = ref('')
const fieldErrors = ref({})

async function submit() {
  if (loading.value) return
  error.value = ''
  fieldErrors.value = {}
  if (form.new_password !== form.confirm_password) {
    fieldErrors.value = { confirm_password: '两次输入的新密码不一致' }
    return
  }
  loading.value = true
  try {
    const response = await changePassword({ ...form })
    emit('changed', response.user)
  } catch (exc) {
    if (exc.fieldErrors) {
      fieldErrors.value = {
        new_password: exc.fieldErrors.new_password || exc.fieldErrors.password,
        confirm_password: exc.fieldErrors.confirm_password,
      }
    } else {
      error.value = exc.message || '修改密码失败'
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--overlay);
  backdrop-filter: blur(3px);
}

.dialog {
  width: min(440px, 100%);
  padding: 26px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg, 0 18px 60px rgba(15, 23, 42, 0.2));
}

.dialog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.dialog-head h2 {
  margin: 0;
  color: var(--ink);
  font-size: 16px;
  font-weight: 650;
}

.dialog-head p {
  margin: 4px 0 0;
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.dialog-form {
  display: grid;
  gap: 14px;
}

.dialog-form label {
  display: grid;
  gap: 6px;
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 550;
}

.dialog-form input {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-weight: 400;
  outline: none;
}

.dialog-form input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.dialog-form em {
  color: var(--danger, #dc2626);
  font-size: 11.5px;
  font-style: normal;
  font-weight: 400;
}

.dialog-error {
  margin: 0;
  color: var(--danger, #dc2626);
  font-size: 12.5px;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.btn-primary,
.btn-ghost {
  height: 36px;
  padding: 0 14px;
  border-radius: var(--r-sm);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.btn-primary {
  border: 0;
  background: var(--accent);
  color: #fff;
}

.btn-ghost {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink-2);
}

.btn-primary:disabled,
.btn-ghost:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
