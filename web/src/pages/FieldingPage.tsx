import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import axios from 'axios'
import {
  fieldingApi,
  questionnaireApi,
  FieldingLink,
  FieldingRedirects,
  QuotaCell,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { CardListSkeleton } from '../components/Skeleton'
import RoutingEditor, { FlatRouting, fromFlatRouting, toFlatRouting } from '../components/RoutingEditor'
import ThemeSelect from '../components/ThemeSelect'

// Roles allowed to write (matches api/auth.py require_role(OWNER, ANALYST)).
const WRITE_ROLES = new Set(['owner', 'analyst'])

function errorMessage(err: unknown): string | null {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail?.error) return detail.error
    return err.message
  }
  return err instanceof Error ? err.message : null
}

const REDIRECT_FIELDS: Array<{ key: keyof FieldingRedirects; label: string }> = [
  { key: 'redirect_complete', label: 'Complete (10)' },
  { key: 'redirect_terminate', label: 'Terminate (20)' },
  { key: 'redirect_overquota', label: 'Over-quota (40)' },
  { key: 'redirect_security', label: 'Security (30)' },
]

// --- Small helpers ----------------------------------------------------------

function CountPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex flex-col items-center px-3 py-1.5 rounded-md bg-slate-50 border border-slate-200 min-w-[70px]">
      <span className={`text-lg font-semibold font-mono tabular-nums ${color}`}>{value}</span>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
    </div>
  )
}

function CopyableUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-center gap-2">
      <code className="text-xs text-slate-700 bg-slate-50 border border-slate-200 rounded px-2 py-1 break-all flex-1">
        {url}
      </code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard.writeText(url)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
        className="text-xs font-medium text-primary-500 hover:text-primary-600 shrink-0"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

// --- Quota management --------------------------------------------------------

