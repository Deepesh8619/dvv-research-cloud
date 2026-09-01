import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/**
 * Keep a route out of the hands of a role that cannot use it.
 *
 * Hiding the nav link is presentation only — anyone can type the URL, and
 * before this they landed on a rendered page whose first API call returned 403,
 * which reads as "the tool is broken" rather than "this is not yours". The
 * server is still the access control: every endpoint behind these pages checks
 * the role itself and refuses regardless of what the browser did.
 */
export default function RequireRole({
  roles,
  children,
}: {
  roles: readonly string[]
  children: JSX.Element
}) {
  const { user } = useAuth()
  if (!user?.role || !roles.includes(user.role)) {
    return <Navigate to="/dashboard" replace />
  }
  return children
}
