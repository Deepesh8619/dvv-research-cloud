import { useState } from 'react'
import { Link, NavLink, useNavigate, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import CopilotDrawer from './CopilotDrawer'
import { FOUNDER_ROLES, WRITE_ROLES } from '../auth/roles'
import { ChatIcon, DocChartIcon, FolderIcon, GridIcon, LogOutIcon, MenuIcon, PlusIcon, SearchIcon, ShieldIcon } from './Icons'

// `roles` restricts an entry to those roles; omitted means everyone signed in.
// Mirrors the SERVER-side gate — hiding a link is presentation, not access
// control, and the endpoints refuse regardless.
// `roles` restricts an entry; omitted means everyone signed in.
//
// `restrictedTo` is the caption shown beside a restricted entry, so someone who
// CAN see it knows the rest of the team cannot — the question "who else sees
// this?" was previously unanswerable from the UI.
//
// Admin and New Project were missing their `roles` entirely: every signed-in
// user saw them and every viewer who clicked landed on a page whose first API
// call returns 403. Hiding a link is presentation, not access control — the
// endpoints refuse regardless — but advertising a door that is locked is its
// own bug.
const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: GridIcon },
  { to: '/projects', label: 'Projects', icon: FolderIcon },
  {
    to: '/projects/new',
    label: 'New Project',
    icon: PlusIcon,
    roles: WRITE_ROLES,
    restrictedTo: 'Owner, analyst, founder',
  },
  { to: '/coding', label: 'Coding', icon: ChatIcon },
  {
    to: '/admin',
    label: 'Admin',
    icon: ShieldIcon,
    roles: ['owner'],
    restrictedTo: 'Owner only',
  },
  // Founders' wording console — same roles as the correction loop.
  {
    to: '/wording',
    label: 'Wording',
    icon: ChatIcon,
    roles: FOUNDER_ROLES,
    restrictedTo: 'Owner, founder',
  },
  {
    to: '/activity',
    label: 'Activity',
    icon: DocChartIcon,
    roles: FOUNDER_ROLES,
    restrictedTo: 'Owner, founder',
  },
]

