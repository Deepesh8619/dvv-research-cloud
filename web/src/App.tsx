import './styles/globals.css'

import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import Shell from './components/Shell'
import RequireAuth from './components/RequireAuth'
import AdminPage from './pages/AdminPage'
import CodingPage from './pages/CodingPage'
import DashboardPage from './pages/DashboardPage'
import LoginPage from './pages/LoginPage'
import NewProjectPage from './pages/NewProjectPage'
import ProjectPage from './pages/ProjectPage'
import ProjectsPage from './pages/ProjectsPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SharePage from './pages/SharePage'

function LegacyStudyRedirect() {
  const { id } = useParams()
  return <Navigate to={`/projects/${id}`} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public routes (no auth required) */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/share/:token" element={<SharePage />} />

          {/* Protected routes (auth required) */}
          <Route element={<RequireAuth><Shell /></RequireAuth>}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/new" element={<NewProjectPage />} />
            <Route path="/projects/:id" element={<ProjectPage />} />
            <Route path="/coding" element={<CodingPage />} />
            <Route path="/admin" element={<AdminPage />} />
            {/* Legacy routes */}
            <Route path="/upload" element={<Navigate to="/projects/new" replace />} />
            <Route path="/studies" element={<Navigate to="/projects" replace />} />
            <Route path="/studies/:id" element={<LegacyStudyRedirect />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
