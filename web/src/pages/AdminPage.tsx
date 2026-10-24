import { useSearchParams } from 'react-router-dom'
import ActivityPage from './ActivityPage'
import WordingPage from './WordingPage'
import { useEffect, useState } from 'react'
import { authApi, User } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ShieldIcon } from '../components/Icons'
import PageHeader from '../components/ui/PageHeader'
import { CardHeader } from '../components/ui/Card'
import { buttonClasses } from '../components/ui/Button'

const ROLES = [
  { role: 'Owner', desc: 'Full access: projects, delivery sign-off, user management, archive' },
  { role: 'Analyst', desc: 'Create projects, run tables, approve gates, create/revoke share links' },
  { role: 'Reviewer', desc: 'View-only access to tables and reports; can approve QC gates' },
]

function TeamAdmin() {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteName, setInviteName] = useState('')
  const [inviteRole, setInviteRole] = useState('analyst')
  const [invitePassword, setInvitePassword] = useState('')
  const [inviteError, setInviteError] = useState('')
  const [inviteSuccess, setInviteSuccess] = useState('')

  useEffect(() => {
    const loadUsers = async () => {
      try {
        if (currentUser?.role === 'owner') {
          const res = await authApi.listUsers()
          setUsers(res.data)
        }
      } finally {
        setLoading(false)
      }
    }

    loadUsers()
  }, [currentUser])

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setInviteError('')
    setInviteSuccess('')

    try {
      await authApi.createUser(inviteEmail, inviteName, inviteRole, invitePassword)
      setInviteSuccess('User created successfully!')
      setInviteEmail('')
      setInviteName('')
      setInviteRole('analyst')
      setInvitePassword('')
      setShowInviteForm(false)

      // Reload users
      const res = await authApi.listUsers()
      setUsers(res.data)
    } catch (err: any) {
      setInviteError(err.response?.data?.detail || 'Failed to create user')
    }
  }

  if (currentUser?.role !== 'owner') {
    return (
      <div className="max-w-4xl">
        <PageHeader title="Admin" description="Organization, users, and access control" icon={ShieldIcon} />
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-700">Only Owners can access the admin panel.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl">
      <PageHeader title="Admin" description="Organization, users, and access control" icon={ShieldIcon} />

      <div className="bg-white rounded-lg border border-slate-200 shadow-panel mb-6">
        <CardHeader
          title="Users"
          actions={
            <button onClick={() => setShowInviteForm(!showInviteForm)} className={buttonClasses('secondary')}>
              {showInviteForm ? 'Cancel' : 'Invite user'}
            </button>
          }
        />

        {/* Invite form */}
        {showInviteForm && (
          <form onSubmit={handleCreateUser} className="p-5 border-b border-slate-100 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <input
                type="text"
                placeholder="Full name"
                value={inviteName}
                onChange={(e) => setInviteName(e.target.value)}
                required
                className="px-3 py-2 rounded-md border border-slate-300 text-sm"
              />
              <input
                type="email"
                placeholder="Email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
                className="px-3 py-2 rounded-md border border-slate-300 text-sm"
              />
              <input
                type="password"
                placeholder="Temporary password"
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                required
                className="px-3 py-2 rounded-md border border-slate-300 text-sm"
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="px-3 py-2 rounded-md border border-slate-300 text-sm"
              >
                <option value="analyst">Analyst</option>
                <option value="reviewer">Reviewer</option>
                <option value="owner">Owner</option>
              </select>
            </div>

            {inviteError && <p className="text-sm text-red-600">{inviteError}</p>}
            {inviteSuccess && <p className="text-sm text-emerald-600">{inviteSuccess}</p>}

            <button type="submit" className={buttonClasses('primary')}>
              Create user
            </button>
          </form>
        )}

        {/* Users table */}
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-5 py-2.5 font-medium">Name</th>
              <th className="px-5 py-2.5 font-medium">Email</th>
              <th className="px-5 py-2.5 font-medium">Role</th>
              <th className="px-5 py-2.5 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <tr key={i} className="animate-pulse">
                  <td className="px-5 py-3.5"><div className="h-3 w-28 rounded bg-slate-200/70" /></td>
                  <td className="px-5 py-3.5"><div className="h-3 w-36 rounded bg-slate-200/70" /></td>
                  <td className="px-5 py-3.5"><div className="h-3 w-16 rounded bg-slate-200/70" /></td>
                  <td className="px-5 py-3.5"><div className="h-3 w-14 rounded bg-slate-200/70" /></td>
                </tr>
              ))
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-5 py-8 text-center text-sm text-slate-500">
                  No users yet
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.email}>
                  <td className="px-5 py-3 font-medium text-slate-900">{u.name}</td>
                  <td className="px-5 py-3 text-slate-600">{u.email}</td>
                  <td className="px-5 py-3">
                    <span className="px-2 py-0.5 rounded bg-primary-50 text-primary-500 text-xs font-medium capitalize">
                      {u.role}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-1.5 text-emerald-700 text-xs font-medium">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />{' '}
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="bg-white rounded-lg border border-slate-200 shadow-panel">
        <CardHeader title="Roles" />
        <div className="divide-y divide-slate-100">
          {ROLES.map((r) => (
            <div key={r.role} className="px-5 py-3.5 flex gap-6">
              <span className="w-24 shrink-0 text-sm font-medium text-slate-900">{r.role}</span>
              <span className="text-sm text-slate-500">{r.desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}


/** Admin: the team (owner), the app's wording and the activity log (owner, founder). */
export default function AdminPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const tabs = [
    ...(user?.role === 'owner' ? [{ key: 'team', label: 'Team' }] : []),
    { key: 'wording', label: 'Wording' },
    { key: 'activity', label: 'Activity' },
  ]
  const tab = tabs.find((t) => t.key === params.get('tab'))?.key ?? tabs[0].key
  return (
    <div>
      <nav className="mb-6 flex gap-1 border-b border-slate-200" aria-label="Admin sections">
        {tabs.map((t) => (
          <button key={t.key} type="button" onClick={() => setParams({ tab: t.key })}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${tab === t.key
              ? 'border-slate-900 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
            {t.label}
          </button>
        ))}
      </nav>
      {tab === 'team' && <TeamAdmin />}
      {tab === 'wording' && <WordingPage />}
      {tab === 'activity' && <ActivityPage />}
    </div>
  )
}