export default function Shell() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const [query, setQuery] = useState('')
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [showCopilot, setShowCopilot] = useState(false)
  const [showNav, setShowNav] = useState(false)  // the drawer, below md only

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault()
    navigate(`/projects?q=${encodeURIComponent(query)}`)
  }

  const handleLogout = () => {
    logout()
  }

  const userInitials = user?.name
    .split(' ')
    .map((part) => part[0])
    .join('')
    .toUpperCase() || 'U'

  return (
    <div className="min-h-screen bg-slate-100">
      {/* Top bar */}
      <header className="fixed top-0 inset-x-0 h-14 bg-primary-500 z-40 flex items-center px-4 gap-3 sm:gap-6 shadow">
        <button
          type="button"
          onClick={() => setShowNav((v) => !v)}
          aria-label={showNav ? 'Close menu' : 'Open menu'}
          aria-expanded={showNav}
          className="md:hidden -ml-2 h-10 w-10 shrink-0 flex items-center justify-center rounded-md text-white hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
        >
          <MenuIcon className="w-5 h-5" open={showNav} />
        </button>
        <Link to="/dashboard" className="flex items-center gap-2.5 shrink-0">
          <div className="w-8 h-8 rounded bg-white/10 flex items-center justify-center">
            <DocChartIcon className="w-5 h-5 text-white" />
          </div>
          <div className="leading-tight">
            <span className="hidden sm:inline text-white font-semibold text-[15px] tracking-tight">
              DVV Research Cloud
            </span>
            <span className="sm:hidden text-white font-semibold text-[15px] tracking-tight">DVV</span>
          </div>
        </Link>

        <form onSubmit={onSearch} className="flex-1 max-w-xl">
          <div className="relative">
            <SearchIcon className="w-4 h-4 text-white/50 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search projects…"
              className="w-full h-9 pl-9 pr-3 rounded-md bg-white/10 text-white placeholder-white/50 text-sm border border-white/10 focus:bg-white focus:text-slate-900 focus:placeholder-slate-400 focus:outline-none transition-colors"
            />
          </div>
        </form>

        <div className="flex items-center gap-2 sm:gap-4 ml-auto shrink-0">
          <button
            type="button"
            onClick={() => setShowCopilot((v) => !v)}
            aria-pressed={showCopilot}
            className="h-8 px-3 rounded-md text-sm font-medium text-white border border-white/25 hover:bg-white/10 transition-colors"
          >
            Copilot
          </button>
          <span className="hidden sm:inline text-[11px] font-medium text-white/70 border border-white/20 rounded px-2 py-0.5 uppercase tracking-wider">
            MVP
          </span>
          <div className="relative">
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="w-8 h-8 rounded-full bg-sky-600 text-white text-sm font-semibold flex items-center justify-center hover:bg-sky-700 transition-colors"
              title={user?.name}
            >
              {userInitials}
            </button>

            {/* User menu */}
            {showUserMenu && (
              <div className="absolute right-0 top-full mt-2 w-52 rounded-lg bg-white border border-slate-200 shadow-lg ring-1 ring-slate-900/5 z-50 py-1.5">
                <div className="px-3 py-2 border-b border-slate-100">
                  <p className="text-sm font-medium text-slate-900">{user?.name}</p>
                  <p className="text-xs text-slate-500">{user?.email}</p>
                  <p className="text-xs text-slate-400 mt-1 capitalize">{user?.role}</p>
                </div>
                <button
                  onClick={handleLogout}
                  className="w-full flex items-center gap-2.5 text-left px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 transition-colors focus-visible:outline-none focus-visible:bg-slate-50"
                >
                  <LogOutIcon className="w-4 h-4 text-slate-400" />
                  Log out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Left rail — a fixed column from md up, a drawer below it. A 224px
          column on a 375px screen leaves no room for the page it navigates. */}
      {showNav && (
        <button
          type="button"
          aria-label="Close menu"
          onClick={() => setShowNav(false)}
          className="md:hidden fixed inset-0 top-14 z-30 bg-slate-900/40"
        />
      )}
      <aside
        className={`fixed left-0 top-14 bottom-0 w-56 bg-white border-r border-slate-200 z-30 transition-transform duration-200 ease-out md:translate-x-0 ${
          showNav ? 'translate-x-0 shadow-xl' : '-translate-x-full'
        }`}
      >
        <nav className="p-3 space-y-0.5">
          {NAV.filter(
            ({ roles }) =>
              !roles || (user?.role ? (roles as readonly string[]).includes(user.role) : false),
          ).map(({ to, label, icon: Icon, restrictedTo }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/projects'}
              onClick={() => setShowNav(false)}
              className={({ isActive }) =>
                `relative flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 ${
                  isActive
                    ? "bg-primary-50 text-primary-500 before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-1 before:rounded-r-full before:bg-primary-500 before:content-['']"
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`
              }
            >
              <Icon className="w-[18px] h-[18px] shrink-0" />
              <span className="flex-1 min-w-0">
                {label}
                {restrictedTo && (
                  <span className="block text-[10px] font-normal text-slate-400 leading-tight">
                    {restrictedTo}
                  </span>
                )}
              </span>
            </NavLink>
          ))}
        </nav>
        <div className="absolute bottom-0 inset-x-0 p-4 border-t border-slate-100">
          <p className="text-[11px] text-slate-400">DVV Research Cloud v0.2</p>
          <p className="text-[11px] text-slate-400">Data resident: local</p>
        </div>
      </aside>

      <CopilotDrawer open={showCopilot} onClose={() => setShowCopilot(false)} />

      {/* Content */}
      <main className="md:pl-56 pt-14">
        <div className="p-4 sm:p-6 md:p-8 max-w-[1400px]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
