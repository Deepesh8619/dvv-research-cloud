import './styles/globals.css'

import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { LabelProvider } from './labels/LabelContext'
import Shell from './components/Shell'
import WordingPage from './pages/WordingPage'
import ActivityPage from './pages/ActivityPage'
import RequireAuth from './components/RequireAuth'
import RequireRole from './components/RequireRole'
import { FOUNDER_ROLES, WRITE_ROLES } from './auth/roles'
import AdminPage from './pages/AdminPage'
import CodingPage from './pages/CodingPage'
import DashboardPage from './pages/DashboardPage'
import FieldingPage from './pages/FieldingPage'
import LoginPage from './pages/LoginPage'
import NewProjectPage from './pages/NewProjectPage'
import ProjectPage from './pages/ProjectPage'
import ProjectsPage from './pages/ProjectsPage'
import QuestionnairePage from './pages/QuestionnairePage'
import RespondentPage from './pages/RespondentPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SurveyBuilderPage from './pages/SurveyBuilderPage'
import SharePage from './pages/SharePage'

function LegacyStudyRedirect() {
  const { id } = useParams()
  return <Navigate to={`/projects/${id}`} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LabelProvider>
        <Routes>
          {/* Public routes (no auth required) */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/share/:token" element={<SharePage />} />
          <Route path="/r/:token" element={<RespondentPage />} />

          {/* Protected routes (auth required) */}
          <Route element={<RequireAuth><Shell /></RequireAuth>}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route
              path="/projects/new"
              element={
                <RequireRole roles={WRITE_ROLES}>
                  <NewProjectPage />
                </RequireRole>
              }
            />
            <Route path="/projects/:id" element={<ProjectPage />} />
            <Route path="/projects/:id/questionnaire" element={<QuestionnairePage />} />
            <Route path="/projects/:id/builder" element={<SurveyBuilderPage />} />
            <Route path="/projects/:id/fielding" element={<FieldingPage />} />
            <Route path="/coding" element={<CodingPage />} />
            <Route
              path="/admin"
              element={
                <RequireRole roles={['owner']}>
                  <AdminPage />
                </RequireRole>
              }
            />
            <Route
              path="/wording"
              element={
                <RequireRole roles={FOUNDER_ROLES}>
                  <WordingPage />
                </RequireRole>
              }
            />
            <Route
              path="/activity"
              element={
                <RequireRole roles={FOUNDER_ROLES}>
                  <ActivityPage />
                </RequireRole>
              }
            />
            {/* Legacy routes */}
            <Route path="/upload" element={<Navigate to="/projects/new" replace />} />
            <Route path="/studies" element={<Navigate to="/projects" replace />} />
            <Route path="/studies/:id" element={<LegacyStudyRedirect />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
        </LabelProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
