import type { Theme } from '../../api/themes'

// Respondent-link templates: the LAYOUT a link is drawn in — type, surfaces,
// header, how options sit. The theme still supplies the colour, so any template
// takes any of the eight themes. Slugs mirror backend TEMPLATE_SLUGS in
// src/core/survey_definition.py; keep the two lists in sync.
//
// SurveyJS stays the engine under every one of them (routing, masks, pipes,
// the per-page save): a template is a stylesheet scoped by `.dvv-t-<slug>` plus
// the header RespondentPage draws for it.

export interface Template {
  name: string
  blurb: string
  /** Google Fonts `family=` query; system fonts when absent. */
  font?: string
  family: string
  /** Page ground behind the questions. */
  ground: (theme: Theme) => string
}

export const TEMPLATES: Record<string, Template> = {
  classic: {
    name: 'Classic',
    blurb: 'One card, segmented progress',
    family: 'inherit',
    ground: (t) => t.wash,
  },
  pulse: {
    name: 'Pulse',
    blurb: 'Big tiles, one question a screen',
    font: 'Manrope:wght@500;600;700;800',
    family: "Manrope, system-ui, sans-serif",
    ground: () => '#F6F7FB',
  },
  ledger: {
    name: 'Ledger',
    blurb: 'Editorial serif, numbered, hairlines',
    font: 'Instrument+Serif&family=IBM+Plex+Sans:wght@400;500;600',
    family: "'IBM Plex Sans', system-ui, sans-serif",
    ground: () => '#EEF1F4',
  },
  chat: {
    name: 'Chat',
    blurb: 'Asked as a conversation',
    font: 'DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700',
    family: "'DM Sans', system-ui, sans-serif",
    ground: () => '#F2F4F6',
  },
  brand: {
    name: 'Brand Split',
    blurb: 'Theme colour header, white sheet',
    font: 'Sora:wght@400;600;700',
    family: 'Sora, system-ui, sans-serif',
    ground: (t) => t.accent,
  },
  play: {
    name: 'Play',
    blurb: 'Path progress, stars, faces',
    font: 'Fredoka:wght@400;500;600;700',
    family: 'Fredoka, system-ui, sans-serif',
    ground: () => '#E9F5FF',
  },
  clear: {
    name: 'Clear',
    blurb: 'Large text, high contrast',
    font: 'Atkinson+Hyperlegible:wght@400;700',
    family: "'Atkinson Hyperlegible', system-ui, sans-serif",
    ground: () => '#FFFFFF',
  },
}

export const DEFAULT_TEMPLATE_SLUG = 'classic'
export const TEMPLATE_SLUGS = Object.keys(TEMPLATES)

export function templateSlug(slug?: string | null): string {
  return slug && TEMPLATES[slug] ? slug : DEFAULT_TEMPLATE_SLUG
}

/** Load a template's face once; the fallback stack covers the wait. */
export function loadTemplateFont(slug: string): void {
  const font = TEMPLATES[slug]?.font
  if (!font || document.querySelector(`link[data-dvv-font="${slug}"]`)) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = `https://fonts.googleapis.com/css2?family=${font}&display=swap`
  link.dataset.dvvFont = slug
  document.head.appendChild(link)
}

const INK = '#1F2333'

/** Every template's rules, scoped by class, recoloured by the theme. Layered
 * after the base `.dvv-survey` block, so only what differs is said here. */
