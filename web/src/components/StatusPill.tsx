const STATUS_CONFIG: Record<string, { label: string; classes: string; dot: string }> = {
  authoring: { label: 'Authoring', classes: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20', dot: 'bg-indigo-500' },
  classifying: { label: 'Processing', classes: 'bg-amber-50 text-amber-700 ring-amber-600/20', dot: 'bg-amber-500 animate-pulse' },
  qc_passed: { label: 'QC Passed', classes: 'bg-sky-50 text-sky-700 ring-sky-600/20', dot: 'bg-sky-500' },
  qc_pending: { label: 'QC Review', classes: 'bg-amber-50 text-amber-700 ring-amber-600/20', dot: 'bg-amber-500' },
  coding_done: { label: 'Coded', classes: 'bg-sky-50 text-sky-700 ring-sky-600/20', dot: 'bg-sky-500' },
  rendered: { label: 'Ready', classes: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', dot: 'bg-emerald-500' },
  approved_for_delivery: { label: 'Awaiting Sign-off', classes: 'bg-violet-50 text-violet-700 ring-violet-600/20', dot: 'bg-violet-500' },
  delivered: { label: 'Delivered', classes: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', dot: 'bg-emerald-500' },
  error: { label: 'Failed', classes: 'bg-red-50 text-red-700 ring-red-600/20', dot: 'bg-red-500' },
  archived: { label: 'Archived', classes: 'bg-slate-100 text-slate-600 ring-slate-500/20', dot: 'bg-slate-400' },
}

export default function StatusPill({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || {
    label: status.replace(/_/g, ' '),
    classes: 'bg-slate-100 text-slate-600 ring-slate-500/20',
    dot: 'bg-slate-400',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ring-1 ring-inset ${cfg.classes}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  )
}
