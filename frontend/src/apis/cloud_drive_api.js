import { apiAdminDelete, apiAdminPost, apiAdminGet, apiAdminPut, apiDelete, apiGet, apiPost, buildQuery } from './base'

const WPS_BASE = '/api/cloud-drive/wps'

export const wpsCloudApi = {
  getConfig: () => apiAdminGet(`${WPS_BASE}/config`),
  saveConfig: (config) => apiAdminPut(`${WPS_BASE}/config`, config),
  getDevelopmentTunnel: () => apiGet(`${WPS_BASE}/development-tunnel`),
  startDevelopmentTunnel: () => apiAdminPost(`${WPS_BASE}/development-tunnel`),
  stopDevelopmentTunnel: () => apiAdminDelete(`${WPS_BASE}/development-tunnel`),
  getConnection: () => apiGet(`${WPS_BASE}/connection`),
  checkConnection: () => apiPost(`${WPS_BASE}/connection/check`),
  authorize: () => apiPost(`${WPS_BASE}/authorize`),
  exchange: (code, state) => apiPost(`${WPS_BASE}/exchange`, { code, state }),
  disconnect: () => apiDelete(`${WPS_BASE}/connection`),
  getDrives: (pageToken) => apiGet(`${WPS_BASE}/drives?${buildQuery({ page_token: pageToken })}`),
  getFiles: (driveId, parentId = '0', pageToken) =>
    apiGet(`${WPS_BASE}/files?${buildQuery({ drive_id: driveId, parent_id: parentId, page_token: pageToken })}`),
  searchFiles: (keyword, pageToken) =>
    apiGet(`${WPS_BASE}/search?${buildQuery({ keyword, page_token: pageToken })}`),
  getDownload: (driveId, fileId) =>
    apiGet(`${WPS_BASE}/download?${buildQuery({ drive_id: driveId, file_id: fileId })}`)
}
