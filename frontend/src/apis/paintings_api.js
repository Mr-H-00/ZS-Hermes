import { apiPost } from './base'

export const paintingsApi = {
  /** 按选择的模型能力提交生成参数，接收一组图片字节。 */
  async render({ modelSpec, prompt, size, count, referenceImages, seed, negativePrompt, guidanceScale }, { signal } = {}) {
    const result = await apiPost('/api/paintings/render', {
      model_spec: modelSpec,
      prompt,
      size,
      count,
      reference_images: referenceImages,
      seed: seed ?? undefined,
      negative_prompt: negativePrompt || undefined,
      guidance_scale: guidanceScale ?? undefined
    }, { signal })
    if (!Array.isArray(result?.images) || !result.images.length) throw new Error('未收到有效的图片')
    return result.images.map(({ mime_type: mime, data_base64: encoded }) => {
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(mime) || !encoded) {
        throw new Error('未收到有效的图片')
      }
      return new Blob([Uint8Array.from(atob(encoded), (value) => value.charCodeAt(0))], { type: mime })
    })
  },

  /** 通过认证接口生成 PNG，并读取图片字节。 */
  async generate({ modelSpec, prompt, size }, { signal } = {}) {
    const response = await apiPost(
      '/api/paintings/generate',
      { model_spec: modelSpec, prompt, size },
      { signal },
      true,
      'blob'
    )
    const image = await response.blob()
    if (image.type !== 'image/png' || !image.size) throw new Error('未收到有效的 PNG 图片')
    return image
  }
}
