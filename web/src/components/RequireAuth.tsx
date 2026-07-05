import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from './LoadingSpinner'

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return <LoadingSpinner />
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
