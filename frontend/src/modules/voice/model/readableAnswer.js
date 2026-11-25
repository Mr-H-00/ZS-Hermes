import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt('commonmark', { html: true })

/** 只有服务端标记为正式结果的已完成回复可以朗读。 */
export function isReadableAnswer(message) {
  return message?.phase === 'final_answer' && message.execution_status === 'completed' &&
    typeof message.content === 'string' && Boolean(message.content.trim())
}

/** 朗读正文，跳过围栏代码、工具组件数据与链接地址。 */
export function toSpokenText(content) {
  return markdown.parse(content, {})
    .map((token) => {
      if (token.type === 'html_block') {
        // 模板内容保持惰性，只读取文本，不把模型 HTML 插入页面。
        const template = globalThis.document?.createElement?.('template')
        if (!template) return ''
        template.innerHTML = token.content
        template.content.querySelectorAll('script, style, template, noscript, [hidden]')
          .forEach((node) => node.remove())
        return template.content.textContent || ''
      }
      if (token.type !== 'inline') return ''
      return token.children.map((child) => {
        if (child.type === 'text' || child.type === 'code_inline' || child.type === 'image') {
          return child.content
        }
        return child.type === 'softbreak' || child.type === 'hardbreak' ? '\n' : ''
      }).join('')
    })
    .filter((line) => line.trim())
    .join('\n\n')
}
