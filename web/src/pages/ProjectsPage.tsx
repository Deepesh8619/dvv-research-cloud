import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { errorMessage, Study, studiesApi } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { FolderIcon, PlusIcon, SearchIcon } from '../components/Icons'
import { TableSkeleton } from '../components/Skeleton'
import StatusPill from '../components/StatusPill'
import PageHeader from '../components/ui/PageHeader'
import { Card } from '../components/ui/Card'
import EmptyState from '../components/ui/EmptyState'
import { buttonClasses } from '../components/ui/Button'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'In progress' },
  { key: 'approved_for_delivery', label: 'Awaiting sign-off' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'error', label: 'Failed' },
  { key: 'archived', label: 'Deleted' },
]

export default function ProjectsPage() {
  const [studies, setStudies] = useState<Study[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [params] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') ?? '')

  useEffect(() => {
    setQuery(params.get('q') ?? '')
  }, [params])

  useEffect(() => {
    studiesApi
      .list()
      .then((r) => setStudies(r.data.studies))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const { user } = useAuth()
  const isOwner = user?.role === 'owner'
  const [confirming, setConfirming] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const setStatus = (id: string, status: string) =>
    setStudies((all) => all.map((s) => (s.id === id ? { ...s, status } as Study : s)))
  const deleteProject = async (id: string) => {
    setActionError(null)
    try {
      await studiesApi.archive(id)
      setStatus(id, 'archived')
    } catch (err) {
      setActionError(errorMessage(err) || 'Could not delete this project.')
    } finally {
      setConfirming(null)
    }
  }
  const restoreProject = async (id: string) => {
    setActionError(null)
    try {
      setStatus(id, (await studiesApi.restore(id)).data.status)
    } catch (err) {
      setActionError(errorMessage(err) || 'Could not restore this project.')
    }
  }

  const visible = useMemo(() => {
    let out = studies.filter((s) => (filter === 'archived') === (s.status === 'archived'))
    if (filter === 'active') {
      out = out.filter((s) => !['delivered', 'error', 'archived'].includes(s.status))
    } else if (filter !== 'all') {
      out = out.filter((s) => s.status === filter)
    }
    if (query.trim()) {
      const q = query.trim().toLowerCase()
      out = out.filter(
        (s) => s.client_name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q),
      )
    }
    return out
  }, [studies, filter, query])

  if (loading) {
    return (
      <div>
        <PageHeader title="Projects" description="Loading projects…" icon={FolderIcon} />
        <TableSkeleton rows={6} cols={5} />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Projects"
        description={
          <span className="tabular-nums">
            {visible.length} of {studies.length} projects
          </span>
        }
        icon={FolderIcon}
        actions={
          <Link to="/projects/new" className={buttonClasses('primary')}>
            <PlusIcon className="w-4 h-4" /> New Project
          </Link>
        }
      />

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="relative w-72">
          <SearchIcon className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or ID…"
            className="w-full h-9 pl-9 pr-3 rounded-md bg-white border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
          />
        </div>
        <div className="flex bg-white border border-slate-300 rounded-md p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 ${
                filter === f.key ? 'bg-primary-500 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {actionError && <p role="alert" className="mb-3 text-sm text-red-700">{actionError}</p>}
      <Card padded={false} className="overflow-hidden dvv-rise">
        {visible.length === 0 ? (
          <EmptyState
            icon={FolderIcon}
            title={studies.length === 0 ? 'No projects yet' : 'No projects match'}
            description={
              studies.length === 0
                ? 'Create your first research project to get started.'
                : 'Try a different search term or filter, or create a new project.'
            }
            action={
              <Link to="/projects/new" className={buttonClasses('primary')}>
                <PlusIcon className="w-4 h-4" /> New Project
              </Link>
            }
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-5 py-3 font-medium">Project</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Deliverables</th>
                <th className="px-5 py-3 font-medium">Created</th>
                <th className="px-5 py-3 font-medium">Updated</th>
                {isOwner && <th className="px-5 py-3"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visible.map((s) => (
                <tr key={s.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3.5">
                    <Link to={`/projects/${s.id}`} className="font-medium text-slate-900 hover:text-sky-700">
                      {s.client_name}
                    </Link>
                    <p className="text-xs text-slate-400 mt-0.5 font-mono">{s.id}</p>
                  </td>
                  <td className="px-5 py-3.5">
                    <StatusPill status={s.status} />
                  </td>
                  <td className="px-5 py-3.5 text-slate-600">
                    {s.excel_path ? (
                      <span className="text-xs">Tables · Excel · PowerPoint</span>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-slate-500">
                    {new Date(s.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}
                  </td>
                  <td className="px-5 py-3.5 text-slate-500">
                    {new Date(s.updated_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                  </td>
                  {isOwner && (
                    <td className="px-5 py-3.5 text-right text-xs whitespace-nowrap">
                      {s.status === 'archived' ? (
                        <button type="button" className="font-medium text-sky-700 hover:underline"
                          onClick={() => void restoreProject(s.id)}>Restore</button>
                      ) : confirming === s.id ? (
                        <span className="inline-flex items-center gap-2">
                          <button type="button" className="font-medium text-red-700 hover:underline"
                            onClick={() => void deleteProject(s.id)}>Delete project</button>
                          <button type="button" className="text-slate-500 hover:underline"
                            onClick={() => setConfirming(null)}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" className="text-slate-500 hover:text-red-700 hover:underline"
                          onClick={() => setConfirming(s.id)}>Delete</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