function QuotaBar({ count, limit }: { count: number; limit: number }) {
  const pct = limit > 0 ? Math.min((count / limit) * 100, 100) : 0
  const color = count >= limit ? 'bg-red-400' : pct > 80 ? 'bg-amber-400' : 'bg-emerald-400'
  return (
    <div className="h-1.5 w-24 rounded-full bg-slate-100 overflow-hidden">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function QuotaSection({
  studyId,
  linkId,
  qidOptions,
  canWrite,
}: {
  studyId: string
  linkId: string
  qidOptions: string[]
  canWrite: boolean
}) {
  const [cells, setCells] = useState<QuotaCell[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [limit, setLimit] = useState(100)
  const [flat, setFlat] = useState<FlatRouting>(() => toFlatRouting(null))
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fieldingApi.listQuotas(studyId, linkId)
      setCells(res.data.cells)
      setError(null)
    } catch (err) {
      setError(errorMessage(err) || 'Could not load quotas')
    }
  }, [studyId, linkId])

  useEffect(() => {
    load()
  }, [load])

  const handleAdd = async () => {
    setSaving(true)
    setError(null)
    try {
      const cond = fromFlatRouting(flat, null)
      if (!cond) {
        setError('Add at least one condition for the quota cell.')
        setSaving(false)
        return
      }
      await fieldingApi.createQuota(studyId, linkId, {
        name: name.trim() || 'Cell',
        condition: cond as unknown as Record<string, unknown>,
        limit,
      })
      setName('')
      setLimit(100)
      setFlat(toFlatRouting(null))
      setAdding(false)
      await load()
    } catch (err) {
      setError(errorMessage(err) || 'Could not create quota cell')
    } finally {
      setSaving(false)
    }
  }

  const handleLimit = async (cell: QuotaCell, newLimit: number) => {
    setError(null)
    try {
      await fieldingApi.updateQuota(studyId, linkId, cell.id, { limit: newLimit })
      await load()
    } catch (err) {
      setError(errorMessage(err) || 'Could not update limit')
    }
  }

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Quota cells</span>
        {canWrite && !adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-xs text-primary-500 hover:text-primary-600 font-medium"
          >
            + Add cell
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-600 mb-2">{error}</p>}

      {cells === null ? (
        <p className="text-xs text-slate-400">Loading…</p>
      ) : cells.length === 0 && !adding ? (
        <p className="text-xs text-slate-400">No quota cells — every complete counts toward the total.</p>
      ) : (
        <div className="space-y-2">
          {cells.map((cell) => (
            <div key={cell.id} className="flex items-center gap-3 text-sm">
              <span className="font-medium text-slate-700 w-32 truncate" title={cell.name}>
                {cell.name}
              </span>
              <code className="text-[11px] text-slate-500 flex-1 truncate" title={JSON.stringify(cell.condition)}>
                {JSON.stringify(cell.condition)}
              </code>
              <QuotaBar count={cell.count} limit={cell.limit} />
              <span className="text-xs text-slate-600 w-16 text-right font-mono tabular-nums">
                {cell.count} / {cell.limit}
              </span>
              {canWrite && (
                <input
                  type="number"
                  min={0}
                  defaultValue={cell.limit}
                  onBlur={(e) => {
                    const v = Number(e.target.value)
                    if (Number.isFinite(v) && v !== cell.limit) handleLimit(cell, v)
                  }}
                  className="h-7 w-20 px-2 rounded border border-slate-300 text-xs"
                  aria-label={`limit for ${cell.name}`}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {adding && canWrite && (
        <div className="mt-3 border border-slate-200 rounded-md p-3 space-y-3">
          <div className="flex items-center gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Cell name (e.g. Male 18-34)"
              className="h-8 flex-1 px-2 rounded border border-slate-300 text-sm"
            />
            <label className="text-xs text-slate-500">Limit</label>
            <input
              type="number"
              min={1}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="h-8 w-24 px-2 rounded border border-slate-300 text-sm"
            />
          </div>
          <RoutingEditor
            label="Count a respondent in this cell if"
            flat={flat}
            onChange={setFlat}
            qidOptions={qidOptions}
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleAdd}
              disabled={saving}
              className="bg-primary-500 text-white text-xs font-medium px-3 py-1.5 rounded-md hover:bg-primary-600 disabled:opacity-50 transition-all duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
            >
              {saving ? 'Adding…' : 'Add cell'}
            </button>
            <button
              type="button"
              onClick={() => {
                setAdding(false)
                setError(null)
              }}
              className="text-xs text-slate-500 hover:text-slate-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// --- Link card --------------------------------------------------------------

function LinkCard({
  studyId,
  link,
  qidOptions,
  canWrite,
  onChanged,
}: {
  studyId: string
  link: FieldingLink
  qidOptions: string[]
  canWrite: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [materializeMsg, setMaterializeMsg] = useState<string | null>(null)
  const [showQuotas, setShowQuotas] = useState(false)

  const publicUrl = `${window.location.origin}${link.respondent_path}`
  const c = link.counts

  const handleClose = async () => {
    if (!window.confirm('Close this link? New respondents will be turned away; in-flight sessions may still finish.')) return
    setBusy(true)
    setError(null)
    try {
      await fieldingApi.closeLink(studyId, link.id)
      onChanged()
    } catch (err) {
      setError(errorMessage(err) || 'Could not close link')
    } finally {
      setBusy(false)
    }
  }

  const handleMaterialize = async () => {
    setBusy(true)
    setError(null)
    setMaterializeMsg(null)
    try {
      const res = await fieldingApi.materialize(studyId, link.id)
      setMaterializeMsg(
        `Materialized ${res.data.n_respondents} respondents × ${res.data.n_variables} variables.`,
      )
      onChanged()
    } catch (err) {
      setError(errorMessage(err) || 'Materialize failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                link.status === 'open'
                  ? 'bg-emerald-50 text-emerald-700'
                  : 'bg-slate-100 text-slate-500'
              }`}
            >
              {link.status}
            </span>
            <span className="text-xs text-slate-400">v{link.survey_version}</span>
            <span className="text-xs text-slate-400">
              created {new Date(link.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
            </span>
          </div>
        </div>
        {canWrite && (
          <div className="flex items-center gap-2">
            {link.status === 'open' && (
              <button
                onClick={handleClose}
                disabled={busy}
                className="text-xs border border-slate-300 text-slate-700 font-medium px-3 py-1.5 rounded-md hover:bg-slate-50 disabled:opacity-50 transition-all duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
              >
                Close
              </button>
            )}
            <button
              onClick={handleMaterialize}
              disabled={busy}
              title="Load completed responses into this study's tables and analysis"
              className="text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded-md hover:bg-primary-600 disabled:opacity-50 transition-all duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
            >
              {busy ? 'Working…' : 'Materialize'}
            </button>
          </div>
        )}
      </div>

      <CopyableUrl url={publicUrl} />

      <div className="flex flex-wrap gap-2 mt-3">
        <CountPill label="starts" value={c.starts ?? 0} color="text-slate-700" />
        <CountPill label="completes" value={c.completes ?? 0} color="text-emerald-600" />
        <CountPill label="terminates" value={c.terminates ?? 0} color="text-amber-600" />
        <CountPill label="overquota" value={c.overquota ?? 0} color="text-red-500" />
        <CountPill label="security" value={c.security ?? 0} color="text-red-500" />
      </div>

      {materializeMsg && (
        <div className="mt-3 bg-emerald-50 border border-emerald-200 rounded-md p-3 text-sm text-emerald-700 flex items-center justify-between">
          <span>{materializeMsg}</span>
          <Link to={`/projects/${studyId}`} className="font-medium underline hover:text-emerald-800">
            View tables →
          </Link>
        </div>
      )}
      {error && <p className="text-sm text-red-600 mt-3">{error}</p>}

      <button
        type="button"
        onClick={() => setShowQuotas((s) => !s)}
        className="mt-3 text-xs text-slate-500 hover:text-slate-700"
      >
        {showQuotas ? '▾ Hide quotas' : '▸ Manage quotas'}
      </button>
      {showQuotas && (
        <QuotaSection studyId={studyId} linkId={link.id} qidOptions={qidOptions} canWrite={canWrite} />
      )}
    </div>
  )
}

// --- Create panel -----------------------------------------------------------

function CreatePanel({
  studyId,
  canCreate,
  canWrite,
  onCreated,
}: {
  studyId: string
  canCreate: boolean
  canWrite: boolean
  onCreated: () => void
}) {
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showRedirects, setShowRedirects] = useState(false)
  const [redirects, setRedirects] = useState<FieldingRedirects>({})
  // '' = inherit the survey's default theme (don't stamp an override on the link).
  const [theme, setTheme] = useState<string>('')

  const handleCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      const payload = Object.fromEntries(
        Object.entries(redirects).filter(([, v]) => v && String(v).trim()),
      )
      await fieldingApi.createLink(
        studyId,
        Object.keys(payload).length ? payload : undefined,
        theme || undefined,
      )
      setRedirects({})
      setTheme('')
      setShowRedirects(false)
      onCreated()
    } catch (err) {
      setError(errorMessage(err) || 'Could not create fielding link')
    } finally {
      setCreating(false)
    }
  }

  if (!canWrite) return null

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-5 mb-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-slate-900">New fielding link</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Opens a public respondent link for the study's approved questionnaire.
          </p>
        </div>
        <button
          onClick={handleCreate}
          disabled={creating || !canCreate}
          title={canCreate ? '' : 'The study needs an approved questionnaire first'}
          className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50 transition-all duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
        >
          {creating ? 'Creating…' : 'Create link'}
        </button>
      </div>

      {!canCreate && (
        <p className="text-xs text-amber-600 mt-2">
          No approved questionnaire yet.{' '}
          <Link to={`/projects/${studyId}/questionnaire`} className="underline">
            Review &amp; approve the questionnaire
          </Link>{' '}
          first.
        </p>
      )}

      <div className="mt-3 flex items-center gap-3">
        <label htmlFor="link-theme" className="text-xs text-slate-500 w-28 shrink-0">
          Respondent theme
        </label>
        <ThemeSelect
          id="link-theme"
          value={theme}
          onChange={setTheme}
          inheritLabel="Survey default"
        />
        <span className="text-xs text-slate-400">Overrides the survey default for this link only.</span>
      </div>

      <button
        type="button"
        onClick={() => setShowRedirects((s) => !s)}
        className="mt-3 text-xs text-slate-500 hover:text-slate-700"
      >
        {showRedirects ? '▾ Hide vendor redirects' : '▸ Vendor redirect templates (optional)'}
      </button>
      {showRedirects && (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-slate-400">
            Panel/vendor redirect URLs per outcome. Use <code>{'{pid}'}</code> / <code>{'{rid}'}</code>{' '}
            placeholders — they're substituted from the respondent's entry params.
          </p>
          {REDIRECT_FIELDS.map(({ key, label }) => (
            <div key={key} className="flex items-center gap-2">
              <label className="text-xs text-slate-500 w-28 shrink-0">{label}</label>
              <input
                value={redirects[key] ?? ''}
                onChange={(e) => setRedirects((r) => ({ ...r, [key]: e.target.value }))}
                placeholder="https://vendor.example/redirect?rid={rid}"
                className="h-8 flex-1 px-2 rounded border border-slate-300 text-sm"
              />
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
    </div>
  )
}

// --- Page -------------------------------------------------------------------

export default function FieldingPage() {
  const { id: studyId } = useParams<{ id: string }>()
  const { user } = useAuth()
  const canWrite = !!user && WRITE_ROLES.has(user.role)

  const [links, setLinks] = useState<FieldingLink[] | null>(null)
  const [qidOptions, setQidOptions] = useState<string[]>([])
  const [canCreate, setCanCreate] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const linksRef = useRef<FieldingLink[] | null>(null)

  const load = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await fieldingApi.listLinks(studyId)
      setLinks(res.data.links)
      linksRef.current = res.data.links
      setLoadError(null)
    } catch (err) {
      setLinks(null)
      setLoadError(errorMessage(err) || 'Could not load fielding links. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [studyId])

  // Questionnaire drives both the create gate and quota qid options.
  const loadQuestionnaire = useCallback(async () => {
    if (!studyId) return
    try {
      const status = await questionnaireApi.status(studyId)
      setCanCreate(status.data.status === 'approved')
    } catch {
      setCanCreate(false)
    }
    try {
      const q = await questionnaireApi.questions(studyId)
      setQidOptions(q.data.questions.map((x) => x.qid))
    } catch {
      /* no questions yet — quota editor still works with a free-typed qid */
    }
  }, [studyId])

  useEffect(() => {
    load()
    loadQuestionnaire()
  }, [load, loadQuestionnaire])

  // Poll while any link is open (mirrors QuestionnairePage's interval pattern).
  useEffect(() => {
    if (!links || !links.some((l) => l.status === 'open')) return
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [links, load])

  if (loading) {
    return (
      <div>
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900">Field monitor</h1>
          <p className="text-sm text-slate-500 mt-1">Loading fielding links…</p>
        </div>
        <CardListSkeleton count={2} />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-6">
        <Link to={`/projects/${studyId}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to project
        </Link>
        <h1 className="text-2xl font-semibold text-slate-900 mt-1">Field monitor</h1>
        <p className="text-sm text-slate-500 mt-1">
          Create fielding links, watch responses live, manage quotas, and materialize completes into analysis.
        </p>
      </div>

      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4 max-w-xl mb-6">
          <p className="text-sm font-medium text-red-700 mb-1">Couldn&apos;t load fielding links</p>
          <p className="text-sm text-red-600">{loadError}</p>
          <button
            onClick={() => {
              setLoading(true)
              load()
            }}
            className="mt-3 text-sm font-medium text-red-700 underline"
          >
            Retry
          </button>
        </div>
      )}

      <CreatePanel studyId={studyId!} canCreate={canCreate} canWrite={canWrite} onCreated={load} />

      {links && links.length === 0 && !loadError && (
        <p className="text-sm text-slate-400">No fielding links yet.</p>
      )}

      <div className="space-y-4">
        {(links ?? []).map((link) => (
          <LinkCard
            key={link.id}
            studyId={studyId!}
            link={link}
            qidOptions={qidOptions}
            canWrite={canWrite}
            onChanged={load}
          />
        ))}
      </div>
    </div>
  )
}
