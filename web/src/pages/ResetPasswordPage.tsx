import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authApi } from '../api/client'
import { DocChartIcon } from '../components/Icons'

// Emergency admin recovery — see api/routes/auth.py:emergency_reset and
// ADMIN_RESET_TOKEN in api/config.py. Deliberately unauthenticated (that's
// the point: recovering a locked-out owner account), gated by a token you
// set yourself in the hosting environment rather than a session. The
// endpoint 404s if ADMIN_RESET_TOKEN isn't set, so this form only works
// once that's deliberately configured.
export default function ResetPasswordPage() {
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await authApi.emergencyReset(email, token, newPassword)
      setSuccess(true)
    } catch (err: any) {
      const detail = err.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Reset failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2.5 mb-6">
            <div className="w-10 h-10 rounded bg-primary-500 flex items-center justify-center">
              <DocChartIcon className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-xl font-semibold text-slate-900">DVV Research Cloud</h1>
          </div>
          <p className="text-sm text-slate-600">Reset the admin password</p>
        </div>

        <div className="bg-white rounded-lg border border-slate-200 shadow-sm p-6">
          {success ? (
            <div className="space-y-4">
              <div className="rounded-md bg-emerald-50 border border-emerald-200 p-3">
                <p className="text-sm text-emerald-700">
                  Password reset. You can log in with the new password now.
                </p>
              </div>
              <button
                onClick={() => navigate('/login')}
                className="w-full py-2.5 px-3.5 rounded-md bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 transition-colors"
              >
                Back to login
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <p className="text-xs text-slate-500">
                Requires the admin reset token set as <code>ADMIN_RESET_TOKEN</code> in the
                backend's environment. Only the configured admin account (
                <code>ADMIN_EMAIL</code>) can be reset this way.
              </p>

              <div>
                <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1.5">
                  Admin email
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-md border border-slate-300 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  placeholder="you@company.com"
                />
              </div>

              <div>
                <label htmlFor="token" className="block text-sm font-medium text-slate-700 mb-1.5">
                  Reset token
                </label>
                <input
                  id="token"
                  type="password"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-md border border-slate-300 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  placeholder="ADMIN_RESET_TOKEN value"
                />
              </div>

              <div>
                <label htmlFor="new-password" className="block text-sm font-medium text-slate-700 mb-1.5">
                  New password
                </label>
                <input
                  id="new-password"
                  type="password"
                  required
                  minLength={8}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-md border border-slate-300 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  placeholder="••••••••"
                />
              </div>

              {error && (
                <div className="rounded-md bg-red-50 border border-red-200 p-3">
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 px-3.5 rounded-md bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? 'Resetting...' : 'Reset password'}
              </button>

              <p className="text-center text-sm text-slate-500">
                <Link to="/login" className="text-primary-600 hover:text-primary-700 font-medium">
                  Back to login
                </Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
