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
const WRITE_ROLES = new Set(['owner', 'analyst', 'founder'])

/** Founders' surfaces: the correction loop, wording, activity. */
const FOUNDER_ROLES = new Set(['owner', 'founder'])

export function canWrite(role: string | undefined | null): boolean {
  return !!role && WRITE_ROLES.has(role)
}

export function canCorrect(role: string | undefined | null): boolean {
  return !!role && FOUNDER_ROLES.has(role)
}

export function isOwner(role: string | undefined | null): boolean {
  return role === 'owner'
}
