import { useEffect, useState } from 'react'

import { complianceApi, type NoticeCheck } from '../api/client'

/**
 * What a respondent is told before agreeing, against what India's DPDP Act
 * asks — and, for a health study, whether they are told side effects they
 * describe will be reported. One click puts a notice and a consent question
 * first in the questionnaire; a "no" ends the interview.
 */
const field = 'w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm'

export default function NoticePanel({ studyId }: { studyId: string }) {
  const [check, setCheck] = useState<NoticeCheck | null>(null)
  const [form, setForm] = useState({ organisation: '', purpose: '', contact: '' })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    complianceApi.notice(studyId).then((r) => setCheck(r.data)).catch(() => setCheck(null))
  }, [studyId])

  if (!check) return null
  const missing = check.items.filter((i) => !i.present)
  const ready = form.organisation.trim().length > 1 && form.purpose.trim().length > 2 && form.contact.trim().length > 2

  const add = () => {
    setSaving(true)
    complianceApi
      .addNotice(studyId, form)
      .then((r) => { setCheck(r.data); setError(null) })
      .catch((e) => setError(e?.response?.status === 409 ? 'The questionnaire already has a notice.' : 'Could not add the notice.'))
      .finally(() => setSaving(false))
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="notice-panel">
      <h2 className="font-semibold text-slate-900">Consent and notice</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        What respondents are told before they agree (DPDP Act, s.5)
        {check.health_study ? ', and — this is a health study — that side effects they describe will be reported.' : '.'}
      </p>
      <ul className="mt-3 grid gap-1 text-sm sm:grid-cols-2">
        {check.items.map((i) => (
          <li key={i.item} className={i.present ? 'text-slate-600' : 'text-amber-800'}>
            <span aria-hidden className="mr-1.5">{i.present ? '✓' : '○'}</span>
            {i.label}
            <span className="sr-only">{i.present ? ' — present' : ' — missing'}</span>
          </li>
        ))}
      </ul>
      {missing.length > 0 && check.consent_questions.length === 0 && (
        <div className="mt-4 rounded-md bg-slate-50 p-4">
          <p className="text-sm text-slate-700">
            Add a notice and a consent question as the first screens. They name you, the purpose and a contact for
            withdrawal and grievances; a respondent who does not agree is screened out.
          </p>
          <div className="mt-3 grid items-end gap-3 sm:grid-cols-3">
            <label className="text-xs text-slate-600">Organisation
              <input className={field} value={form.organisation}
                onChange={(e) => setForm({ ...form, organisation: e.target.value })} />
            </label>
            <label className="text-xs text-slate-600">Purpose of the research
              <input className={field} value={form.purpose} placeholder="understanding treatment choices"
                onChange={(e) => setForm({ ...form, purpose: e.target.value })} />
            </label>
            <label className="text-xs text-slate-600">Contact for withdrawal and grievances
              <input className={field} value={form.contact} placeholder="privacy@yourfirm.com"
                onChange={(e) => setForm({ ...form, contact: e.target.value })} />
            </label>
          </div>
          <button type="button" disabled={!ready || saving} onClick={add}
            className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            {saving ? 'Adding…' : 'Add notice and consent question'}
          </button>
          {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        </div>
      )}
      {missing.length > 0 && check.consent_questions.length > 0 && (
        <p className="mt-3 text-sm text-amber-800">
          Consent is asked at {check.consent_questions.join(', ')}; add the missing items to its wording.
        </p>
      )}
    </section>
  )
}
