<template>
  <section class="wps-cloud-drive">
    <header class="cloud-header">
      <div class="provider-heading"><Cloud :size="20" /><h2>WPS</h2></div>
      <span v-if="connection?.connected" class="connection-label"><CheckCircle2 :size="14" /> 已连接 {{ connection.user?.name }}</span>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" @click="openSettingsModal?.('cloudDrive')"><template #icon><Settings :size="16" /></template>云盘配置</a-button>
        <a-button class="lucide-icon-btn" :loading="loadingConnection" @click="refresh"><template #icon><RefreshCw :size="16" /></template>刷新</a-button>
      </div>
    </header>

    <div v-if="loadingConnection && !connection" class="cloud-state"><a-spin /><p>正在读取 WPS 连接</p></div>
    <div v-else-if="connectionError" class="cloud-state" role="alert">
      <CircleAlert :size="32" /><h3>无法读取 WPS 连接</h3><p>请检查网络后重新刷新。</p><a-button @click="refresh">重新读取</a-button>
    </div>
    <div v-else-if="!connection?.connected" class="cloud-state">
      <Cloud :size="40" /><h3>连接 WPS 365 云文档</h3><p>{{ connection?.message }}</p>
      <p v-if="originMismatch">请通过已登记的域名访问并登录后连接：<a :href="authorizationOrigin + '/workspace?tab=cloud'">{{ authorizationOrigin }}</a></p>
      <p v-if="connection?.configured && !authorizationOrigin">请在云盘配置中启动开发联调，登记固定回调并打开联调站点登录。</p>
      <a-button v-if="connection?.enabled && connection?.configured && authorizationOrigin" type="primary" :loading="connecting" :disabled="originMismatch" @click="connect">连接 WPS</a-button>
      <a-button v-else @click="openSettingsModal?.('cloudDrive')">查看云盘配置</a-button>
    </div>
    <div v-else class="cloud-browser">
      <aside class="drive-sidebar">
        <h3>文档库</h3>
        <button type="button" class="drive-row" :class="{ selected: !selectedDrive && !searching }" @click="showDrives"><LibraryBig :size="18" />我的文档库</button>
        <button v-for="drive in drives" :key="drive.id" type="button" class="drive-row drive-child" :class="{ selected: selectedDrive?.id === drive.id && !searching }" :title="drive.name" @click="selectDrive(drive)">
          <Folder :size="17" /><span>{{ drive.name }}</span>
        </button>
        <a-button v-if="drivesNextPage" type="link" :loading="loadingDrives" @click="loadDrives(true)">更多文档库</a-button>
        <p v-if="drivesError" class="sidebar-error">文档库读取失败，请刷新重试。</p>
      </aside>
      <main class="cloud-files">
        <a-input-search v-model:value="searchText" placeholder="搜索 WPS 文件名" aria-label="搜索 WPS 文件名" allow-clear class="cloud-search" :loading="loadingFiles" @search="search" />
        <nav class="cloud-breadcrumb" aria-label="WPS 文件路径">
          <button type="button" @click="showDrives">我的文档库</button>
          <template v-if="searching"><ChevronRight :size="14" /><span>搜索“{{ searchKeyword }}”</span></template>
          <template v-else-if="selectedDrive">
            <ChevronRight :size="14" /><button type="button" @click="goToFolder(-1)">{{ selectedDrive.name }}</button>
            <template v-for="(folder, index) in folders" :key="folder.id"><ChevronRight :size="14" /><button type="button" @click="goToFolder(index)">{{ folder.name }}</button></template>
          </template>
        </nav>
        <a-alert v-if="filesError" type="error" show-icon :message="filesError" class="files-error" />
        <a-spin :spinning="loadingFiles || loadingDrives">
          <template v-if="!selectedDrive && !searching">
            <div v-if="drives.length" class="drive-grid">
              <button v-for="drive in drives" :key="drive.id" class="drive-card" type="button" @click="selectDrive(drive)"><LibraryBig :size="24" /><span>{{ drive.name }}<small>{{ drive.description || 'WPS 云文档' }}</small></span><ChevronRight :size="17" /></button>
            </div>
            <a-empty v-else-if="!loadingDrives && !drivesError" description="暂无可访问的 WPS 文档库" />
          </template>
          <template v-else>
            <div v-if="files.length" class="files-table-scroll">
              <table class="cloud-files-table" aria-label="WPS 云文档文件">
                <thead><tr><th scope="col">文件名</th><th v-if="searching" scope="col">文档库</th><th scope="col">大小</th><th scope="col">更新时间</th><th scope="col">操作</th></tr></thead>
                <tbody><tr v-for="file in files" :key="`${file.drive_id}:${file.id}`">
                  <td><button type="button" class="file-name" :disabled="file.type !== 'folder' && !safeCloudUrl(file.link_url)" :title="file.name" @click="openFile(file)"><FileTypeIcon :name="file.name" :is-dir="file.type === 'folder'" :size="20" /><span>{{ file.name }}</span></button></td>
                  <td v-if="searching">{{ file.drive?.name || '—' }}</td>
                  <td>{{ file.type === 'folder' ? '—' : formatSize(file.size) }}</td>
                  <td>{{ formatModifiedAt(file.mtime) }}</td>
                  <td><div v-if="file.type !== 'folder'" class="file-actions">
                    <a v-if="safeCloudUrl(file.link_url)" :href="safeCloudUrl(file.link_url)" target="_blank" rel="noopener noreferrer">打开 <ArrowUpRight :size="13" /></a>
                    <a-button type="link" size="small" class="lucide-icon-btn" :loading="downloading === file.id" :disabled="file.permission?.download === false" @click="download(file)"><template #icon><Download :size="14" /></template>下载</a-button>
                  </div></td>
                </tr></tbody>
              </table>
            </div>
            <a-empty v-else-if="!loadingFiles && !filesError" :description="searching ? '没有找到匹配的 WPS 文件' : '此文件夹为空'" />
            <div v-if="filesNextPage" class="load-more"><a-button :loading="loadingFiles" @click="loadFiles(true)">加载更多</a-button></div>
          </template>
        </a-spin>
      </main>
    </div>
  </section>