export function templateCss(theme: Theme): string {
  const A = theme.accent
  const D = theme.accentDeep
  return `
/* ---------- shared: templates other than Classic drop the white card */
.dvv-t-pulse .dvv-card, .dvv-t-ledger .dvv-card, .dvv-t-chat .dvv-card, .dvv-t-clear .dvv-card {
  background: transparent; border: 0; box-shadow: none; padding: 0;
}
.dvv-face { display: none; }
/* SurveyJS's empty title toolbar: invisible, but 8px past a phone's edge. */
.dvv-survey .sd-action-title-bar--empty { display: none !important; }
/* SurveyJS sets its own face (Open Sans) inside the survey; the template's wins. */
:is(.dvv-t-pulse, .dvv-t-ledger, .dvv-t-chat, .dvv-t-brand, .dvv-t-play, .dvv-t-clear) .sd-root-modern,
:is(.dvv-t-pulse, .dvv-t-ledger, .dvv-t-chat, .dvv-t-brand, .dvv-t-play, .dvv-t-clear) .sd-root-modern * {
  font-family: inherit !important;
}
/* SurveyJS sizes the title's text node itself (16px, under a 0px wrapper), so a
   size set on the title never reached the words. Classic keeps what it had. */
.dvv-t-pulse { --dvv-title: 1.65rem; }
.dvv-t-ledger { --dvv-title: 1.95rem; }
.dvv-t-chat { --dvv-title: 1.05rem; }
.dvv-t-brand, .dvv-t-play { --dvv-title: 1.4rem; }
.dvv-t-clear { --dvv-title: calc(var(--dvv-fs) * 1.3); }
:is(.dvv-t-pulse, .dvv-t-ledger, .dvv-t-chat, .dvv-t-brand, .dvv-t-play, .dvv-t-clear) .sd-question__title .sv-string-viewer {
  font-size: var(--dvv-title); line-height: 1.3;
}
:is(.dvv-t-ledger, .dvv-t-chat, .dvv-t-brand) .dvv-survey .sd-action-bar.sd-body__navigation { right: 0; width: auto !important; }
.dvv-t-ledger .dvv-card { padding-top: 28px; }

/* ---------- welcome / thank-you / screen-out screens */
:is(.dvv-t-pulse, .dvv-t-ledger, .dvv-t-chat, .dvv-t-play, .dvv-t-clear) .dvv-bar { display: none; }
:is(.dvv-t-ledger, .dvv-t-clear) .dvv-emblem { display: none; }
.dvv-t-pulse .dvv-centered { border: 0; border-radius: 28px; box-shadow: 0 24px 48px -28px ${A}66; }
.dvv-t-pulse .dvv-centered h1 { font-weight: 800; letter-spacing: -0.02em; }
.dvv-t-ledger .dvv-centered { background: transparent; border: 0; box-shadow: none; text-align: left; }
.dvv-t-ledger .dvv-centered h1 { font-size: 3rem; line-height: 1.02; color: #13161B; }
.dvv-t-ledger .dvv-centered :is(p, div) { margin-left: 0; justify-content: flex-start; }
.dvv-t-ledger .dvv-centered button { border-radius: 0; }
.dvv-t-chat .dvv-centered { border: 0; border-radius: 24px 24px 24px 8px; box-shadow: 0 1px 2px rgba(26,29,35,.08); }
.dvv-t-chat .dvv-centered button { border-radius: 24px; }
.dvv-t-brand .dvv-centered { border: 0; border-radius: 28px; }
.dvv-t-brand .dvv-centered button { border-radius: 14px; }
.dvv-t-play .dvv-centered { border: 2px solid ${INK}; border-radius: 24px; box-shadow: 6px 6px 0 ${INK}; }
.dvv-t-play .dvv-centered h1 { color: ${INK}; font-weight: 600; }
.dvv-t-play .dvv-centered button { background: #FFC93C !important; color: ${INK}; border: 2px solid ${INK}; box-shadow: 3px 3px 0 ${INK}; }
.dvv-t-clear .dvv-centered { border: 2px solid #111; border-radius: 12px; box-shadow: none; text-align: left; font-size: 20px; }
.dvv-t-clear .dvv-centered :is(p, div) { margin-left: 0; justify-content: flex-start; color: #111; }
.dvv-t-clear .dvv-centered button { width: 100%; min-height: 60px; background: ${D} !important; }

/* ---------- Pulse */
.dvv-t-pulse .dvv-survey .sd-question__title { font-size: 1.65rem; line-height: 2.1rem; font-weight: 800; letter-spacing: -0.02em; }
.dvv-t-pulse .dvv-survey .sd-item {
  min-height: 58px; border-radius: 16px; border: 2px solid transparent;
  box-shadow: 0 1px 2px rgba(16,24,40,.06), 0 0 0 1px #E4E7EF;
}
.dvv-t-pulse .dvv-survey .sd-item--checked { border-color: ${A}; background: ${A}10; box-shadow: 0 10px 24px -14px ${A}; }
.dvv-t-pulse .dvv-survey .sd-item__control-label { font-weight: 600; }
.dvv-t-pulse .dvv-choice { border-radius: 14px !important; }

/* ---------- Ledger */
.dvv-t-ledger h1, .dvv-t-ledger .dvv-survey .sd-question__title, .dvv-t-ledger .dvv-numeral {
  font-family: 'Instrument Serif', Georgia, serif !important; font-weight: 400; letter-spacing: 0;
}
.dvv-t-ledger .dvv-survey .sd-question__title { font-size: 1.95rem; line-height: 2.25rem; color: #13161B; }
.dvv-t-ledger .dvv-survey .sd-selectbase { border-top: 1px solid #C9D0D9; }
.dvv-t-ledger .dvv-survey .sd-item {
  margin: 0; min-height: 54px; border: 0; border-bottom: 1px solid #C9D0D9; border-radius: 0; background: transparent;
}
.dvv-t-ledger .dvv-survey .sd-item:hover { background: ${A}08; }
.dvv-t-ledger .dvv-survey .sd-item--checked { box-shadow: none; background: transparent; }
.dvv-t-ledger .dvv-survey .sd-item--checked .sd-item__control-label { font-weight: 600; }
.dvv-t-ledger .dvv-survey .sd-input {
  border: 0; border-bottom: 1.5px solid #13161B; border-radius: 0; background: transparent; padding-inline: 0;
}
.dvv-t-ledger .dvv-survey .sd-input:focus { box-shadow: none; border-color: ${A}; }
.dvv-t-ledger .dvv-choice { border-radius: 0 !important; }
.dvv-t-ledger .dvv-survey .sd-action-bar.sd-body__navigation {
  left: 0; bottom: 0; transform: none; width: 100%; border-radius: 0; box-shadow: none;
  background: #EEF1F4; border-top: 1px solid #C9D0D9;
  padding: 12px max(16px, calc(50% - 320px)) !important;
}
.dvv-t-ledger .dvv-survey .sd-btn { border-radius: 0; }
.dvv-t-ledger .dvv-survey .sd-body__navigation .sd-navigation__prev-btn { color: #4E5664; border: 0; background: transparent; }

/* ---------- Chat */
.dvv-t-chat .dvv-survey .sd-question__title {
  display: inline-block; max-width: 85%; margin-bottom: 1rem; padding: 11px 15px;
  background: #fff; border-radius: 18px 18px 18px 6px; box-shadow: 0 1px 2px rgba(26,29,35,.08);
  font-size: 1.05rem; line-height: 1.5rem; font-weight: 500; color: #1A1D23; letter-spacing: 0;
}
.dvv-t-chat .dvv-survey .sd-selectbase { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.dvv-t-chat .dvv-survey .sd-selectbase__column { display: contents; }
.dvv-t-chat .dvv-survey .sd-item {
  width: auto; min-height: 44px; margin: 0; padding: 0.5rem 1rem; border-radius: 22px;
  border: 1.5px solid ${A}; background: #fff;
}
.dvv-t-chat .dvv-survey .sd-item__decorator { display: none; }
.dvv-t-chat .dvv-survey .sd-item__control-label { color: ${A}; font-weight: 700; font-size: 0.95rem; }
.dvv-t-chat .dvv-survey .sd-item--checked { background: ${A}; box-shadow: none; }
.dvv-t-chat .dvv-survey .sd-item--checked .sd-item__control-label { color: #fff; }
.dvv-t-chat .dvv-survey .sd-input { border-radius: 24px; }
.dvv-t-chat .dvv-choice { border-radius: 22px !important; }
.dvv-t-chat .dvv-survey .sd-action-bar.sd-body__navigation {
  left: 0; bottom: 0; transform: none; width: 100%; border-radius: 0; box-shadow: none;
  background: #fff; border-top: 1px solid #E1E5EA;
  padding: 12px max(16px, calc(50% - 320px)) 20px !important;
}
.dvv-t-chat .dvv-survey .sd-btn { border-radius: 24px; }
.dvv-t-chat .dvv-survey .sd-body__navigation .sd-navigation__prev-btn { color: #1A1D23; border-color: #D3D8DF; }

/* ---------- Brand Split */
.dvv-t-brand.min-h-screen { padding-bottom: 0; }
.dvv-t-brand .dvv-card {
  min-height: 72vh; border: 0; border-radius: 28px 28px 0 0; box-shadow: none; padding: 30px 22px 140px;
}
.dvv-t-brand .dvv-survey .sd-question__title { font-weight: 700; }
.dvv-t-brand .dvv-survey .sd-item { border: 1.5px solid #E5E7EB; border-radius: 14px; }
.dvv-t-brand .dvv-survey .sd-item--checked { border-color: ${A}; background: #F5F7FB; box-shadow: none; }
.dvv-t-brand .dvv-survey .sd-item__decorator { border-radius: 7px; }
.dvv-t-brand .dvv-survey .sd-action-bar.sd-body__navigation {
  left: 0; bottom: 0; transform: none; width: 100%; border-radius: 0; box-shadow: 0 -1px 0 #E5E7EB;
  background: #fff; padding: 12px max(16px, calc(50% - 320px)) 20px !important;
}
.dvv-t-brand .dvv-survey .sd-body__navigation .sd-btn { border-radius: 14px; }
.dvv-t-brand .dvv-survey .sd-body__navigation .sd-navigation__prev-btn { color: #111827; border-color: #D1D5DB; }

/* ---------- Play */
.dvv-t-play .dvv-card { border: 2px solid ${INK}; border-radius: 24px; box-shadow: 5px 5px 0 ${INK}; }
.dvv-t-play .dvv-survey .sd-question__title { font-weight: 600; color: ${INK}; letter-spacing: 0; }
.dvv-t-play .dvv-survey .sd-item {
  border: 2px solid ${INK}; border-radius: 14px; box-shadow: 3px 3px 0 ${INK}; background: #fff;
}
.dvv-t-play .dvv-survey .sd-item:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 ${INK}; }
.dvv-t-play .dvv-survey .sd-item--checked { background: #FFC93C; border-color: ${INK}; box-shadow: 3px 3px 0 ${INK}; }
.dvv-t-play .dvv-survey .sd-item__control-label { color: ${INK}; font-weight: 500; }
.dvv-t-play .dvv-choice { border: 2px solid ${INK} !important; box-shadow: 2px 2px 0 ${INK}; }
.dvv-t-play .dvv-choice[aria-checked="true"] { background: #FFC93C !important; color: ${INK} !important; }
.dvv-t-play .dvv-face { display: block; margin: 0 auto 2px; }
.dvv-t-play .dvv-choice:has(.dvv-face) { height: auto; padding-block: 8px; }
.dvv-t-play .dvv-survey .sd-action-bar.sd-body__navigation { background: #fff; border: 2px solid ${INK}; box-shadow: 4px 4px 0 ${INK}; }
.dvv-t-play .dvv-survey .sd-body__navigation .sd-btn { background: #FFC93C; color: ${INK}; border: 2px solid ${INK}; }
.dvv-t-play .dvv-survey .sd-body__navigation .sd-navigation__prev-btn { background: #fff; color: ${INK}; border-color: ${INK}; }
@keyframes dvv-pop { 0% { transform: scale(1); } 40% { transform: scale(1.25); } 100% { transform: scale(1); } }
.dvv-t-play .dvv-stars { animation: dvv-pop 360ms ease-out; }

/* ---------- Clear */
.dvv-t-clear { color: #111111; font-size: var(--dvv-fs); }
.dvv-t-clear *, .dvv-t-clear *::before { animation: none !important; transition: none !important; }
.dvv-t-clear :focus-visible { outline: 4px solid #FFB000 !important; outline-offset: 2px; }
.dvv-t-clear .dvv-survey .sd-question__title { font-size: calc(var(--dvv-fs) * 1.3); line-height: 1.3; font-weight: 700; color: #111; letter-spacing: 0; }
.dvv-t-clear .dvv-survey .sd-item__control-label, .dvv-t-clear .dvv-survey .sd-input,
.dvv-t-clear .dvv-choice, .dvv-t-clear .dvv-survey .sd-question__erbox, .dvv-t-clear .dvv-survey .sd-btn {
  font-size: var(--dvv-fs) !important; color: #111;
}
.dvv-t-clear .dvv-survey .sd-selectbase { border-top: 2px solid #D0D0D0; }
.dvv-t-clear .dvv-survey .sd-item {
  margin: 0; min-height: 60px; border: 0; border-bottom: 2px solid #D0D0D0; border-radius: 0; background: #fff;
}
.dvv-t-clear .dvv-survey .sd-item--checked { background: #E6EEFB; box-shadow: none; }
.dvv-t-clear .dvv-survey .sd-item--checked .sd-item__control-label { font-weight: 700; }
.dvv-t-clear .dvv-survey .sd-item__decorator { width: 28px; height: 28px; border: 3px solid #111; box-shadow: none; }
.dvv-t-clear .dvv-survey .sd-input { border: 3px solid #111; border-radius: 8px; }
.dvv-t-clear .dvv-choice { border: 3px solid #111 !important; border-radius: 8px !important; min-height: 52px; }
.dvv-t-clear .dvv-survey .sd-question__erbox {
  background: #FDECEC; color: #A31515 !important; font-weight: 700; box-sizing: border-box; max-width: 100%; margin-inline: 0;
  line-height: 1.35;
}
.dvv-t-clear .dvv-survey :is(.sd-item, .sd-question__erbox) .sv-string-viewer { font-size: var(--dvv-fs); line-height: 1.35; }
.dvv-t-clear .dvv-survey .sd-body__navigation .sv-action { width: 100%; }
.dvv-t-clear .dvv-survey .sd-action-bar.sd-body__navigation {
  position: static; transform: none; width: 100%; margin-top: 28px; padding: 0 !important;
  flex-direction: column-reverse; background: transparent; box-shadow: none;
}
.dvv-t-clear .dvv-survey .sd-body__navigation .sd-btn { min-height: 60px; border-radius: 10px; background: ${D}; color: #fff; font-weight: 700; }
.dvv-t-clear .dvv-survey .sd-body__navigation .sd-navigation__prev-btn { min-height: 52px; background: #fff; color: #111; border: 2px solid #111; }
`
}
