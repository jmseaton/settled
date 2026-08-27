import { useCallback, useEffect, useState } from 'react'
import { api, type CorporateActionRow, type Note, type OptionEvent, type Tag } from '../api'
import { Markdown } from '../Markdown'
import { money, num } from '../format'

const input = {
  background: 'var(--panel-2)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '6px 9px',
  fontSize: 13,
}

const TAG_GROUPS = ['Setup', 'Mistake', 'Market Regime', 'Emotion'] as const

function EventsPanel({ events }: { events: OptionEvent[] }) {
  if (!events.length) return <p className="muted">No assignments, exercises or expirations yet.</p>
  return (
    <div className="scroll" style={{ maxHeight: '45vh' }}>
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Event</th>
            <th>Contract</th>
            <th className="num">Qty</th>
            <th>Resulting position</th>
            <th>Settlement</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{e.occurred_on}</td>
              <td>
                <span className="tag">{e.event_type}</span>
              </td>
              <td>{e.symbol}</td>
              <td className="num">{num(e.quantity, 0)}</td>
              <td className={e.underlying ? '' : 'muted'} style={{ whiteSpace: 'normal', maxWidth: 420 }}>
                {e.underlying ? (
                  <>
                    {e.underlying.side} {Math.abs(e.underlying.quantity)} {e.underlying.symbol} @{' '}
                    {money(e.underlying.price)}
                  </>
                ) : (
                  e.link_note || '—'
                )}
              </td>
              <td className="muted">{e.settlement ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function JournalPage({ onChanged }: { onChanged?: () => void }) {
  const [events, setEvents] = useState<OptionEvent[]>([])
  const [disclaimer, setDisclaimer] = useState('')
  const [tags, setTags] = useState<Tag[]>([])
  const [notes, setNotes] = useState<Note[]>([])
  const [actions, setActions] = useState<CorporateActionRow[]>([])
  const [actionNote, setActionNote] = useState('')
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [newTag, setNewTag] = useState('')
  const [newTagGroup, setNewTagGroup] = useState<string>(TAG_GROUPS[0])
  const [newNote, setNewNote] = useState('')

  const load = useCallback(() => {
    api.optionEvents().then((r) => {
      setEvents(r.items)
      setDisclaimer(r.disclaimer)
    }).catch((e) => setError(String(e)))
    api.tags().then((r) => setTags(r.items)).catch(() => undefined)
    api.notes(search ? { q: search } : {}).then((r) => setNotes(r.items)).catch(() => undefined)
    api.corporateActions().then((r) => {
      setActions(r.items)
      setActionNote(r.note)
    }).catch(() => undefined)
  }, [search])

  useEffect(load, [load])

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      load()
      onChanged?.()
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    }
  }

  const byGroup = new Map<string, Tag[]>()
  for (const tag of tags) {
    const key = tag.group_name || 'Ungrouped'
    byGroup.set(key, [...(byGroup.get(key) ?? []), tag])
  }

  return (
    <>
      <h1>Journal</h1>
      <p className="subtitle">
        Assignments and exercises as the broker reported them (§5.4), plus tags, notes and the
        corporate actions that adjust historical figures (§11).
      </p>

      {error && <div className="banner err">{error}</div>}

      <h2>Option events</h2>
      <div className="panel">
        <EventsPanel events={events} />
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>{disclaimer}</div>
      </div>

      <h2>Tags</h2>
      <div className="panel">
        <div className="toolbar">
          <input
            type="text"
            placeholder="New tag"
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            style={{ ...input, width: 180 }}
          />
          <select value={newTagGroup} onChange={(e) => setNewTagGroup(e.target.value)}>
            {TAG_GROUPS.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
          <button
            className="primary"
            disabled={!newTag.trim()}
            onClick={() =>
              run(async () => {
                await api.createTag({ name: newTag, group_name: newTagGroup })
                setNewTag('')
              })
            }
          >
            Add tag
          </button>
        </div>

        {[...byGroup.entries()].map(([group, groupTags]) => (
          <div key={group} style={{ marginBottom: 10 }}>
            <div className="mode-label">{group}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {groupTags.map((tag) => (
                <span key={tag.id} className="tag" style={{ padding: '3px 9px' }}>
                  {tag.name}
                  <span className="muted"> · {tag.usage_count ?? 0}</span>
                  {!tag.system && (
                    <button
                      title="Delete tag"
                      onClick={() => run(() => api.deleteTag(tag.id))}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--muted)',
                        cursor: 'pointer',
                        padding: '0 0 0 6px',
                      }}
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          </div>
        ))}
        <div className="muted" style={{ fontSize: 12 }}>
          Tags applied automatically by the app (like <span className="tag">assigned</span>) cannot
          be deleted — the next import would recreate them, and removing one would silently empty
          a filter you believe is complete.
        </div>
      </div>

      <h2>Notes</h2>
      <div className="panel">
        <div className="toolbar">
          <input
            type="search"
            placeholder="Search notes"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ ...input, width: 240 }}
          />
        </div>
        <textarea
          value={newNote}
          onChange={(e) => setNewNote(e.target.value)}
          placeholder="Markdown. A general note — trade and day notes are added from their own views."
          rows={3}
          style={{ ...input, width: '100%', fontFamily: 'inherit' }}
        />
        <div className="toolbar" style={{ marginTop: 10 }}>
          <button
            className="primary"
            disabled={!newNote.trim()}
            onClick={() =>
              run(async () => {
                await api.createNote({ scope: 'GENERAL', body_md: newNote })
                setNewNote('')
              })
            }
          >
            Save note
          </button>
        </div>

        {notes.map((note) => (
          <div
            key={note.id}
            style={{
              borderTop: '1px solid var(--border)',
              paddingTop: 10,
              marginTop: 10,
              fontSize: 13,
            }}
          >
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
              {note.scope}
              {note.scope_ref ? ` #${note.scope_ref}` : ''} · {note.updated_at?.slice(0, 10)}
              {note.draft && (
                <>
                  {' '}
                  <span className="tag">auto-draft</span>
                </>
              )}
              <button
                onClick={() => run(() => api.deleteNote(note.id))}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--muted)',
                  cursor: 'pointer',
                  float: 'right',
                }}
              >
                Delete
              </button>
            </div>
            <Markdown body={note.body_md} />
          </div>
        ))}
        {!notes.length && <p className="muted">No notes yet.</p>}
      </div>

      <h2>Corporate actions</h2>
      <div className="panel">
        {actions.length ? (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Symbol</th>
                <th>Type</th>
                <th className="num">Ratio</th>
                <th>Applied</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => (
                <tr key={a.id}>
                  <td>{a.occurred_on}</td>
                  <td>{a.symbol ?? '—'}</td>
                  <td>{a.action_type}</td>
                  <td className="num">{a.split_ratio ? `${num(a.split_ratio, 4)}:1` : '—'}</td>
                  <td>{a.applied ? 'yes' : <span className="muted">recorded only</span>}</td>
                  <td>
                    <span className="tag">{a.source}</span>
                  </td>
                  <td>
                    {a.source === 'MANUAL' && (
                      <button
                        className="primary"
                        style={{ padding: '3px 9px', fontSize: 12 }}
                        onClick={() => run(() => api.deleteCorporateAction(a.id))}
                      >
                        Undo
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">None recorded.</p>
        )}
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>{actionNote}</div>
      </div>

      <div className="footer-note">
        Everything written here is keyed to the broker's own execution identifiers, not to trade
        ids — trades are deleted and re-derived on every import, so a tag or note stored against
        one would silently detach on the next sync. A trade whose opening leg was reconstructed
        (§5.5) has no such identifier and is refused rather than accepted and lost.
      </div>
    </>
  )
}
