<script setup>
import { useUserStore } from '@/modules/identity'
import PaintingWorkspace from '@/modules/paintings/ui/PaintingWorkspace.vue'

const userStore = useUserStore()
</script>

<template>
  <PaintingWorkspace
    v-if="userStore.isLoggedIn && userStore.uid"
    :key="userStore.uid"
    :owner="userStore.uid"
  />
</template>
