const root = `${import.meta.env.BASE_URL}cherry-studio/painting-templates/`
const screenshotOrder = [
  'slow-shutter-fashion',
  'human-fragments-motion',
  'light-trails',
  'doodle-shadow',
  'deadpan-cat'
]

/** 保留源目录内容，首屏用用户截图中的五张图作为轮播初始位置。 */
export async function loadPaintingTemplates(signal) {
  const [manifest, entries] = await Promise.all(
    ['catalog.json', 'locales/zh-cn.json'].map(async (file) => {
      const response = await fetch(root + file, { signal })
      if (!response.ok) throw new Error('绘画模板加载失败')
      return response.json()
    })
  )
  return [...new Set([...screenshotOrder, ...manifest])]
    .filter((id) => entries[id]?.prompt)
    .map((id) => ({ id, ...entries[id], imageUrl: `${root}images/${id}.webp` }))
}
