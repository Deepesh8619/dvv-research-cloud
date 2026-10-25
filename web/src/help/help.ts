/**
 * The explanation behind every (i) in the app: what a feature is, why it is
 * there, how to use it. One place, so the same thing is explained the same
 * way wherever it appears, and so a feature without an explanation shows up
 * in review as a missing entry here.
 *
 * The text lives in help.json so the server can list it too: founders can
 * reword any of it from Admin → Wording (keys `help.<id>.<part>`), and this
 * file's wording is what shows until they do.
 */
import text from './help.json'

export interface Help { title: string; what: string; why: string; how: string }

export const HELP: Record<keyof typeof text, Help> = text
export type HelpId = keyof typeof HELP