</template>

<script setup>
import { computed, inject, onActivated, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ArrowUpRight, CheckCircle2, ChevronRight, CircleAlert, Cloud, Download, Folder, LibraryBig, RefreshCw, Settings } from '@lucide/vue'
import { wpsCloudApi } from '@/apis/cloud_drive_api'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import { safeCloudUrl, normalizeWPSFiles, formatModifiedAt, wpsAuthorizationOrigin } from '@/modules/workspace'
import { formatFileSize as formatSize } from '@/shared/lib/file_utils'

const { openSettingsModal } = inject('settingsModal', {})
const connection = ref(null)
const authorizationOrigin = computed(() => wpsAuthorizationOrigin(connection.value, window.location.origin))
const originMismatch = computed(() => Boolean(authorizationOrigin.value && authorizationOrigin.value !== window.location.origin))
const connectionError = ref(false)
const loadingConnection = ref(false)
const connecting = ref(false)
const drives = ref([])
const drivesNextPage = ref('')
const drivesError = ref(false)
const loadingDrives = ref(false)
const selectedDrive = ref(null)
const folders = ref([])
const files = ref([])
const filesNextPage = ref('')
const filesError = ref('')
const loadingFiles = ref(false)
const searchText = ref('')
const searchKeyword = ref('')
const searching = ref(false)
const downloading = ref('')
let fileRequest = 0
let driveRequest = 0

async function refresh() {
  if (loadingConnection.value) return
  loadingConnection.value = true
  connectionError.value = false
  // 配置或连接变化后清除旧文件，失败时不继续展示旧账号的数据。
  fileRequest++
  driveRequest++
  drives.value = []
  drivesNextPage.value = ''
  drivesError.value = false
  files.value = []
  filesNextPage.value = ''
  filesError.value = ''
  loadingFiles.value = false
  loadingDrives.value = false
  selectedDrive.value = null
  folders.value = []
  searching.value = false
  try {
    connection.value = await wpsCloudApi.checkConnection()
    if (connection.value.connected) await loadDrives()
  } catch {
    connectionError.value = true
    connection.value = null
  } finally {
    loadingConnection.value = false
  }
}

