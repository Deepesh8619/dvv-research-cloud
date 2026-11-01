import type { Model } from 'survey-core'

export type Group = { codes: unknown[]; shuffle_within?: boolean }
type Choice = { value: unknown }

function shuffle<T>(xs: T[], rng: () => number): T[] {
  const a = [...xs]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

/**
 * One random order where each group's members stay side by side. Anchored
 * answers keep their slot; every other answer and every whole group is a unit
 * dealt into the remaining slots. Within a group the members are shuffled
 * unless `shuffle_within` is false, when they keep the order they were given.
 * `rng` returns [0, 1).
 */
export function orderWithGroups<T extends Choice>(
  choices: T[], groups: Group[], rng: () => number, anchored: unknown[] = [],
): T[] {
  const same = (a: unknown, b: unknown) => String(a) === String(b)
  const pinned = (c: T) => anchored.some((a) => same(a, c.value))
  const inGroup = (c: T) => groups.findIndex((g) => g.codes.some((x) => same(x, c.value)))
  const units: T[][] = []
  const seen = new Set<number>()
  for (const c of choices) {
    if (pinned(c)) continue
    const g = inGroup(c)
    if (g < 0) units.push([c])
    else if (!seen.has(g)) {
      seen.add(g)
      const members = choices.filter((m) => !pinned(m) && inGroup(m) === g)
      units.push(groups[g].shuffle_within === false ? members : shuffle(members, rng))
    }
  }
  const dealt = shuffle(units, rng).flat()
  let next = 0
  return choices.map((c) => (pinned(c) ? c : dealt[next++]))
}

/** Order, once per respondent, every question the compiled JSON marked with groups. */
export function applyKeepTogether(survey: Model, json: unknown, rng: () => number = Math.random): void {
  const pages = (json as { pages?: Array<{ elements?: Array<Record<string, unknown>> }> }).pages ?? []
  for (const el of pages.flatMap((p) => p.elements ?? [])) {
    const groups = el.keepTogether as Group[] | undefined
    const q = groups?.length ? survey.getQuestionByName(String(el.name)) : null
    if (!q || !groups) continue
    const q2 = q as unknown as { choices: Choice[] }
    q2.choices = orderWithGroups(q2.choices, groups, rng, (el.anchoredValues as unknown[]) ?? [])
  }
}
