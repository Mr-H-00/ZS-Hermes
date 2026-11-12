<script setup>
defineProps({
  size: { type: [Number, String], default: 24 }
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path
      d="M8.5 12.5V9.8a5.8 5.8 0 0 1 11.5-1C21.4 10 22 11.5 22 13.2c0 3.2-2.3 5.8-5.4 5.8H7.5A5.5 5.5 0 0 1 2 13.5C2 10.7 4 8.4 6.7 8"
    />
  </svg>
</template>
