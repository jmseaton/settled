import { Fragment, type ReactNode } from 'react'

/**
 * §11.2 — notes are markdown, so they should not read as asterisks.
 *
 * Deliberately tiny, and deliberately **not** `dangerouslySetInnerHTML`. A
 * note can quote a broker description or an error message, and turning
 * user-supplied text into raw HTML to get bold type would be trading a
 * cosmetic win for an injection surface. Everything here builds React
 * elements, so there is no path from note text to markup by construction.
 *
 * Supports what a trading journal actually uses: bold, italic, inline code,
 * bullet lists, and paragraphs. Anything else renders as the literal text
 * that was typed, which is the right failure — the note is still readable.
 */

const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g

function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const key = `${keyPrefix}-${i}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={key}>{part.slice(1, -1)}</code>
    }
    return <Fragment key={key}>{part}</Fragment>
  })
}

export function Markdown({ body }: { body: string }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []

  const flushBullets = () => {
    if (!bullets.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} style={{ margin: '6px 0', paddingLeft: 20 }}>
        {bullets.map((item, i) => (
          <li key={i}>{inline(item, `li-${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    )
    bullets = []
  }

  for (const line of (body || '').split('\n')) {
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
    if (bullet) {
      bullets.push(bullet[1])
      continue
    }
    flushBullets()
    if (!line.trim()) continue
    blocks.push(
      <p key={`p-${blocks.length}`} style={{ margin: '6px 0' }}>
        {inline(line, `p-${blocks.length}`)}
      </p>,
    )
  }
  flushBullets()

  return <>{blocks}</>
}
