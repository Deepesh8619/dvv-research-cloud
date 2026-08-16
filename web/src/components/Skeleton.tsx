// Layout-shaped loading placeholders. Plain Tailwind animate-pulse — no library.
// Use these where the loaded layout is known; keep LoadingSpinner for unknown-shape waits.

function Bar({ className = '' }: { className?: string }) {
  return <div className={`rounded bg-slate-200/70 ${className}`} />
}

/** N rows of a data table (project list, questionnaire list). */
export function TableSkeleton({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden animate-pulse">
      <div className="bg-slate-50 border-b border-slate-200 px-5 py-3 flex gap-6">
        {Array.from({ length: cols }).map((_, i) => (
          <Bar key={i} className="h-3 flex-1" />
        ))}
      </div>
      <div className="divide-y divide-slate-100">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="px-5 py-4 flex items-center gap-6">
            {Array.from({ length: cols }).map((_, c) => (
              <Bar key={c} className={`h-3 flex-1 ${c === 0 ? 'max-w-[40%]' : 'max-w-[70%]'}`} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/** KPI card row + recent-table for the dashboard. */
export function DashboardSkeleton() {
  return (
    <div className="animate-pulse">
      <div className="grid grid-cols-4 gap-4 mb-8">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white rounded-lg border border-slate-200 p-5 flex items-center gap-4">
            <Bar className="w-10 h-10 shrink-0" />
            <div className="flex-1 space-y-2">
              <Bar className="h-5 w-10" />
              <Bar className="h-3 w-20" />
            </div>
          </div>
        ))}
      </div>
      <TableSkeleton rows={4} cols={3} />
    </div>
  )
}

/** Fielding link cards. */
export function CardListSkeleton({ count = 2 }: { count?: number }) {
  return (
    <div className="space-y-4 animate-pulse">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="bg-white rounded-lg border border-slate-200 p-5 space-y-4">
          <div className="flex items-center gap-2">
            <Bar className="h-5 w-16 rounded-full" />
            <Bar className="h-3 w-24" />
          </div>
          <Bar className="h-8 w-full" />
          <div className="flex gap-2">
            {Array.from({ length: 5 }).map((_, p) => (
              <Bar key={p} className="h-12 w-[70px]" />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
