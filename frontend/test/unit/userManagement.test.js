import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createRenderer, nextTick, ssrContextKey } from 'vue'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component, authApi, departmentApi
const pinia = createPinia()
const previousStorage = globalThis.localStorage
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  setActivePinia(pinia)
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ authApi, departmentApi } = await server.ssrLoadModule('/src/apis/index.js'))
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const user = useUserStore(pinia)
  user.userRole = 'superadmin'
  user.userId = 1
  ;({ default: component } = await server.ssrLoadModule('/src/modules/settings/ui/UserManagementComponent.vue'))
})
after(async () => {
  await server?.close()
  disposePinia(pinia)
  if (previousStorage === undefined) delete globalThis.localStorage
  else globalThis.localStorage = previousStorage
})
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
  parentNode: () => null, nextSibling: () => null, patchProp() {}
})

/** 运行完整组件及真实筛选 watcher，隔离网络接口。 */
async function setupPage(requests) {
  const previousUsers = authApi.getUsersPage, previousDepartments = departmentApi.getDepartments
  authApi.getUsersPage = async (params) => { requests.push(params); return { items: [], total: 0 } }
  departmentApi.getDepartments = async () => []
  let page
  const app = renderer.createApp({ setup() {
    page = component.setup({}, { expose() {} })
    return () => null
  } })
  app.use(pinia)
  app.provide(ssrContextKey, {})
  app.mount({ children: [] })
  await nextTick()
  return { page, close() {
    app.unmount()
    authApi.getUsersPage = previousUsers
    departmentApi.getDepartments = previousDepartments
  } }
}

test('用户类型默认排除 APP 身份，切换类型回到第一页并保留搜索条件', async () => {
  const requests = []
  const { page, close } = await setupPage(requests)
  try {
    const endUser = { id: 2, user_kind: 'end_user', role: 'user', username: 'app-visitor' }
    assert.equal(page.isUserEditDisabled(endUser), true)
    page.showEditUserModal(endUser)
    assert.equal(page.userManagement.modalVisible, false)
    assert.equal(page.userManagement.editUserId, null)
    const human = { ...endUser, user_kind: 'human', username: 'product-user' }
    assert.equal(page.isUserEditDisabled(human), false)
    page.showEditUserModal(human)
    assert.equal(page.userManagement.modalVisible, true)
    assert.equal(page.userManagement.form.username, 'product-user')
    await page.fetchUsers()
    assert.equal(requests[0].userKind, 'human')
    page.userManagement.currentPage = 3
    page.userManagement.searchKeyword = 'visitor'
    page.userManagement.userKindFilter = 'end_user'
    await nextTick()
    assert.deepEqual([page.userManagement.searchKeyword, page.userManagement.departmentFilterValues, page.userManagement.roleFilterValues, page.userManagement.userKindFilter], ['visitor', [], [], 'end_user'])
    assert.equal(page.userManagement.currentPage, 1)
    assert.equal(page.hasActiveFilters.value, true)
    await page.fetchUsers()
    assert.deepEqual(requests.at(-1), {
      offset: 0, limit: 20, search: 'visitor', departmentIds: [], roles: [], userKind: 'end_user'
    })
    page.userManagement.userKindFilter = 'all'
    await page.fetchUsers()
    assert.equal(requests.at(-1).userKind, 'all')
  } finally {
    close()
  }
})

test('表头筛选支持角色与部门多选，未选值不下发筛选参数', async () => {
  const requests = []
  const { page, close } = await setupPage(requests)
  try {
    await page.fetchUsers()
    // 未选择任何表头筛选时不携带多选参数
    assert.deepEqual(requests.at(-1).departmentIds, [])
    assert.deepEqual(requests.at(-1).roles, [])
    assert.equal(page.hasActiveFilters.value, false)

    page.setFilterOpen('role', true)
    page.filterDraft.role = ['admin', 'user']
    await page.fetchUsers()
    assert.deepEqual(requests.at(-1).roles, [])
    page.setFilterOpen('role', false)
    page.setFilterOpen('role', true)
    assert.deepEqual(page.filterDraft.role, [])
    page.filterDraft.role = ['admin', 'user']
    page.applyFilter('role')
    assert.equal(page.filterOpen.role, false)

    page.setFilterOpen('department', true)
    page.filterDraft.department = ['3', '5']
    page.applyFilter('department')
    await nextTick()
    await page.fetchUsers()
    assert.deepEqual(requests.at(-1).roles, ['admin', 'user'])
    assert.deepEqual(requests.at(-1).departmentIds, ['3', '5'])
    assert.equal(page.hasActiveFilters.value, true)

    page.setFilterOpen('role', true)
    page.filterDraft.role = []
    page.setFilterOpen('role', false)
    page.setFilterOpen('role', true)
    assert.deepEqual(page.filterDraft.role, ['admin', 'user'])
    page.filterDraft.role = []
    page.applyFilter('role')
    assert.deepEqual(page.userManagement.roleFilterValues, [])

    // APP 终端用户视图隐藏表头筛选
    page.userManagement.userKindFilter = 'end_user'
    assert.equal(page.showHeaderFilter.value, false)
    page.userManagement.userKindFilter = 'human'
    assert.equal(page.showHeaderFilter.value, true)
  } finally {
    close()
  }
})

test('部门筛选选项只保留有用户的部门，无人部门不进入候选', async () => {
  const { page, close } = await setupPage([])
  try {
    page.departmentManagement.departments = [
      { id: 1, name: '研发部', user_count: 5 },
      { id: 2, name: '空部门', user_count: 0 },
      { id: 3, name: '产品部', user_count: 2 }
    ]
    // 当前页用户兜底：部门列表缺失时按用户所在部门补齐。
    page.userManagement.users = [
      { id: 9, department_id: 3, department_name: '产品部' },
      { id: 10, department_id: null, department_name: '' }
    ]
    const labels = page.departmentFilterOptions.value.map((option) => option.label)
    assert.deepEqual(labels, ['研发部', '产品部'])
  } finally {
    close()
  }
})
