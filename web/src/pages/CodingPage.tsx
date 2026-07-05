import { ChatIcon } from '../components/Icons'

export default function CodingPage() {
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold text-slate-900 mb-1">Verbatim Coding</h1>
      <p className="text-sm text-slate-500 mb-8">
        AI codeframe generation and review queue for open-ended responses
      </p>

      <div className="bg-white rounded-lg border border-slate-200 p-10 text-center">
        <div className="w-12 h-12 rounded-lg bg-primary-50 text-primary-500 flex items-center justify-center mx-auto mb-4">
          <ChatIcon className="w-6 h-6" />
        </div>
        <h2 className="font-semibold text-slate-900 mb-2">Coding runs inside each project</h2>
        <p className="text-sm text-slate-500 max-w-md mx-auto">
          When a project's data contains open-ended questions, the pipeline generates a codeframe from a
          sample of verbatims, codes every response with a confidence score, and queues low-confidence
          items here for human review. The cross-project review queue UI lands in the next release —
          coded results already flow into the project's tables and reports.
        </p>
      </div>
    </div>
  )
}
