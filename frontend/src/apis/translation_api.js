import { apiGet, apiPost } from './base'

export function translateText(payload, { signal } = {}) {
  return apiPost('/api/translate', payload, { signal })
}

export function detectTranslationLanguage(payload, { signal } = {}) {
  return apiPost('/api/translate/detect', payload, { signal })
}

export function extractTranslationFile(file, { signal } = {}) {
  const data = new FormData()
  data.append('file', file)
  return apiPost('/api/translate/extract', data, { signal })
}

export function getPdfTranslationCapability({ signal } = {}) {
  return apiGet('/api/translate/pdf/capability', { signal })
}

export async function translatePdf(file, options, { signal } = {}) {
  const data = new FormData()
  data.append('file', file)
  for (const [key, value] of Object.entries(options)) data.append(key, value)
  const response = await apiPost('/api/translate/pdf', data, { signal }, true, 'blob')
  return response.blob()
}
