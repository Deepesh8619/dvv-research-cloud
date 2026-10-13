"""Vendor scorecard math: cost, progress and quality rates. Pure, no I/O.

A missing price or target is None, never 0: "free" and "unknown" must not look alike.
"""

from __future__ import annotations

from collections.abc import Iterable


def cost(n: int | None, cpi: float | None) -> float | None:
    return None if n is None or cpi is None else round(n * cpi, 2)


def pct(part: int, whole: int | None) -> float | None:
    """part / whole as a percentage; None when whole is missing or zero."""
    return round(100 * part / whole, 1) if whole else None


def flag_rates(n: int, flags: dict[str, list[dict]]) -> dict[str, float | None]:
    """Share (%) of n completes carrying each QC flag, from collect_flags output."""
    return {
        f"{check}_rate": pct(sum(any(f["check"] == check for f in v) for v in flags.values()), n)
        for check in ("speeder", "straightliner", "duplicate")
    }


def pct_removed(session_ids: Iterable[str], decisions: dict[str, dict]) -> float | None:
    ids = list(session_ids)
    removed = sum(decisions.get(i, {}).get("decision") == "remove" for i in ids)
    return pct(removed, len(ids))


def scorecard_row(link, report: dict, quality: dict, removed_pct: float | None) -> dict:
    """One vendor row. `report` is FieldReport.to_dict(); `quality` the flag rates."""
    completes = report["dispositions"].get("complete", 0)
    dropoff = report["dropoff_by_question"]
    return {
        "link_id": link.id,
        "vendor_name": link.vendor_name,
        "starts": report["starts"],
        "completes": completes,
        "incidence_pct": report["incidence_pct"],
        "median_loi": report["median_loi_sec"],
        "top_dropoff_question": dropoff[0]["qid"] if dropoff else None,
        "cost_to_date": cost(completes, link.cpi),
        "projected_cost": cost(link.target_completes, link.cpi),
        "pct_of_target": pct(completes, link.target_completes),
        **quality,
        "pct_removed": removed_pct,
    }


def _split(remaining: int, weights: list[int]) -> list[int]:
    """Split remaining in proportion to weights (equal when all zero); sums exactly."""
    total = sum(weights)
    raw = [remaining * (w / total if total else 1 / len(weights)) for w in weights]
    out = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[: remaining - sum(out)]:
        out[i] += 1
    return out


def quota_balance(links: dict[str, str | None], cells: Iterable, target: int) -> dict:
    """Suggest per-vendor limits per cell name so the vendors reach `target` combined.

    `links` maps link_id -> vendor_name; `cells` have fielding_link_id, name, count,
    cell_limit. Read-only suggestion. The remaining need goes to vendors still under
    their limit (None = open) in proportion to their count so far (their fill; cells
    carry no timestamps). Vendors at their limit keep it. A name that is empty or
    repeated on one link is `ambiguous`, never merged.
    """
    by_name: dict[str | None, list] = {}
    for c in cells:
        if c.fielding_link_id in links:
            by_name.setdefault((c.name or "").strip() or None, []).append(c)
    groups, ambiguous = [], []
    for name, rows in by_name.items():
        link_ids = [r.fielding_link_id for r in rows]
        if name is None or len(set(link_ids)) != len(link_ids):
            ambiguous.append({
                "name": name, "link_ids": sorted(set(link_ids)),
                "reason": "empty name" if name is None else "name appears more than once on a link",
            })
            continue
        vendors = [{"link_id": r.fielding_link_id, "vendor_name": links[r.fielding_link_id],
                    "count": r.count or 0, "limit": r.cell_limit} for r in rows]
        combined = sum(v["count"] for v in vendors)
        limits = [v["limit"] for v in vendors]
        open_ = any(lim is None for lim in limits)
        under = [v for v in vendors if v["limit"] is None or v["count"] < v["limit"]]
        need = max(target - combined, 0)
        add = dict(zip((v["link_id"] for v in under),
                       _split(need, [v["count"] for v in under]) if under else [], strict=False))
        for v in vendors:
            extra = add.get(v["link_id"])
            v["suggested_limit"] = v["limit"] if extra is None else v["count"] + extra
        groups.append({
            "cell": name, "vendors": vendors, "combined_count": combined,
            "combined_limit": None if open_ else sum(limits),
            "limit_open": open_, "target": target, "remaining_need": need,
            "unplaceable": need if need and not under else 0,
        })
    return {"cells": groups, "ambiguous": ambiguous, "target": target,
            "note": "Suggestion only. Nothing was changed; adjust limits via PATCH /quotas/{id}."}


# --- Invitations / reminders: a stable pid per contact, nothing stored -------------

CONTACT_COLUMNS = ("email", "phone", "name")
MAX_INVITE_ROWS = 100_000


def normalise_contact(email: str | None, phone: str | None) -> str | None:
    """Lowercased/stripped email, else digits-only phone, else None."""
    e = (email or "").strip().lower()
    if e:
        return e
    return "".join(ch for ch in (phone or "") if ch.isdigit()) or None


def pid_for(secret: str, contact: str) -> str:
    """16 hex chars of HMAC-SHA256(secret, contact). Not reversible to the contact."""
    import hashlib
    import hmac

    return hmac.new(secret.encode(), contact.encode(), hashlib.sha256).hexdigest()[:16]


def row_pid(secret: str, row: dict[str, str]) -> str | None:
    """pid for a CSV row with lowercase keys; None when it has no usable contact."""
    contact = normalise_contact(row.get("email"), row.get("phone"))
    return pid_for(secret, contact) if contact else None


def unfinished(rows: Iterable[dict], finished_pids: set[str]) -> list[dict]:
    """Rows (with a 'pid' key) whose pid is not finished; rows with no pid are dropped."""
    return [r for r in rows if r.get("pid") and r["pid"] not in finished_pids]
