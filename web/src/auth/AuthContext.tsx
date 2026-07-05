import { createContext, useContext, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi } from '../api/client'

export interface User {
  id: string
  email: string
  name: string
  role: string
  created_at: string
  is_active: boolean
}

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  // Load user on mount
  useEffect(() => {
    const loadUser = async () => {
      const token = localStorage.getItem('authToken')
      if (token) {
        try {
          const res = await authApi.me()
          setUser(res.data)
        } catch (err) {
          // Token invalid/expired
          localStorage.removeItem('authToken')
          setUser(null)
        }
      }
      setLoading(false)
    }

    loadUser()
  }, [])

  const login = async (email: string, password: string) => {
    try {
      const res = await authApi.login(email, password)
      localStorage.setItem('authToken', res.data.access_token)
      setUser(res.data.user)
    } catch (err) {
      throw err
    }
  }

  const logout = () => {
    localStorage.removeItem('authToken')
    setUser(null)
    navigate('/login')
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
