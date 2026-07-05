import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { DocChartIcon, DownloadIcon } from '../components/Icons'
import { shareApi, PublicStudy } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'

export default function SharePage() {
  const { token } = useParams<{ token: string }>()
  const [study, setStudy] = useState<PublicStudy | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      if (!token) return
      try {
        const res = await shareApi.getPublic(token)
        setStudy(res.data)
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Share link not found or expired')
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [token])

  if (loading) {
    return <LoadingSpinner />
  }

  if (error || !study) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
        <div className="w-full max-w-md text-center">
          <div className="inline-flex items-center gap-2.5 mb-6">
            <div className="w-10 h-10 rounded bg-primary-500 flex items-center justify-center">
              <DocChartIcon className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-xl font-semibold text-slate-900">DVV Research Cloud</h1>
          </div>
          <div className="bg-white rounded-lg border border-slate-200 p-6">
            <p className="text-slate-600 mb-4">{error || 'Share link not found'}</p>
            <p className="text-sm text-slate-500">This link may have expired or been revoked.</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-100">
      {/* Header */}
      <header className="bg-primary-500 border-b border-primary-600">
        <div className="max-w-4xl mx-auto px-6 py-6">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-8 h-8 rounded bg-white/10 flex items-center justify-center">
              <DocChartIcon className="w-5 h-5 text-white" />
            </div>
            <h1 className="text-white font-semibold">DVV Research Cloud</h1>
          </div>
          <p className="text-white/70 text-sm">Project Report</p>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Project info card */}
        <div className="bg-white rounded-lg border border-slate-200 p-6 mb-6">
          <div className="mb-6">
            <h2 className="text-2xl font-semibold text-slate-900 mb-2">{study.client_name}</h2>
            <p className="text-sm text-slate-500">Created {new Date(study.created_at).toLocaleDateString()}</p>
          </div>

          {/* Status badge */}
          <div className="mb-6">
            <div className="inline-flex items-center gap-2">
              <span className="text-xs font-medium text-slate-600 uppercase tracking-wide">Status</span>
              <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                {study.status === 'approved_for_delivery' ? 'Ready for Download' : 'Delivered'}
              </span>
            </div>
          </div>

          {/* Download buttons */}
          <div className="space-y-3">
            <p className="text-sm font-medium text-slate-700 mb-3">Available Reports</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {study.excel_available && (
                <a
                  href={shareApi.publicDownloadUrl(token!, 'excel')}
                  download
                  className="inline-flex items-center justify-center gap-2 px-4 py-3 rounded-md bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 transition-colors"
                >
                  <DownloadIcon className="w-4 h-4" />
                  Download Excel
                </a>
              )}
              {study.pptx_available && (
                <a
                  href={shareApi.publicDownloadUrl(token!, 'pptx')}
                  download
                  className="inline-flex items-center justify-center gap-2 px-4 py-3 rounded-md bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 transition-colors"
                >
                  <DownloadIcon className="w-4 h-4" />
                  Download PowerPoint
                </a>
              )}
            </div>

            {!study.excel_available && !study.pptx_available && (
              <p className="text-sm text-slate-500 italic">No reports available for download yet.</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="text-center py-6">
          <p className="text-xs text-slate-500">DVV Research Cloud • Secure Report Delivery</p>
        </div>
      </main>
    </div>
  )
}
