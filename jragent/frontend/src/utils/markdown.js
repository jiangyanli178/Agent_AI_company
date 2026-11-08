// 轻量 Markdown 渲染，不引外部库（省 30KB+ 体积）
// 只覆盖 AI 实际会输出的语法：标题、粗体、列表、表格、代码、分割线

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

export function renderMarkdown(text) {
  if (!text) return ''

  const src = escapeHtml(String(text)).replace(/\r\n/g, '\n')

  // 先把代码块抽出来占位，避免里面的符号被其它规则改写
  const blocks = []
  const withPlaceholder = src.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push({ lang, code })
    return `\u0000CODE${blocks.length - 1}\u0000`
  })

  const lines = withPlaceholder.split('\n')
  const out = []
  let listType = null   // 'ul' | 'ol'

  const closeList = () => {
    if (listType) { out.push(`</${listType}>`); listType = null }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()

    // 代码块占位
    const ph = trimmed.match(/^\u0000CODE(\d+)\u0000$/)
    if (ph) {
      closeList()
      const b = blocks[Number(ph[1])]
      out.push(`<pre><code>${b.code.replace(/\n$/, '')}</code></pre>`)
      continue
    }

    if (!trimmed) { closeList(); continue }

    // 分割线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      closeList(); out.push('<hr>'); continue
    }

    // 标题
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      closeList()
      const lv = Math.min(h[1].length + 2, 6)   // h1→h3，避免页面出现多个 h1
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`)
      continue
    }

    // 表格：当前行有 | 且下一行是分隔行
    if (trimmed.includes('|') && /^\|?[\s:|-]+\|[\s:|-]*$/.test((lines[i + 1] || '').trim())) {
      closeList()
      const parseRow = (r) => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim())
      const head = parseRow(trimmed)
      i++   // 跳过分隔行
      const body = []
      while (i + 1 < lines.length && lines[i + 1].includes('|') && lines[i + 1].trim()) {
        i++
        body.push(parseRow(lines[i].trim()))
      }
      out.push('<div class="md-table"><table><thead><tr>' +
        head.map(c => `<th>${inline(c)}</th>`).join('') +
        '</tr></thead><tbody>' +
        body.map(r => '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>')
      continue
    }

    // 无序列表
    const ul = trimmed.match(/^[-*+]\s+(.*)$/)
    if (ul) {
      if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul' }
      out.push(`<li>${inline(ul[1])}</li>`)
      continue
    }

    // 有序列表
    const ol = trimmed.match(/^\d+[.)]\s+(.*)$/)
    if (ol) {
      if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol' }
      out.push(`<li>${inline(ol[1])}</li>`)
      continue
    }

    // 引用
    const quote = trimmed.match(/^&gt;\s?(.*)$/)
    if (quote) {
      closeList()
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`)
      continue
    }

    // 普通段落
    closeList()
    out.push(`<p>${inline(trimmed)}</p>`)
  }
  closeList()
  return out.join('\n')
}
