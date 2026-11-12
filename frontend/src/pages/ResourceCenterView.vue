<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '@/shared/ui/PageHeader.vue'
import DataBaseView from '@/modules/knowledge/ui/KnowledgeCatalog.vue'
import { useUserStore } from '@/modules/identity'

const route = useRoute()
const userStore = useUserStore()
const knowledgeRef = ref(null)
</script>

<template>
  <div class="resource-center-view extension-page-root">
    <template v-if="route.name === 'ExtensionsComp'">
      <PageHeader title="知识库" :loading="knowledgeRef?.loading || false" :show-border="true" />
      <div class="resource-content">
        <DataBaseView v-if="userStore.isAdmin" ref="knowledgeRef" embedded />
        <a-empty v-else description="知识库管理仅管理员可用" />
      </div>
    </template>
    <router-view v-else :key="route.path" />
  </div>
</template>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.resource-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
