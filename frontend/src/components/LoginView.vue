<template>
  <div class="login-shell">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-brand">
        <span class="login-mark">ERP</span>
        <div>
          <h1 id="login-title">采购智能助手</h1>
          <p>登录后继续你的采购工作</p>
        </div>
      </div>

      <div class="login-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'login'"
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'register'"
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <form v-if="mode === 'login'" class="login-form" @submit.prevent="submitLogin">
        <label class="login-field">
          <span>用户名</span>
          <input
            v-model.trim="loginForm.username"
            name="username"
            autocomplete="username"
            placeholder="请输入登录名"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.username" class="field-error">{{ fieldErrors.username }}</em>
        </label>

        <label class="login-field">
          <span>密码</span>
          <input
            v-model="loginForm.password"
            name="password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            :disabled="loading"
            required
          />
        </label>

        <p v-if="error" class="login-error" role="alert">{{ error }}</p>

        <button class="login-submit" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
      </form>

      <form v-else class="login-form" @submit.prevent="submitRegister">
        <label class="login-field">
          <span>用户名</span>
          <input
            v-model.trim="registerForm.username"
            name="username"
            autocomplete="username"
            placeholder="3–32 位字母、数字或 . _ -"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.username" class="field-error">{{ fieldErrors.username }}</em>
        </label>

        <label class="login-field">
          <span>显示名称</span>
          <input
            v-model.trim="registerForm.display_name"
            name="display_name"
            autocomplete="name"
            placeholder="例如：张三"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.display_name" class="field-error">{{ fieldErrors.display_name }}</em>
        </label>

        <label class="login-field">
          <span>密码</span>
          <input
            v-model="registerForm.password"
            name="password"
            type="password"
            autocomplete="new-password"
            placeholder="至少 8 位，包含字母和数字"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.password" class="field-error">{{ fieldErrors.password }}</em>
        </label>

        <label class="login-field">
          <span>确认密码</span>
          <input
            v-model="registerForm.confirm_password"
            name="confirm_password"
            type="password"
            autocomplete="new-password"
            placeholder="再次输入密码"
            :disabled="loading"
            required
          />
          <em v-if="fieldErrors.confirm_password" class="field-error">
            {{ fieldErrors.confirm_password }}
          </em>
        </label>

        <p v-if="error" class="login-error" role="alert">{{ error }}</p>

        <button class="login-submit" type="submit" :disabled="loading">
          {{ loading ? '注册中…' : '注册并登录' }}
        </button>
        <p class="login-note">
          注册后默认是申请人（requester）；需要审批权限时，登录后在用户菜单中申请。
        </p>
      </form>

      <button
        v-if="devEnabled && mode === 'login'"
        class="login-dev"
        type="button"
        :disabled="loading"
        @click="submitDev"
      >
        开发登录（使用演示身份）
      </button>

      <p class="login-hint">身份由服务端会话校验；没有账号可直接注册。</p>
    </section>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { devLogin, login, register } from '../api/auth.js'

const emit = defineEmits(['logged-in'])
const devEnabled = import.meta.env.DEV

const mode = ref('login')
const loading = ref(false)
const error = ref('')
const fieldErrors = ref({})

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({
  username: '',
  display_name: '',
  password: '',
  confirm_password: '',
})

function switchMode(next) {
  if (loading.value) return
  mode.value = next
  error.value = ''
  fieldErrors.value = {}
}

function validateRegister() {
  const errors = {}
  const username = registerForm.username
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/.test(username)) {
    errors.username = '3–32 位，以字母或数字开头，仅允许字母、数字、点、下划线、短横线'
  }
  if (!registerForm.display_name || registerForm.display_name.length > 64) {
    errors.display_name = '显示名称不能为空且不能超过 64 个字符'
  }
  if (
    registerForm.password.length < 8 ||
    registerForm.password.length > 128 ||
    !/[A-Za-z]/.test(registerForm.password) ||
    !/\d/.test(registerForm.password)
  ) {
    errors.password = '密码需 8–128 位，并同时包含字母和数字'
  } else if (username && registerForm.password.toLowerCase().includes(username.toLowerCase())) {
    errors.password = '密码不能包含用户名'
  }
  if (registerForm.password !== registerForm.confirm_password) {
    errors.confirm_password = '两次输入的密码不一致'
  }
  fieldErrors.value = errors
  return Object.keys(errors).length === 0
}

async function submitLogin() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  fieldErrors.value = {}
  try {
    const response = await login(loginForm.username, loginForm.password)
    emit('logged-in', response.user)
  } catch (exc) {
    error.value =
      exc.status === 429
        ? '登录失败次数过多，请稍后再试'
        : exc.message || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}

async function submitRegister() {
  if (loading.value) return
  error.value = ''
  if (!validateRegister()) return
  loading.value = true
  try {
    const response = await register({ ...registerForm })
    emit('logged-in', response.user)
  } catch (exc) {
    if (exc.fieldErrors) {
      fieldErrors.value = exc.fieldErrors
    } else if (exc.status === 409) {
      fieldErrors.value = { username: '用户名已被占用' }
    } else {
      error.value = exc.message || '注册失败，请重试'
    }
  } finally {
    loading.value = false
  }
}

async function submitDev() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const response = await devLogin()
    emit('logged-in', response.user)
  } catch (exc) {
    error.value = exc.message || '开发登录不可用'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-shell {
  display: grid;
  place-items: center;
  min-height: 100dvh;
  padding: 24px;
  background:
    radial-gradient(circle at 20% 10%, var(--accent-soft), transparent 42%),
    var(--canvas);
}

.login-card {
  width: min(440px, 100%);
  padding: 32px;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lg, 0 18px 60px rgba(15, 23, 42, 0.12));
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 22px;
}

.login-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.login-brand h1 {
  margin: 0;
  color: var(--ink);
  font-size: 18px;
  font-weight: 650;
}

.login-brand p {
  margin: 3px 0 0;
  color: var(--ink-3);
  font-size: 12.5px;
}

.login-tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 20px;
  border-radius: var(--r-sm);
  background: var(--canvas);
}

.login-tabs button {
  flex: 1;
  height: 32px;
  border: 0;
  border-radius: calc(var(--r-sm) - 2px);
  background: transparent;
  color: var(--ink-3);
  font-size: 12.5px;
  font-weight: 550;
  cursor: pointer;
}

.login-tabs button.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.login-form {
  display: grid;
  gap: 14px;
}

.login-field {
  display: grid;
  gap: 6px;
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 550;
}

.login-field input {
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-weight: 400;
  outline: none;
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}

.login-field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.field-error {
  color: var(--danger, #dc2626);
  font-size: 11.5px;
  font-style: normal;
  font-weight: 400;
}

.login-error {
  margin: 0;
  color: var(--danger, #dc2626);
  font-size: 12.5px;
}

.login-submit {
  height: 40px;
  margin-top: 4px;
  border: 0;
  border-radius: var(--r-sm);
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.login-submit:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

.login-note {
  margin: 0;
  color: var(--ink-3);
  font-size: 11.5px;
  line-height: 1.6;
}

.login-dev {
  width: 100%;
  height: 36px;
  margin-top: 12px;
  border: 1px dashed var(--line);
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  cursor: pointer;
}

.login-hint {
  margin: 18px 0 0;
  color: var(--ink-3);
  font-size: 11.5px;
  line-height: 1.6;
  text-align: center;
}
</style>
