import './styles/globals.css'

import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { LabelProvider } from './labels/LabelContext'
import RequireAuth from './components/RequireAuth'
import RequireRole from './components/RequireRole'
import { FOUNDER_ROLES, WRITE_ROLES } from './auth/roles'
import { Suspense, lazy } from 'react'
import LoadingSpinner from './components/LoadingSpinner'

// Each page loads when it is opened. A respondent opening a survey link on a
// phone downloads the survey and nothing else — not the analyst app (2.5 MB
// in one file before this).
const Shell = lazy(() => import('./components/Shell'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const FieldingPage = lazy(() => import('./pages/FieldingPage'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const NewProjectPage = lazy(() => import('./pages/NewProjectPage'))
const ProjectPage = lazy(() => import('./pages/ProjectPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))
const QuestionnairePage = lazy(() => import('./pages/QuestionnairePage'))
const RespondentPage = lazy(() => import('./pages/RespondentPage'))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'))
const SurveyBuilderPage = lazy(() => import('./pages/SurveyBuilderPage'))
const SharePage = lazy(() => import('./pages/SharePage'))

function LegacyStudyRedirect() {
  const { id } = useParams()
  return <Navigate to={`/projects/${id}`} replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LabelProvider>
        <Suspense fallback={<LoadingSpinner />}>
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
            <Route
              path="/admin"
              element={
                <RequireRole roles={FOUNDER_ROLES}>
                  <AdminPage />
                </RequireRole>
              }
            />
            {/* Moved: coding is each project's Open ends tab; wording and activity are Admin tabs. */}
            <Route path="/coding" element={<Navigate to="/projects" replace />} />
            <Route path="/wording" element={<Navigate to="/admin?tab=wording" replace />} />
            <Route path="/activity" element={<Navigate to="/admin?tab=activity" replace />} />
            {/* Legacy routes */}
            <Route path="/upload" element={<Navigate to="/projects/new" replace />} />
            <Route path="/studies" element={<Navigate to="/projects" replace />} />
            <Route path="/studies/:id" element={<LegacyStudyRedirect />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
        </Suspense>
        </LabelProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
