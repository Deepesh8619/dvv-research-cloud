import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Study, studiesApi } from '../api/client'
import { PlusIcon, SearchIcon } from '../components/Icons'
import LoadingSpinner from '../components/LoadingSpinner'
import StatusPill from '../components/StatusPill'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'In progress' },
  { key: 'approved_for_delivery', label: 'Awaiting sign-off' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'error', label: 'Failed' },
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

  const visible = useMemo(() => {
    let out = studies.filter((s) => s.status !== 'archived')
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

  if (loading) return <LoadingSpinner />

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Projects</h1>
          <p className="text-sm text-slate-500 mt-1">
            {visible.length} of {studies.length} projects
          </p>
        </div>
        <Link
          to="/projects/new"
          className="inline-flex items-center gap-2 bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 transition-colors"
        >
          <PlusIcon className="w-4 h-4" /> New Project
        </Link>
      </div>

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
              className={`px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                filter === f.key ? 'bg-primary-500 text-white' : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        {visible.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            No projects match. <Link to="/projects/new" className="text-sky-600 font-medium">Create one</Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-5 py-3 font-medium">Project</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium">Deliverables</th>
                <th className="px-5 py-3 font-medium">Created</th>
                <th className="px-5 py-3 font-medium">Updated</th>
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
