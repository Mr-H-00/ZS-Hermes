/** 明暗色卡共用主体与人物背景色，夜间版本只调整底纸与墨色。 */
function palette(label, description, light, dark) {
  return {
    label,
    description,
    light,
    dark: { ...dark, body: light.body, background: light.background }
  }
}
export const presets = {
  default: palette(
    '官方原色',
    '保留原作的鲜明与轻快',
    {
      body: ['76a7ff', '75c675', 'ffaa64', 'ff4d6f', 'af61f2'],
      background: ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'],
      paper: 'ffffff',
      ink: '292d38'
    },
    { paper: '222633', ink: '252936' }
  ),
  studio: palette(
    '设计工作室',
    '钴蓝 · 珊瑚 · 黄油黄 · 薄荷',
    {
      body: ['6489c7', 'e69482', 'e4c66f', '89b4a2'],
      background: ['dce5f3', 'f5ddd6', 'f4ebc9', 'dcebe3'],
      paper: 'f5f3ee',
      ink: '333846'
    },
    { paper: '242832', ink: '29313e' }
  ),
  heritage: palette(
    '复古印刷',
    '陶红 · 芥黄 · 湖绿 · 烟紫',
    {
      body: ['bf796b', 'c5a55b', '709b95', 'a18da9'],
      background: ['e8cdc5', 'e9ddba', 'ccdfd9', 'ded2e3'],
      paper: 'f4eee3',
      ink: '433a36'
    },
    { paper: '302c2b', ink: '403832' }
  ),
  soft: palette(
    '柔光粉彩',
    '雾蓝 · 丁香 · 杏桃 · 鼠尾草',
    {
      body: ['a5bbd5', 'bba9cf', 'e5b89a', 'a6bca2'],
      background: ['e1e9f2', 'e9e1f0', 'f5e5da', 'e3ecdf'],
      paper: 'f6f4f7',
      ink: '454451'
    },
    { paper: '2b2935', ink: '3a3546' }
  )
}

/** 把视觉色卡映射到每种风格的原生颜色通道。 */
export function paletteOptions(style, preset, dark) {
  const { body, paper, ink } = presets[preset][dark ? 'dark' : 'light']
  const light = presets[preset].light
  // 人物风格保留浅色画纸；用户的背景与整张头像不随主题改变。
  if (style === 'notionists') {
    return {
      backgroundColor: light.background,
      ...(preset === 'default' ? {} : { paperColor: [light.paper], inkColor: [light.ink] })
    }
  }
  if (style === 'lorelei') {
    return { backgroundColor: light.background }
  }
  // 官方原色由各风格的上游定义拥有，避免夜间模式替换主体色板。
  if (preset === 'default') {
    if (dark && style === 'glyphs') return { paperColor: [paper] }
    if (dark && style === 'clay') return { backgroundColor: [paper] }
    return {}
  }
  switch (style) {
    case 'glyphs':
      return { glyphColor: body, ...(dark ? { paperColor: [paper] } : {}) }
    case 'clay':
      return { backgroundColor: [paper], bodyColor: body, accentColor: body, inkColor: [ink] }
    case 'shape-grid':
      return { backgroundColor: body }
    case 'gaze':
      return { bodyColor: body, inkColor: [ink], glintColor: ['ffffff'] }
  }
}
