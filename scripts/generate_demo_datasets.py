"""Generate ready-made respondent datasets for the client-demo kit.

Second demo path: instead of fielding a live link, an account manager uploads a
finished respondent file through the normal New Project intake and gets instant
analysis on "existing" data (exactly how the pharma demo `.sav` was seeded).

For each of the six demo segments in ``scripts/demo_questionnaires.json`` this
writes ONE realistic dummy dataset of N=500 completes to ``data/demo/``:

  <slug>.csv   plain one-row-per-respondent export (labels inferred by the
               tabular adapter as bare codes — the honest CSV limitation).
  <slug>.xlsx  a 2-sheet workbook: a "Data" sheet + a "Datamap" codebook sheet
               in the Decipher/Confirmit block format that
               ``src/adapters/datamap_sheet.py`` parses, so values come through
               the intake adapter fully LABELED (Colgate, Very satisfied, ...),
               not as bare numbers. The xlsx is the flagship demo file.

Column conventions match ``src/engines/fielding/materialize.py`` so a dataset
links to its questionnaire for free (T5/T20):
  single/dropdown -> one column named the qid
  multi           -> exploded 0/1 dummies `{qid}r{code}`
  grid_single     -> one column per row `{qid}r{rowcode}`, cells are col codes
  numeric/open    -> one column named the qid
Plus `respondent_id` and `vDurationSec` (an interview-duration column named so
`src/engines/qc/respondents.py` finds it -> speeder check runs on uploads too).

Realism: completes only (no screen-outs), uneven market-leader brand shares,
adult-skewed demographics, and a per-respondent LATENT satisfaction factor that
drives BOTH the satisfaction grid and the NPS score, so detractors really do
rate the grid lower and crosstabs show a genuine relationship. Routing is
honored (detractor follow-up answered only where NPS < 7, blank otherwise;
screeners carry only qualifying answers). ~3% speeders and ~2% straightliners
are planted so the respondent-QC demo has something to flag; a few gibberish
open-ends seed the coding/QC quirk. Deterministic: seeded per segment.

Run:  .venv/bin/python scripts/generate_demo_datasets.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
QUESTIONNAIRES = ROOT / "scripts" / "demo_questionnaires.json"
OUT_DIR = ROOT / "data" / "demo"

N = 500
SPEEDER_FRAC = 0.03
STRAIGHTLINER_FRAC = 0.02
GIBBERISH_FRAC = 0.01

# Short, clusterable open-end verbatims — deliberately a small pool with heavy
# duplication so the verbatim-coding demo has real clusters to find.
POSITIVE_OES = [
    "Good quality and reliable",
    "Trustworthy brand I have used for years",
    "Easy to find everywhere",
    "Value for money",
    "Works well for me",
]
NEGATIVE_OES = [
    "Too expensive",
    "Prices keep going up",
    "Poor customer service",
    "Quality has gone down lately",
    "Hard to find sometimes",
]
NEUTRAL_OES = [
    "Nothing to change really",
    "It is okay",
    "No strong opinion",
    "Cannot think of anything",
]
GIBBERISH_OES = ["asdfgh", "aaaaaa", "qwerty", "dfghjk"]


def _decay_probs(codes: list[int], rng: np.random.Generator, sharp: float = 0.55) -> np.ndarray:
    """Uneven, market-leader-ish distribution over codes.

    A geometric decay (leader ~2-3x the tail) shuffled by a seeded permutation
    so the leading code isn't always the first option.
    """
    weights = sharp ** np.arange(len(codes), dtype=float)
    weights = weights[rng.permutation(len(codes))]
    return weights / weights.sum()


def _emit_single(name, label, codes, labels, probs, rng, n=N):
    col = rng.choice(codes, size=n, p=probs)
    return col.astype("int64"), {"name": name, "label": label, "kind": "single",
                                 "values": dict(zip(codes, labels, strict=True))}


def generate_segment(seg: dict, seed: int) -> tuple[pd.DataFrame, list[dict]]:
    """Return (wide DataFrame of N completes, ordered var-spec list for datamap)."""
    rng = np.random.default_rng(seed)
    survey = seg["survey"]
    questions = survey["questions"]

    # Shared latent satisfaction factor: high -> happy grid + promoter NPS.
    latent = rng.standard_normal(N)

    nps_qid = next((q["qid"] for q in questions if q["type"] == "numeric"), None)
    followup_qid = next(
        (q["qid"] for q in questions if q.get("show_if")), None
    )

    # NPS driven by latent (mean ~7.6 so ~30% land as detractors, <7).
    nps = np.clip(np.round(7.6 + 1.8 * latent + rng.normal(0, 1.1, N)), 0, 10).astype("int64")
    is_detractor = nps < 7

    cols: dict[str, np.ndarray] = {"respondent_id": np.arange(1, N + 1, dtype="int64")}
    specs: list[dict] = [
        {"name": "respondent_id", "label": "Respondent ID", "kind": "numeric", "values": {}}
    ]
    grid_member_cols: list[str] = []
    oe_first_col: str | None = None

    for q in questions:
        qid, qtype = q["qid"], q["type"]
        text = q["text"]

        if qtype in ("single", "dropdown"):
            opts = q["options"]
            codes = [o["code"] for o in opts]
            labels = [o["label"] for o in opts]
            term = q.get("terminate_if") or {}
            # Completes carry ONLY qualifying screener answers.
            banned = set(term.get("values") or ([term["value"]] if "value" in term else []))
            keep = [(c, lab) for c, lab in zip(codes, labels, strict=True) if c not in banned]
            kcodes = [c for c, _ in keep]
            klabels = [lab for _, lab in keep]
            if qid == "S1":  # age: exclude minors, skew adult
                probs = np.array([0.15, 0.30, 0.28, 0.17, 0.10][: len(kcodes)])
                probs = probs / probs.sum()
            else:
                probs = _decay_probs(kcodes, rng)
            cols[qid], _ = _emit_single(qid, text, kcodes, klabels, probs, rng)
            # Full value labels (all codes) in the datamap even if some are unused.
            specs.append({"name": qid, "label": text, "kind": "single",
                          "values": dict(zip(codes, labels, strict=True))})

        elif qtype == "numeric":
            if qid == nps_qid:
                cols[qid] = nps
            else:
                cols[qid] = np.clip(np.round(rng.normal(5, 2, N)), 0, 10).astype("int64")
            specs.append({"name": qid, "label": text, "kind": "numeric", "values": {}})

        elif qtype == "open_text":
            pool = POSITIVE_OES + NEGATIVE_OES + NEUTRAL_OES
            # Unhappy respondents skew toward negative verbatims.
            texts = []
            for i in range(N):
                if latent[i] < -0.4:
                    texts.append(rng.choice(NEGATIVE_OES))
                elif latent[i] > 0.6:
                    texts.append(rng.choice(POSITIVE_OES))
                else:
                    texts.append(rng.choice(pool))
            cols[qid] = np.array(texts, dtype=object)
            specs.append({"name": qid, "label": text, "kind": "text", "values": {}})
            if oe_first_col is None:
                oe_first_col = qid

        elif qtype == "multi":
            opts = q["options"]
            codes = [o["code"] for o in opts]
            term = q.get("terminate_if") or {}
            banned = set(term.get("values") or [])
            exclusive_code = next((o["code"] for o in opts if o.get("exclusive")), None)
            is_screener_multi = bool(banned)
            # Awareness-style decreasing selection probabilities per option.
            base_p = np.clip(0.7 * (0.8 ** np.arange(len(codes))), 0.08, 0.85)
            base_p = base_p[rng.permutation(len(codes))]
            selected = np.zeros((N, len(codes)), dtype="int64")
            answered = np.ones(N, dtype=bool)
            if followup_qid == qid:
                answered = is_detractor  # routed: only detractors see it
            for i in range(N):
                if not answered[i]:
                    selected[i, :] = -1  # sentinel -> null later
                    continue
                if is_screener_multi:
                    # Qualifying: never pick a terminating code; mostly "None of these".
                    if exclusive_code is not None and rng.random() < 0.88:
                        pick = {exclusive_code}
                    else:
                        safe = [c for c in codes if c not in banned and c != exclusive_code]
                        pick = set(rng.choice(safe, size=max(1, rng.integers(1, len(safe) + 1)),
                                              replace=False)) if safe else {exclusive_code}
                else:
                    pick = {codes[j] for j in range(len(codes)) if rng.random() < base_p[j]}
                    # Guarantee not all-0 and not all-1 (keeps a 0/1 multi from
                    # ever reading as a straightlined grid via the adapter path).
                    if not pick:
                        pick = {codes[int(rng.integers(len(codes)))]}
                    elif len(pick) == len(codes):
                        pick.discard(codes[int(rng.integers(len(codes)))])
                for j, c in enumerate(codes):
                    selected[i, j] = 1 if c in pick else 0
            for j, o in enumerate(opts):
                name = f"{qid}r{o['code']}"
                colv = selected[:, j].astype("float")
                colv[selected[:, j] == -1] = np.nan
                cols[name] = colv
                specs.append({"name": name, "label": f"{text} — {o['label']}",
                              "kind": "single", "values": {0: "Not selected", 1: "Selected"}})

        elif qtype == "grid_single":
            rows = q["rows"]
            gcols = q["cols"]
            ccodes = [c["code"] for c in gcols]
            clabels = [c["label"] for c in gcols]
            lo, hi = min(ccodes), max(ccodes)
            mid = (lo + hi) / 2
            for r in rows:
                name = f"{qid}r{r['code']}"
                row_off = rng.normal(0, 0.45)
                # Wide per-cell noise so even very-satisfied respondents vary
                # across items (no natural straightlining) — planted ones aside.
                rating = np.clip(
                    np.round(mid + 0.95 * latent + row_off + rng.normal(0, 1.15, N)),
                    lo, hi,
                ).astype("int64")
                cols[name] = rating
                specs.append({"name": name, "label": f"{text} — {r['label']}",
                              "kind": "single", "values": dict(zip(ccodes, clabels, strict=True))})
                grid_member_cols.append(name)

    df = pd.DataFrame(cols)

    # --- Plant quality quirks (deterministic indices) ---
    n_speed = int(N * SPEEDER_FRAC)
    n_straight = int(N * STRAIGHTLINER_FRAC)
    n_gib = int(N * GIBBERISH_FRAC)
    idx = rng.permutation(N)
    speed_idx = idx[:n_speed]
    straight_idx = idx[n_speed:n_speed + n_straight]
    gib_idx = idx[n_speed + n_straight:n_speed + n_straight + n_gib]

    # Interview duration (seconds): believable spread, then speeders forced low.
    duration = np.clip(rng.normal(400, 110, N), 150, None)
    duration[speed_idx] = rng.uniform(35, 90, len(speed_idx))  # < median/3
    df["vDurationSec"] = np.round(duration).astype("int64")
    specs.append({"name": "vDurationSec", "label": "Interview duration (seconds)",
                  "kind": "numeric", "values": {}})

    # Straightliners: identical answer across the whole satisfaction grid.
    if grid_member_cols:
        grid_prefixes: dict[str, list[str]] = {}
        for c in grid_member_cols:
            grid_prefixes.setdefault(c.split("r")[0], []).append(c)
        target = max(grid_prefixes.values(), key=len)
        for i in straight_idx:
            df.loc[i, target] = 3  # flat neutral across every grid item

    # Gibberish open-ends seed the coding/QC quirk.
    if oe_first_col is not None:
        for k, i in enumerate(gib_idx):
            df.loc[i, oe_first_col] = GIBBERISH_OES[k % len(GIBBERISH_OES)]

    stats = {
        "n": N,
        "n_cols": df.shape[1],
        "pct_detractor": round(100 * is_detractor.mean(), 1),
        "speeders": int(n_speed),
        "straightliners": int(n_straight),
        "gibberish": int(n_gib),
        "followup_qid": followup_qid,
        "nps_qid": nps_qid,
    }
    return df, specs, stats


def _datamap_rows(specs: list[dict]) -> list[list]:
    """Build the "Datamap" sheet rows in the block format parse_datamap_sheet reads."""
    rows: list[list] = []
    for s in specs:
        rows.append([f"[{s['name']}]: {s['label']}", None, None])
        if s["kind"] == "numeric":
            rows.append(["Open numeric response", None, None])
        elif s["kind"] == "text":
            rows.append(["Open text response", None, None])
        else:  # single
            vals = s["values"]
            rows.append([f"Values: {min(vals)}-{max(vals)}", None, None])
            for code, lab in vals.items():
                rows.append([None, code, lab])
        rows.append([None, None, None])  # blank-row separator
    return rows


def write_segment(slug: str, df: pd.DataFrame, specs: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / f"{slug}.csv", index=False)
    xlsx = OUT_DIR / f"{slug}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Data", index=False)
        pd.DataFrame(_datamap_rows(specs)).to_excel(
            xw, sheet_name="Datamap", index=False, header=False
        )


def main() -> None:
    segments = json.loads(QUESTIONNAIRES.read_text())
    print(f"Generating {len(segments)} demo datasets -> {OUT_DIR}\n")
    for i, seg in enumerate(segments):
        slug = seg["segment_slug"]
        df, specs, stats = generate_segment(seg, seed=1000 + i)
        write_segment(slug, df, specs)
        print(
            f"  {slug:<24} N={stats['n']}  cols={stats['n_cols']:>3}  "
            f"detractors={stats['pct_detractor']:>4}%  "
            f"speeders={stats['speeders']}  straightliners={stats['straightliners']}  "
            f"(NPS={stats['nps_qid']}, followup={stats['followup_qid']})"
        )
    print(f"\nWrote {len(segments)} x (.csv + .xlsx) to {OUT_DIR}")


if __name__ == "__main__":
    main()
