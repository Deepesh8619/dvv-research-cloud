import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Study, studiesApi } from '../api/client'
import { ClockIcon, DocChartIcon, FolderIcon, PlusIcon } from '../components/Icons'
import LoadingSpinner from '../components/LoadingSpinner'
import StatusPill from '../components/StatusPill'

export default function DashboardPage() {
  const [studies, setStudies] = useState<Study[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    studiesApi
      .list()
      .then((r) => setStudies(r.data.studies))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner />

  const active = studies.filter((s) => !['delivered', 'archived', 'error'].includes(s.status))
  const awaiting = studies.filter((s) => s.status === 'approved_for_delivery')
  const delivered = studies.filter((s) => s.status === 'delivered')
  const recent = studies.slice(0, 6)

  const kpis = [
    { label: 'Total projects', value: studies.length, icon: FolderIcon, tint: 'text-primary-500 bg-primary-50' },
    { label: 'In progress', value: active.length, icon: ClockIcon, tint: 'text-amber-600 bg-amber-50' },
    { label: 'Awaiting sign-off', value: awaiting.length, icon: DocChartIcon, tint: 'text-violet-600 bg-violet-50' },
    { label: 'Delivered', value: delivered.length, icon: DocChartIcon, tint: 'text-emerald-600 bg-emerald-50' },
  ]

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
          <p className="text-sm text-slate-500 mt-1">Overview of your research projects</p>
        </div>
        <Link
          to="/projects/new"
          className="inline-flex items-center gap-2 bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 transition-colors"
        >
          <PlusIcon className="w-4 h-4" /> New Project
        </Link>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {kpis.map(({ label, value, icon: Icon, tint }) => (
          <div key={label} className="bg-white rounded-lg border border-slate-200 p-5 flex items-center gap-4">
            <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${tint}`}>
              <Icon className="w-5 h-5" />
            </div>
            <div>
              <p className="text-2xl font-semibold text-slate-900 leading-none">{value}</p>
              <p className="text-xs text-slate-500 mt-1.5">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Recent projects */}
      <div className="bg-white rounded-lg border border-slate-200">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <h2 className="font-semibold text-slate-900">Recent projects</h2>
          <Link to="/projects" className="text-sm text-sky-600 hover:text-sky-700 font-medium">
            View all
          </Link>
        </div>
        {recent.length === 0 ? (
          <div className="p-10 text-center">
            <p className="text-slate-500 mb-3">No projects yet.</p>
            <Link to="/projects/new" className="text-sky-600 hover:text-sky-700 text-sm font-medium">
              Create your first project
            </Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
                <th className="px-5 py-2.5 font-medium">Project</th>
                <th className="px-5 py-2.5 font-medium">Status</th>
                <th className="px-5 py-2.5 font-medium">Updated</th>
                <th className="px-5 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {recent.map((s) => (
                <tr key={s.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3">
                    <Link to={`/projects/${s.id}`} className="font-medium text-slate-900 hover:text-sky-700">
                      {s.client_name}
                    </Link>
                    <p className="text-xs text-slate-400 mt-0.5">{s.id}</p>
                  </td>
                  <td className="px-5 py-3">
                    <StatusPill status={s.status} />
                  </td>
                  <td className="px-5 py-3 text-slate-500">
                    {new Date(s.updated_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Link to={`/projects/${s.id}`} className="text-sky-600 hover:text-sky-700 font-medium">
                      Open
                    </Link>
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
