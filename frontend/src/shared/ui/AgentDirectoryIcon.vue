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
    aria-hidden="true"
  >
    <circle cx="7" cy="7" r="3.5" />
    <circle cx="7" cy="17" r="3.5" />
    <circle cx="17" cy="17" r="3.5" />
    <path d="M17 2.5c.7 2.4 2.1 3.8 4.5 4.5-2.4.7-3.8 2.1-4.5 4.5-.7-2.4-2.1-3.8-4.5-4.5 2.4-.7 3.8-2.1 4.5-4.5Z" fill="currentColor" stroke="none" />
  </svg>
</template>
