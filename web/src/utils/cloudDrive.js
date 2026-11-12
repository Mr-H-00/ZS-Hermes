/** 仅允许 WPS 响应中的 HTTPS 地址进入打开和下载操作。 */
export function safeCloudUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : ''
  } catch {
    return ''
  }
}

/** 回调登记使用公开 HTTPS 域名，拒绝本机地址、IP 和多余参数。 */
export function wpsCallbackUrl(value) {
  try {
    const url = new URL(value)
    const host = url.hostname.replace(/\.$/, '')
    if (url.protocol !== 'https:' || !host.includes('.') ||
        /^(?:\d+\.){3}\d+$/.test(host) || host.startsWith('[') ||
        host.endsWith('.localhost') || host.endsWith('.local') ||
        url.pathname !== '/auth/wps/callback' || url.search || url.hash || url.username || url.password) return ''
    return url.href
  } catch {
    return ''
  }
}

/** 浏览器必须处于已登记的正式域名或当前联调域名，才能保留本域登录态。 */
export function wpsAuthorizationOrigin(connection, origin) {
  if (connection?.development_tunnel_active && connection.development_callback_origin === origin) return origin
  return connection?.authorization_origin || (connection?.development_tunnel_active ? connection.development_callback_origin : '') || ''
}

/** 文件搜索结果使用 file 包装；目录列表直接返回文件对象。 */
export function normalizeWPSFiles(items) {
  return items.map((item) => item.file || item).filter((file) => file.id && file.name)
}

/** WPS mtime 以秒为单位，空值保持空展示。 */
export function formatModifiedAt(value) {
  if (!value) return '—'
  const date = new Date(Number(value) * 1000)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