async function loadDrives(append = false) {
  const request = ++driveRequest
  loadingDrives.value = true
  drivesError.value = false
  try {
    const data = await wpsCloudApi.getDrives(append ? drivesNextPage.value : undefined)
    if (request !== driveRequest) return
    drives.value = append ? [...drives.value, ...(data.items || [])] : data.items || []
    drivesNextPage.value = data.next_page_token || ''
  } catch {
    if (request === driveRequest) drivesError.value = true
  } finally {
    if (request === driveRequest) loadingDrives.value = false
  }
}

function showDrives() {
  fileRequest++
  selectedDrive.value = null
  folders.value = []
  searching.value = false
  files.value = []
  filesError.value = ''
  filesNextPage.value = ''
  loadingFiles.value = false
}

async function selectDrive(drive) {
  selectedDrive.value = drive
  folders.value = []
  searching.value = false
  searchText.value = ''
  await loadFiles()
}

async function goToFolder(index) {
  folders.value = folders.value.slice(0, index + 1)
  searching.value = false
  searchText.value = ''
  await loadFiles()
}

async function search() {
  const keyword = searchText.value.trim()
  if (!keyword) { showDrives(); return }
  searchKeyword.value = keyword
  searching.value = true
  await loadFiles()
}

async function loadFiles(append = false) {
  const request = ++fileRequest
  loadingFiles.value = true
  filesError.value = ''
  if (!append) { files.value = []; filesNextPage.value = '' }
  try {
    const data = searching.value
      ? await wpsCloudApi.searchFiles(searchKeyword.value, append ? filesNextPage.value : undefined)
      : await wpsCloudApi.getFiles(selectedDrive.value.id, folders.value.at(-1)?.id || '0', append ? filesNextPage.value : undefined)
    if (request !== fileRequest) return
    const entries = normalizeWPSFiles(data.items || [])
    files.value = append ? [...files.value, ...entries] : entries
    filesNextPage.value = data.next_page_token || ''
  } catch (error) {
    if (request === fileRequest) {
      filesError.value = error.status === 409 ? '连接已失效，请刷新并重新连接 WPS。' : '读取 WPS 文件失败，请检查应用权限后刷新重试。'
    }
  } finally {
    if (request === fileRequest) loadingFiles.value = false
  }
}

async function openFile(file) {
  if (file.type !== 'folder') {
    const url = safeCloudUrl(file.link_url)
    if (url) window.open(url, '_blank', 'noopener,noreferrer')
    return
  }
  if (searching.value) {
    selectedDrive.value = file.drive || drives.value.find((drive) => drive.id === file.drive_id) || { id: file.drive_id, name: 'WPS 文档库' }
    folders.value = [{ id: file.id, name: file.name }]
  } else folders.value.push({ id: file.id, name: file.name })
  searching.value = false
  await loadFiles()
}

async function download(file) {
  downloading.value = file.id
  try {
    const data = await wpsCloudApi.getDownload(file.drive_id || selectedDrive.value.id, file.id)
    const url = safeCloudUrl(data.url)
    if (!url) throw new Error('invalid-download')
    // 使用 WPS 返回的临时下载地址，不向该地址传递 Yuxi 或 WPS 令牌。
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    link.rel = 'noopener noreferrer'
    link.referrerPolicy = 'no-referrer'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } catch {
    message.error('无法下载此文件，请检查 WPS 下载权限或重新连接。')
  } finally {
    downloading.value = ''
  }
}

async function connect() {
  if (!authorizationOrigin.value || originMismatch.value) {
    message.error('请通过已登记的回调域名访问并登录后连接 WPS。')
    return
  }
  connecting.value = true
  try {
    const result = await wpsCloudApi.authorize()
    window.location.assign(result.authorization_url)
  } catch {
    message.error('无法开始 WPS 授权，请检查云盘配置。')
    connecting.value = false
  }
}

onMounted(refresh)
onActivated(refresh)
</script>

