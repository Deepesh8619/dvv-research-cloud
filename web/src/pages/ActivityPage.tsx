import { useEffect, useState } from 'react'
import { activityApi, type ActivityEntry, type AuditEntry } from '../api/client'
import { CardListSkeleton } from '../components/Skeleton'
import { DocChartIcon } from '../components/Icons'
import PageHeader from '../components/ui/PageHeader'

/**
 * The founders' activity log.
 *
 * Two deliberately separate questions, because mixing them makes both useless:
 *
 *   Changes  — what actually happened, and who did it. The answer to "who
 *              approved the rule that broke extraction, and when", which only
 *              ever gets asked after something has gone wrong.
 *   Looked for — what they SEARCHED and did not necessarily find. A search is
 *              intent: "searched 'terminate' four times, changed nothing" says
 *              the wording they need does not exist yet, and no record of
 *              completed changes would ever reveal that.
 */

const ACTION_STYLE: Record<string, string> = {
  created: 'text-sky-700 bg-sky-50 border-sky-200',
  applied: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  approved: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  set: 'text-sky-700 bg-sky-50 border-sky-200',
  reverted: 'text-amber-700 bg-amber-50 border-amber-200',
  disabled: 'text-amber-700 bg-amber-50 border-amber-200',
  enabled: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  revised: 'text-amber-700 bg-amber-50 border-amber-200',
  reordered: 'text-slate-600 bg-slate-50 border-slate-200',
  searched: 'text-slate-600 bg-slate-50 border-slate-200',
}

function when(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function Pill({ action }: { action: string }) {
  return (
    <span
      className={`shrink-0 text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5
                  rounded border ${ACTION_STYLE[action] ?? 'text-slate-600 bg-slate-50 border-slate-200'}`}
    >
      {action}
    </span>
  )
}

export default function ActivityPage() {
  const [tab, setTab] = useState<'changes' | 'searches'>('changes')
  const [audit, setAudit] = useState<AuditEntry[] | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[] | null>(null)
  const [repeated, setRepeated] = useState<{ query: string; times: number }[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    activityApi
      .audit()
      .then((r) => setAudit(r.data.entries))
      .catch(() => setError('Could not load the change log.'))
    activityApi
      .list()
      .then((r) => {
        setActivity(r.data.entries)
        setRepeated(r.data.repeated_searches)
      })
      .catch(() => undefined)
  }, [])

  const tabClass = (active: boolean) =>
    `text-sm px-3 py-1.5 rounded border transition
     focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 ${
       active
         ? 'bg-slate-900 text-white border-slate-900'
         : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
     }`

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Activity"
        description="Everything that changed, and everything that was looked for. Kept apart on purpose — one answers what happened, the other what someone was trying to do."
        icon={DocChartIcon}
      />

      <div className="flex gap-2 mb-4">
        <button className={tabClass(tab === 'changes')} onClick={() => setTab('changes')}>
          Changes {audit ? `(${audit.length})` : ''}
        </button>
        <button className={tabClass(tab === 'searches')} onClick={() => setTab('searches')}>
          Looked for {activity ? `(${activity.length})` : ''}
        </button>
      </div>

      {error && (
        <p className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-3 mb-4">
          {error}
        </p>
      )}

      {tab === 'changes' &&
        (audit === null ? (
          <CardListSkeleton />
        ) : audit.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing has been changed yet.</p>
        ) : (
          <div className="border-t border-slate-200">
            {audit.map((e) => (
              <div key={e.id} className="border-b border-slate-200 py-2.5 flex gap-3 items-baseline">
                <span className="text-xs text-slate-400 font-mono tabular-nums shrink-0 w-28">
                  {when(e.at)}
                </span>
                <Pill action={e.action} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-800 break-words">{e.summary || e.entity_id}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    <span className="font-mono">{e.entity}</span>
                    {e.study_id && <> · {e.study_id}</>} · {e.actor_id}
                  </p>
                </div>
              </div>
            ))}
          </div>
        ))}

      {tab === 'searches' && (
        <>
          {/* A repeated search is the loudest signal here: they kept looking for
              something and kept not finding it. */}
          {repeated.length > 0 && (
            <div className="mb-5 border border-amber-200 bg-amber-50/60 rounded p-3">
              <p className="text-[10px] font-mono uppercase tracking-wider text-amber-800 mb-1.5">
                Searched more than once — probably missing
              </p>
              <div className="flex flex-wrap gap-2">
                {repeated.map((r) => (
                  <span
                    key={r.query}
                    className="text-xs bg-white border border-amber-200 rounded px-2 py-0.5"
                  >
                    {r.query}
                    <span className="text-amber-700 font-mono ml-1.5 tabular-nums">×{r.times}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {activity === null ? (
            <CardListSkeleton />
          ) : activity.length === 0 ? (
            <p className="text-sm text-slate-500">No searches recorded yet.</p>
          ) : (
            <div className="border-t border-slate-200">
              {activity.map((e, i) => (
                <div
                  key={`${e.at}-${i}`}
                  className="border-b border-slate-200 py-2.5 flex gap-3 items-baseline"
                >
                  <span className="text-xs text-slate-400 font-mono tabular-nums shrink-0 w-28">
                    {when(e.at)}
                  </span>
                  <Pill action={e.action} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-slate-800 break-words">{e.detail}</p>
                    <p className="text-xs text-slate-400 mt-0.5">
                      on {e.surface} · {e.actor_id}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
