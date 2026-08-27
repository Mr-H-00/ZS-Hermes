<script setup>
import { computed, nextTick, reactive, watch } from 'vue'

import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import ProjectSelectionSection from '@/components/ProjectSelectionSection.vue'
import ToolApprovalModeSelector from '@/components/ToolApprovalModeSelector.vue'
import { AUTO_PROJECT_ID } from '@/utils/projectSelection'
import {
  buildCronExpression,
  dayOptions,
  monthOptions,
  parseCronExpression,
  scheduleFrequencies,
  weekdayOptions
} from '@/utils/scheduleFrequency'

const props = defineProps({
  job: { type: Object, default: null },
  agents: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
  saveState: { type: String, default: 'idle' },
  error: { type: String, default: '' }
})
const emit = defineEmits(['change'])

const defaultSchedule = {
  frequency: 'daily',
  time: '09:00',
  weekdays: [1],
  dayOfMonth: 1,
  month: 1,
  cronExpression: '0 9 * * *'
}

const agentOptions = computed(() =>
  props.agents.map((agent) => ({
    value: agent.slug || agent.id,
    label: agent.name || agent.slug || agent.id
  }))
)

function initialForm(job) {
  return {
    name: job?.name || '',
    prompt: job?.prompt || '',
    project_id: job?.project_id || '',
    agent_slug: job?.agent_slug || agentOptions.value[0]?.value || '',
    ...(job ? parseCronExpression(job.cron_expression) : defaultSchedule),
    model_spec: job?.model_spec || '',
    timezone: job?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
    tool_approval_mode: job?.tool_approval_mode || 'default'
  }
}

const form = reactive(initialForm(props.job))
let hydrating = false

const daysInSelectedMonth = computed(() => {
  if (form.frequency !== 'yearly') return 31
  if (Number(form.month) === 2) return 28
  return [4, 6, 9, 11].includes(Number(form.month)) ? 30 : 31
})
const availableDayOptions = computed(() => dayOptions.slice(0, daysInSelectedMonth.value))

const saveLabel = computed(() => {
  if (!props.job && props.saveState === 'invalid') return '填写完整后自动创建'
  if (props.saveState === 'dirty') return '等待自动保存'
  if (props.saveState === 'saving' || props.saving) return '正在保存'
  if (props.saveState === 'saved') return '已自动保存'
  if (props.saveState === 'error') return '保存失败'
  if (props.saveState === 'invalid') return '补全必填项后自动保存'
  return props.job ? '修改会自动保存' : '填写完整后自动创建'
})

function validationMessage() {
  if (!form.name.trim()) return '请输入任务名称'
  if (!form.prompt.trim()) return '请输入任务指令'
  if (!form.project_id || form.project_id === AUTO_PROJECT_ID) return '请选择一个 Project'
  if (!form.agent_slug) return '请选择执行智能体'
  if (form.frequency === 'weekly' && !form.weekdays.length) return '请至少选择一个执行日'
  if (form.frequency !== 'custom' && !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(form.time)) {
    return '请选择执行时间'
  }
  if (form.frequency === 'custom' && form.cronExpression.trim().split(/\s+/).length !== 5) {
    return '请输入有效的五段 Cron 表达式'
  }
  return ''
}

function changePayload() {
  const validationError = validationMessage()
  if (validationError) return { error: validationError, payload: null }
  return {
    error: '',
    payload: {
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      project_id: form.project_id,
      agent_slug: form.agent_slug,
      cron_expression: buildCronExpression(form),
      model_spec: form.model_spec,
      timezone: form.timezone,
      tool_approval_mode: form.tool_approval_mode
    }
  }
}

function toggleWeekday(day) {
  const selected = new Set(form.weekdays)
  if (selected.has(day)) selected.delete(day)
  else selected.add(day)
  form.weekdays = [...selected].sort((left, right) => left - right)
}

watch(agentOptions, (agents) => {
  if (!form.agent_slug && agents.length) form.agent_slug = agents[0].value
})

watch(daysInSelectedMonth, (days) => {
  if (form.dayOfMonth > days) form.dayOfMonth = days
})

watch(
  () => props.job?.id,
  async (jobId, previousJobId) => {
    if (jobId === previousJobId) return
    hydrating = true
    Object.assign(form, initialForm(props.job))
    await nextTick()
    hydrating = false
  }
)

