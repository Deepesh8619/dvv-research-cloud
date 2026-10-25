import { fieldingApi, type WorkflowRun } from '../api/client'

/** Poll a workflow run every 1.5 s until it is no longer running; returns the final run. */
export async function pollRun(runId: string, onUpdate: (run: WorkflowRun) => void): Promise<WorkflowRun> {
  for (;;) {
    const run = (await fieldingApi.workflowRun(runId)).data
    onUpdate(run)
    if (run.state !== 'running') return run
    await new Promise((r) => setTimeout(r, 1500))
  }
}
