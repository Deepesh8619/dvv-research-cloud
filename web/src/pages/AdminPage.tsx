import { useEffect, useState } from 'react'
import { authApi, User } from '../api/client'
import { useAuth } from '../auth/AuthContext'

const ROLES = [
  { role: 'Owner', desc: 'Full access: projects, delivery sign-off, user management, archive' },
  { role: 'Analyst', desc: 'Create projects, run tables, approve gates, create/revoke share links' },
  { role: 'Reviewer', desc: 'View-only access to tables and reports; can approve QC gates' },
]

export default function AdminPage() {
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
        <h1 className="text-2xl font-semibold text-slate-900 mb-1">Admin</h1>
        <p className="text-sm text-slate-500 mb-8">Organization, users, and access control</p>
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-sm text-amber-700">Only Owners can access the admin panel.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-semibold text-slate-900 mb-1">Admin</h1>
      <p className="text-sm text-slate-500 mb-8">Organization, users, and access control</p>

      <div className="bg-white rounded-lg border border-slate-200 mb-6">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <h2 className="font-semibold text-slate-900">Users</h2>
          <button
            onClick={() => setShowInviteForm(!showInviteForm)}
            className="text-sm font-medium px-3.5 py-1.5 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            {showInviteForm ? 'Cancel' : 'Invite user'}
          </button>
        </div>

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

            <button
              type="submit"
              className="px-3.5 py-1.5 rounded-md bg-primary-500 text-white text-sm font-medium hover:bg-primary-600"
            >
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
              <tr>
                <td colSpan={4} className="px-5 py-4 text-center text-slate-500">
                  Loading...
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-5 py-4 text-center text-slate-500">
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

      <div className="bg-white rounded-lg border border-slate-200">
        <div className="px-5 py-4 border-b border-slate-100">
          <h2 className="font-semibold text-slate-900">Roles</h2>
        </div>
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
