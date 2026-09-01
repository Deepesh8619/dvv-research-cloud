/**
 * Who can do what, in one place.
 *
 * This existed as the literal `new Set(['owner', 'analyst'])` copy-pasted into
 * six files, which is exactly how the `founder` role ended up able to teach the
 * parser but NOT create a project: the correction loop added a role, and six
 * separate copies of the write-check never heard about it.
 *
 * Mirrors api/auth.py::require_role, where FOUNDER also satisfies ANALYST.
 * These checks are presentation only — they decide whether a control renders.
 * The server re-checks every request regardless; hiding a button is not access
 * control.
 */

/** Roles that may create and edit studies, questionnaires, and fielding. */
export const WRITE_ROLES = ['owner', 'analyst', 'founder'] as const

/** Founders' surfaces: the correction loop, wording, activity. */
export const FOUNDER_ROLES = ['owner', 'founder'] as const

const WRITE_ROLE_SET = new Set<string>(WRITE_ROLES)
const FOUNDER_ROLE_SET = new Set<string>(FOUNDER_ROLES)

export function canWrite(role: string | undefined | null): boolean {
  return !!role && WRITE_ROLE_SET.has(role)
}

export function canCorrect(role: string | undefined | null): boolean {
  return !!role && FOUNDER_ROLE_SET.has(role)
}

export function isOwner(role: string | undefined | null): boolean {
  return role === 'owner'
}