watch(
  form,
  () => {
    if (!hydrating) emit('change', changePayload())
  },
  { deep: true }
)
</script>

<template>
  <section class="inline-editor" aria-label="任务配置">
    <div class="title-line">
      <input
        v-model="form.name"
        class="name-input"
        maxlength="255"
        aria-label="任务名称"
        placeholder="未命名任务"
      />
      <span class="save-state" :class="saveState">{{ saveLabel }}</span>
    </div>

    <label class="prompt-field">
      <span class="sr-only">任务指令</span>
      <textarea
        v-model="form.prompt"
        maxlength="32000"
        rows="4"
        placeholder="描述每次触发时智能体需要完成的工作"
      />
    </label>

    <p v-if="error" class="save-error" role="alert">{{ error }}</p>

    <section class="settings-section" aria-labelledby="context-settings-heading">
      <h3 id="context-settings-heading">详情</h3>
      <label class="setting-row">
        <span>运行于</span>
        <select v-model="form.agent_slug" aria-label="执行智能体">
          <option value="" disabled>选择智能体</option>
          <option v-for="agent in agentOptions" :key="agent.value" :value="agent.value">
            {{ agent.label }}
          </option>
        </select>
      </label>
      <div class="setting-row">
        <span>Project</span>
        <ProjectSelectionSection
          v-model="form.project_id"
          :disabled="saving"
          :allow-auto="false"
          eager-load
          aria-label="任务 Project"
        />
      </div>
      <div class="setting-row">
        <span>模型</span>
        <ModelSelectorComponent
          :model_spec="form.model_spec"
          clearable
          size="nano"
          display-name="mini"
          placeholder="跟随智能体模型"
          @select-model="(spec) => (form.model_spec = spec)"
        />
      </div>
      <div class="setting-row">
        <span>工具审批</span>
        <ToolApprovalModeSelector v-model="form.tool_approval_mode" />
      </div>
      <p v-if="form.tool_approval_mode === 'always_trust'" class="trust-warning">
        完全信任允许敏感工具无人值守执行，请只绑定可信的智能体和 Project。
      </p>
    </section>

    <section class="settings-section" aria-labelledby="schedule-settings-heading">
      <h3 id="schedule-settings-heading">频率</h3>
      <label class="setting-row">
        <span>重复</span>
        <select v-model="form.frequency" aria-label="重复频率">
          <option
            v-for="frequency in scheduleFrequencies"
            :key="frequency.value"
            :value="frequency.value"
          >
            {{ frequency.label }}
          </option>
        </select>
      </label>
      <div v-if="form.frequency === 'weekly'" class="setting-row weekday-row">
        <span>执行日</span>
        <div class="weekday-options" aria-label="每周执行日">
          <button
            v-for="weekday in weekdayOptions"
            :key="weekday.value"
            type="button"
            :class="{ selected: form.weekdays.includes(weekday.value) }"
            :aria-pressed="form.weekdays.includes(weekday.value)"
            @click="toggleWeekday(weekday.value)"
          >
            {{ weekday.label }}
          </button>
        </div>
      </div>
      <label v-if="form.frequency === 'yearly'" class="setting-row">
        <span>月份</span>
        <select v-model="form.month" aria-label="执行月份">
          <option v-for="month in monthOptions" :key="month.value" :value="month.value">
            {{ month.label }}
          </option>
        </select>
      </label>
      <label v-if="['monthly', 'yearly'].includes(form.frequency)" class="setting-row">
        <span>日期</span>
        <select v-model="form.dayOfMonth" aria-label="执行日期">
          <option v-for="day in availableDayOptions" :key="day.value" :value="day.value">
            {{ day.label }}
          </option>
        </select>
      </label>
      <label class="setting-row">
        <span>{{ form.frequency === 'custom' ? 'Cron' : '时间' }}</span>
        <input
          v-if="form.frequency === 'custom'"
          v-model="form.cronExpression"
          aria-label="Cron 表达式"
          placeholder="0 9 * * *"
        />
        <input v-else v-model="form.time" type="time" aria-label="执行时间" />
      </label>
      <div class="setting-row static-row">
        <span>时区</span>
        <output>{{ form.timezone }}</output>
      </div>
    </section>
  </section>
</template>

<style lang="less" scoped>
.inline-editor {
  min-width: 0;
  color: var(--gray-1000);
}

