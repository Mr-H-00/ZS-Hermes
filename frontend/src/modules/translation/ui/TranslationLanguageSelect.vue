<script setup>
import { computed } from 'vue'

const props = defineProps({
  options: { type: Array, required: true },
  label: { type: String, required: true },
  disabled: { type: Boolean, default: false }
})
const value = defineModel({ type: String, default: '' })
const choices = computed(() => props.options.map(({ code, name, flag }) => ({
  value: code, label: name, flag
})))
</script>

<template>
  <a-select
    v-model:value="value"
    :options="choices"
    :aria-label="label"
    :disabled="disabled"
    :dropdown-match-select-width="true"
    class="translation-language-select"
  >
    <template #option="{ label: name, flag }">
      <span class="translation-flag" aria-hidden="true">{{ flag }}</span>
      <span>{{ name }}</span>
    </template>
    <template #optionLabel="{ label: name, value: code }">
      <span class="translation-flag" aria-hidden="true">{{ options.find((item) => item.code === code)?.flag }}</span>
      <span>{{ name }}</span>
    </template>
  </a-select>
</template>

<style lang="less">
@font-face {
  font-family: 'Twemoji Country Flags';
  src: url('../assets/cherry-studio/TwemojiCountryFlags.woff2') format('woff2');
  font-display: swap;
  unicode-range: U+1F1E6-1F1FF;
}

.translation-flag {
  display: inline-block;
  margin-inline-end: 7px;
  font-family: 'Twemoji Country Flags', sans-serif;
  font-size: 13px;
  line-height: 1;
}
</style>