<style scoped lang="less">
.wps-cloud-drive { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: auto; background: var(--gray-0); }
.cloud-header { display: flex; align-items: center; gap: 16px; padding: 16px 24px; border-bottom: 1px solid var(--gray-100); }
.provider-heading { display: flex; align-items: center; gap: 8px; color: var(--main-color); }
.provider-heading h2 { margin: 0; font-size: 16px; font-weight: 600; }
.connection-label { display: flex; align-items: center; gap: 5px; color: var(--color-success-700); font-size: 12px; overflow-wrap: anywhere; }
.header-actions { display: flex; gap: 8px; margin-left: auto; }
.cloud-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; min-height: 300px; padding: 32px; color: var(--gray-500); text-align: center; }
.cloud-state h3 { color: var(--gray-900); font-size: 18px; margin: 0; }
.cloud-state p { margin: 0; font-size: 14px; line-height: 1.6; }
.cloud-browser { flex: 1; display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 0; }
.drive-sidebar { padding: 20px 12px; border-right: 1px solid var(--gray-100); }
.drive-sidebar h3 { margin: 0 12px 12px; color: var(--gray-900); font-size: 15px; font-weight: 600; }
.drive-row { display: flex; align-items: center; gap: 10px; width: 100%; border: 0; border-radius: 6px; padding: 11px 12px; background: transparent; color: var(--gray-700); font-size: 14px; text-align: left; cursor: pointer; }
.drive-row svg { flex: none; }
.drive-row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drive-row:hover { background: var(--gray-50); }
.drive-row.selected { background: var(--main-10); color: var(--main-color); }
.drive-child { padding-left: 28px; }
.sidebar-error { padding: 0 12px; color: var(--color-error-700); font-size: 12px; }
.cloud-files { min-width: 0; padding: 24px; }
.cloud-search { max-width: 680px; }
.cloud-breadcrumb { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 20px 0; color: var(--gray-500); font-size: 13px; overflow-wrap: anywhere; }
.cloud-breadcrumb button { max-width: 100%; border: 0; padding: 0; background: transparent; color: var(--gray-700); text-align: left; cursor: pointer; overflow-wrap: anywhere; }
.cloud-breadcrumb button:hover { color: var(--main-color); }
.files-error { margin-bottom: 16px; }
.drive-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
.drive-card { display: flex; align-items: center; gap: 14px; padding: 20px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); color: var(--main-color); text-align: left; cursor: pointer; }
.drive-card > span { flex: 1; min-width: 0; color: var(--gray-900); font-size: 15px; overflow-wrap: anywhere; }
.drive-card small { display: block; margin-top: 4px; color: var(--gray-500); font-size: 12px; }
.drive-card:hover { background: var(--gray-25); border-color: var(--gray-200); }
.files-table-scroll { overflow-x: auto; }
.cloud-files-table { width: 100%; min-width: 580px; border-collapse: collapse; text-align: left; font-size: 13px; color: var(--gray-600); }
.cloud-files-table th { font-weight: 500; white-space: nowrap; }
.cloud-files-table th, .cloud-files-table td { padding: 12px 10px; border-bottom: 1px solid var(--gray-100); }
.cloud-files-table td:not(:first-child) { white-space: nowrap; }
.file-name { display: flex; align-items: center; gap: 10px; max-width: 420px; border: 0; padding: 0; background: transparent; color: var(--gray-900); font-size: 14px; text-align: left; cursor: pointer; }
.file-name span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-name:disabled { cursor: default; }
.file-actions { display: flex; align-items: center; gap: 12px; }
.file-actions > a { display: inline-flex; align-items: center; gap: 4px; color: var(--main-color); }
.load-more { margin-top: 20px; text-align: center; }
button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 3px; }
@media (max-width: 768px) {
  .cloud-header { flex-wrap: wrap; padding: 16px; gap: 10px; }
  .header-actions { margin-left: 0; }
  .cloud-browser { grid-template-columns: 1fr; }
  .drive-sidebar { border-right: 0; border-bottom: 1px solid var(--gray-100); }
  .cloud-files { padding: 20px 16px; }
}
</style>