.title-line {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 26px 28px 10px;
}

.name-input {
  min-width: 0;
  padding: 0;
  border: 0;
  outline: 0;
  flex: 1;
  background: transparent;
  color: var(--gray-1000);
  font: inherit;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 28px;

  &:focus {
    box-shadow: 0 1px 0 var(--main-color);
  }
}

.save-state {
  color: var(--gray-400);
  font-size: 12px;
  white-space: nowrap;

  &.saving,
  &.dirty {
    color: var(--gray-600);
  }

  &.saved {
    color: var(--color-success-700);
  }

  &.error,
  &.invalid {
    color: var(--color-error-700);
  }
}

.prompt-field {
  display: block;
  padding: 0 28px 24px;

  textarea {
    width: 100%;
    min-height: 108px;
    padding: 12px 0;
    border: 0;
    border-top: 1px solid var(--gray-100);
    border-bottom: 1px solid var(--gray-100);
    outline: 0;
    background: transparent;
    color: var(--gray-800);
    font: inherit;
    font-size: 14px;
    line-height: 1.7;
    resize: vertical;

    &:focus {
      border-color: var(--gray-300);
    }
  }
}

.save-error,
.trust-warning {
  margin: 0 28px 12px;
  font-size: 12px;
  line-height: 18px;
}

.save-error {
  color: var(--color-error-700);
}

.trust-warning {
  padding: 6px 0 6px 28%;
  color: var(--color-warning-800);
}

.settings-section {
  padding: 0 28px 22px;

  h3 {
    margin: 0;
    padding: 12px 0 8px;
    border-bottom: 1px solid var(--gray-150);
    color: var(--gray-500);
    font-size: 12px;
    font-weight: 500;
  }
}

.setting-row {
  display: grid;
  min-height: 50px;
  margin: 0;
  border-bottom: 1px solid var(--gray-100);
  grid-template-columns: minmax(110px, 28%) minmax(0, 1fr);
  align-items: center;

  > span {
    color: var(--gray-600);
    font-size: 13px;
  }

  input,
  select {
    width: 100%;
    height: 34px;
    padding: 0 9px;
    border: 1px solid transparent;
    border-radius: 5px;
    outline: none;
    background: transparent;
    color: var(--gray-900);
    font: inherit;
    font-size: 13px;
    text-align: right;

    &:hover {
      background: var(--gray-25);
    }

    &:focus {
      border-color: var(--gray-200);
      background: var(--gray-0);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--main-color) 10%, transparent);
    }
  }

  select {
    cursor: pointer;
  }
}

.static-row output {
  color: var(--gray-800);
  font-size: 13px;
  text-align: right;
}

.weekday-options {
  display: flex;
  justify-content: flex-end;
  gap: 4px;

  button {
    width: 30px;
    height: 30px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 5px;
    background: transparent;
    color: var(--gray-500);
    cursor: pointer;

    &:hover {
      background: var(--gray-50);
      color: var(--gray-800);
    }

    &.selected {
      border-color: var(--gray-200);
      background: var(--gray-100);
      color: var(--gray-900);
    }
  }
}

.inline-editor :deep(.project-selection) {
  display: block;
}

.inline-editor :deep(.project-trigger),
.inline-editor :deep(.input-action-btn),
.inline-editor :deep(.model-select--nano) {
  width: 100%;
  max-width: none;
  height: 34px;
  padding: 0 9px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--gray-900);
  justify-content: flex-end;
}

.inline-editor :deep(.project-trigger:hover:not(:disabled)),
.inline-editor :deep(.input-action-btn:hover),
.inline-editor :deep(.model-select--nano:hover) {
  border-color: transparent;
  background: var(--gray-25);
}

.inline-editor :deep(.config-dropdown-trigger) {
  justify-content: flex-end;
}

.inline-editor :deep(.config-dropdown-chevron) {
  margin-left: 6px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 720px) {
  .title-line,
  .prompt-field,
  .settings-section {
    padding-inline: 18px;
  }

  .save-error,
  .trust-warning {
    margin-inline: 18px;
  }

  .trust-warning {
    padding-left: 0;
  }

  .title-line {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .name-input {
    width: 100%;
  }

  .setting-row {
    grid-template-columns: 88px minmax(0, 1fr);
  }

  .weekday-options {
    flex-wrap: wrap;
  }
}
</style>
