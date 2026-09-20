import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Study, studiesApi } from '../api/client'
import { ClockIcon, DocChartIcon, FolderIcon, GridIcon, PlusIcon } from '../components/Icons'
import { DashboardSkeleton } from '../components/Skeleton'
import StatusPill from '../components/StatusPill'
import { useLabel } from '../labels/LabelContext'
import PageHeader from '../components/ui/PageHeader'
import { Card, CardHeader } from '../components/ui/Card'
import StatTile from '../components/ui/StatTile'
import EmptyState from '../components/ui/EmptyState'
import { buttonClasses } from '../components/ui/Button'

export default function DashboardPage() {
  const tDashboard = useLabel('dashboard.title', 'Dashboard')
  const tSubtitle = useLabel('dashboard.subtitle', 'Overview of your research projects')
  const tTotal = useLabel('dashboard.total', 'Total projects')
  const tInProgress = useLabel('dashboard.inProgress', 'In progress')
  const tAwaiting = useLabel('dashboard.awaiting', 'Awaiting sign-off')
  const tDelivered = useLabel('dashboard.delivered', 'Delivered')
  const [studies, setStudies] = useState<Study[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    studiesApi
      .list()
      .then((r) => setStudies(r.data.studies))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div>
        <PageHeader title={tDashboard} description="Loading overview…" icon={GridIcon} />
        <DashboardSkeleton />
      </div>
    )
  }

  const active = studies.filter((s) => !['delivered', 'archived', 'error'].includes(s.status))
  const awaiting = studies.filter((s) => s.status === 'approved_for_delivery')
  const delivered = studies.filter((s) => s.status === 'delivered')
  const recent = studies.slice(0, 6)

  const kpis = [
    { label: tTotal, value: studies.length, icon: FolderIcon, tint: 'text-primary-500 bg-primary-50' },
    { label: tInProgress, value: active.length, icon: ClockIcon, tint: 'text-amber-600 bg-amber-50' },
    { label: tAwaiting, value: awaiting.length, icon: DocChartIcon, tint: 'text-violet-600 bg-violet-50' },
    { label: tDelivered, value: delivered.length, icon: DocChartIcon, tint: 'text-emerald-600 bg-emerald-50' },
  ]

  return (
    <div>
      <PageHeader
        title={tDashboard}
        description={tSubtitle}
        icon={GridIcon}
        actions={
          <Link to="/projects/new" className={buttonClasses('primary')}>
            <PlusIcon className="w-4 h-4" /> New Project
          </Link>
        }
      />

      {/* KPI cards */}
      <div className="dvv-cascade mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {kpis.map(({ label, value, icon: Icon, tint }) => (
          <StatTile key={label} label={label} value={value} icon={Icon} tint={tint} />
        ))}
      </div>

      {/* Recent projects */}
      <Card padded={false}>
        <CardHeader
          title="Recent projects"
          actions={
            <Link to="/projects" className="text-sm font-medium text-sky-600 hover:text-sky-700">
              View all
            </Link>
          }
        />
        {recent.length === 0 ? (
          <EmptyState
            icon={FolderIcon}
            title="No projects yet"
            description="Create your first research project to start building surveys and delivering results."
            action={
              <Link to="/projects/new" className={buttonClasses('primary')}>
                <PlusIcon className="w-4 h-4" /> New Project
              </Link>
            }
          />
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
      </Card>
    </div>
  )
}
