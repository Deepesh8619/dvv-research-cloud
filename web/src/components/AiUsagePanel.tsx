import { useEffect, useState } from 'react'
import { AiUsage, errorMessage, usageApi } from '../api/client'

const usd = (n: number) => (n < 0.01 && n > 0 ? '< $0.01' : `$${n.toFixed(2)}`)
const thousands = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)

/**
 * What the AI models cost, from the token counts they report. An estimate at
 * the configured prices, before tax — the AWS bill is the authority — but it
 * says WHERE the Bedrock line comes from: copilot questions or everything else.
 */
export default function AiUsagePanel() {
  const [data, setData] = useState<AiUsage | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    usageApi
      .get(30)
      .then((res) => setData(res.data))
      .catch((err) => setError(errorMessage(err) || 'Could not load AI usage.'))
  }, [])

  if (error) return <p className="text-sm text-red-600">{error}</p>
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>
  const bedrock = data.prices_per_million.bedrock

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">AI cost this month</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{usd(data.month.estimated_usd)}</p>
          <p className="text-xs text-slate-400">estimated, before tax · {data.month.month}</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Copilot questions</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{data.month.copilot_questions}</p>
          <p className="text-xs text-slate-400">this month</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Model calls</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{data.month.calls}</p>
          <p className="text-xs text-slate-400">copilot, reading documents, data chat</p>
        </div>
      </div>

      {data.days.length === 0 ? (
        <p className="text-sm text-slate-500">
          Nothing counted yet. Usage is recorded from the next model call onwards.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Day</th>
                <th className="px-3 py-2 text-right font-medium">Copilot questions</th>
                <th className="px-3 py-2 text-right font-medium">Model calls</th>
                <th className="px-3 py-2 text-right font-medium">Tokens in / out</th>
                <th className="px-3 py-2 text-right font-medium">Copilot cost</th>
                <th className="px-3 py-2 text-right font-medium">Total cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.days.map((d) => (
                <tr key={d.day}>
                  <td className="px-3 py-2 tabular-nums">{d.day}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{d.copilot_questions}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{d.calls}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {thousands(d.input_tokens)} / {thousands(d.output_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{usd(d.copilot_usd)}</td>
                  <td className="px-3 py-2 text-right font-medium tabular-nums">{usd(d.estimated_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-slate-400">
        {data.note}
        {bedrock && ` Bedrock: $${bedrock.input} in / $${bedrock.output} out per million tokens.`}
      </p>
    </div>
  )
}
