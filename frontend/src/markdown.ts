// 极简安全 Markdown 渲染：先整体转义 HTML，再处理受支持的标记。
// 支持 fences 代码块、标题、粗体/斜体/行内代码、链接、列表、简单表格、段落。

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

export function mdToHtml(md: string): string {
  if (!md) return ''
  const lines = escapeHtml(md).split(/\r?\n/)
  const out: string[] = []
  let i = 0
  let listType: 'ul' | 'ol' | null = null
  let inTable = false

  const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null } }
  const closeTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false } }

  while (i < lines.length) {
    const line = lines[i]

    // fenced code
    if (/^```/.test(line)) {
      closeList(); closeTable()
      i++
      const buf: string[] = []
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++ }
      i++ // 跳过收尾 ```
      out.push(`<pre>${buf.join('\n')}</pre>`)
      continue
    }
    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      closeList(); closeTable()
      out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`) // 官方 md 的 # 很大，降两级
      i++
      continue
    }
    // 分隔线
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      closeList(); closeTable()
      out.push('<hr/>')
      i++
      continue
    }
    // 表格（| a | b | + |---|---|）
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const isSep = /^\s*\|[\s:|-]+\|\s*$/.test(line)
      const nextIsSep = i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])
      closeList()
      if (!inTable && !isSep && nextIsSep) {
        // 表头 + 分隔行
        out.push('<table class="md-table"><thead>')
        inTable = true
        const heads = line.split('|').slice(1, -1).map(c => c.trim())
        out.push('<tr>' + heads.map(hd => `<th>${inline(hd)}</th>`).join('') + '</tr></thead><tbody>')
        i += 2
        continue
      }
      if (inTable) {
        if (isSep) { i++; continue }
        const cells = line.split('|').slice(1, -1).map(c => c.trim())
        out.push('<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>')
        i++
        continue
      }
      closeTable() // 不构成表格的竖线行按段落处理
    }
    if (inTable && !/^\s*\|/.test(line)) closeTable()

    // 无序 / 有序列表
    const ul = line.match(/^\s*[-*+]\s+(.*)$/)
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ul || ol) {
      closeTable()
      const want: 'ul' | 'ol' = ul ? 'ul' : 'ol'
      if (listType !== want) { closeList(); out.push(`<${want}>`); listType = want }
      out.push(`<li>${inline((ul || ol)![1])}</li>`)
      i++
      continue
    }
    closeList()

    // 空行
    if (!line.trim()) { closeTable(); i++; continue }

    // 段落（连续非空行合并）
    const buf = [line]
    i++
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*+]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) {
      buf.push(lines[i]); i++
    }
    out.push(`<p>${buf.map(inline).join('<br/>')}</p>`)
  }
  closeList(); closeTable()
  return out.join('\n')
}
