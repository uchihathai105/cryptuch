"""Track-record review: how closed calls performed and why they won or lost.

Pure functions (no database, no network). Results are in R after fees and slippage ("net"), because the
raw result_r stored with each call ignores costs. Everything here describes the past: groups with few calls are
shown but never used for conclusions, and no rule is changed automatically.
"""
from __future__ import annotations

import datetime as dt
import json

from . import config as C

VN = dt.timezone(dt.timedelta(hours=7))


# ---------------------------------------------------------------- per call
def cost_r(c: dict) -> float:
    """Fees + slippage as a share of the risk (the distance from entry to stop)."""
    risk_pct = abs(c["entry"] - c["stop"]) / c["entry"] * 100 if c.get("entry") and c.get("stop") is not None else 0
    return C.CALL_COST_PCT.get(c.get("market"), 0.3) / risk_pct if risk_pct > 0 else 0.0


def category(c: dict) -> str:
    out = c.get("outcome") or ""
    if out.startswith("Target 2"):
        return "Target 2"
    if out.startswith("Stopped at entry"):
        return "Stopped at entry after T1"
    if out.startswith("Stop-loss"):
        return "Stop-loss"
    if out.startswith("Exit signal"):
        return "Exit signal (win)" if (c.get("result_r") or 0) > 0 else "Exit signal (loss)"
    return "Other"


def checks_of(c: dict) -> dict:
    try:
        return json.loads(c.get("checks") or "{}")
    except ValueError:
        return {}


def held_minutes(c: dict) -> float | None:
    if c.get("closed_at") and c.get("created_at"):
        return (c["closed_at"] - c["created_at"]) / 60000
    return None


def explain(c: dict) -> str:
    """One or two sentences on why this call ended the way it did."""
    cat, mfe, mae = category(c), c.get("mfe_r"), c.get("mae_r")
    known = mfe is not None and c.get("checks") is not None   # opened after v1.2.3, so excursions were recorded
    t1 = C.TARGET1_R
    held = held_minutes(c)
    parts = []
    if cat == "Target 2":
        parts.append("Full win: price reached target 2.")
        if known and mae is not None and mae >= 0.5:
            parts.append(f"It first went {mae:.1f}R against the call, so the stop was tested.")
    elif cat == "Stopped at entry after T1":
        parts.append("Half the position was taken at T1, then price came back to the entry. A small win: the move faded after T1.")
    elif cat == "Stop-loss":
        if not known:
            parts.append("Stopped out. No excursion data for calls opened before v1.2.3.")
        elif mfe < 0.3:
            parts.append(f"Wrong from the start: price never went more than {mfe:.1f}R in favour before the stop. "
                         f"Look at entry timing and direction.")
        elif mfe >= 1.0:
            parts.append(f"Reversal: price reached {mfe:.1f}R in favour (T1 is {t1}R) and then fell back to the stop. "
                         f"A nearer first target or taking profit earlier would have saved this one.")
        else:
            parts.append(f"Moved {mfe:.1f}R in favour but not far enough for T1 ({t1}R), then reversed to the stop.")
        if held is not None and held < 60:
            parts.append(f"The stop was hit within {held:.0f} minutes.")
    elif cat.startswith("Exit signal"):
        parts.append(("Closed in profit" if "win" in cat else "Closed at a loss") + " by an exit rule: "
                     + (c.get("outcome") or "").replace("Exit signal: ", "") + ".")
    else:
        parts.append(c.get("outcome") or "Closed.")
    if (c.get("result_r") or 0) <= 0 and c.get("checks"):
        failed = [k for k, v in checks_of(c).items() if not v]
        if failed:
            parts.append("Checks not met at entry: " + ", ".join(failed) + ".")
    return " ".join(parts)


def enrich(calls: list[dict]) -> list[dict]:
    out = []
    for c in calls:
        if c.get("result_r") is None or c.get("entry") is None:
            continue
        e = dict(c)
        e["net_r"] = c["result_r"] - cost_r(c)
        e["category"] = category(c)
        e["held_min"] = held_minutes(c)
        out.append(e)
    return out


# ---------------------------------------------------------------- statistics
def _summ(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    nets = [r["net_r"] for r in rows]
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x < 0]
    return {"n": n, "win_rate": round(len(wins) / n * 100), "avg_r": sum(nets) / n, "total_r": sum(nets),
            "gross_avg_r": sum(r["result_r"] for r in rows) / n,
            "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
            "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None}


def _by(rows: list[dict], keyfn, order: list | None = None, min_n: int = 1) -> list[dict]:
    buckets: dict = {}
    for r in rows:
        k = keyfn(r)
        if k is not None:
            buckets.setdefault(k, []).append(r)
    keys = [k for k in (order or sorted(buckets, key=str)) if k in buckets]
    return [{"key": k, **_summ(buckets[k])} for k in keys if len(buckets[k]) >= min_n]


def _conf_bucket(r):
    c = r.get("confidence")
    return None if c is None else ("80+" if c >= 80 else "70-79" if c >= 70 else "60-69" if c >= 60 else "under 60")


def _held_bucket(r):
    h = r.get("held_min")
    return None if h is None else ("under 1 h" if h < 60 else "1-4 h" if h < 240 else "4-12 h" if h < 720 else "over 12 h")


def _hour_block(r):
    if not r.get("closed_at") and not r.get("created_at"):
        return None
    h = dt.datetime.fromtimestamp(r["created_at"] / 1000, VN).hour
    a = h // 4 * 4
    return f"{a:02d}-{a + 4:02d}h"


def _drawdown_and_streak(rows: list[dict]) -> tuple[float, int]:
    peak = cum = dd = 0.0
    streak = longest = 0
    for r in sorted(rows, key=lambda r: r.get("closed_at") or 0):
        cum += r["net_r"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if r["net_r"] <= 0 else 0
        longest = max(longest, streak)
    return dd, longest


def analyze(closed: list[dict]) -> dict:
    rows = enrich(closed)
    if not rows:
        return {"n": 0}
    overall = _summ(rows)
    dd, streak = _drawdown_and_streak(rows)
    overall.update(max_drawdown_r=dd, longest_losing_streak=streak,
                   avg_cost_r=sum(cost_r(r) for r in rows) / len(rows))
    conf_order = ["under 60", "60-69", "70-79", "80+"]
    held_order = ["under 1 h", "1-4 h", "4-12 h", "over 12 h"]
    groups = {
        "Call": _by(rows, lambda r: r.get("call")),
        "Market": _by(rows, lambda r: r.get("market")),
        "Confidence at entry": _by(rows, _conf_bucket, conf_order),
        "BTC regime at entry": _by(rows, lambda r: r.get("regime")),
        "Setup": _by(rows, lambda r: r.get("setup_kind")),
        "How the call ended": _by(rows, lambda r: r["category"]),
        "Time held": _by(rows, _held_bucket, held_order),
        "Time of day opened (your time)": _by(rows, _hour_block),
        "Rules version": _by(rows, lambda r: r.get("rules_version")),
    }
    groups = {k: v for k, v in groups.items() if v and not (len(v) == 1 and k in ("Rules version",))}

    # which checks mattered: results when a check was met vs not met at entry
    snap = [r for r in rows if r.get("checks")]
    checks = []
    names = sorted({k for r in snap for k in checks_of(r)})
    for name in names:
        met = [r for r in snap if checks_of(r).get(name) is True]
        miss = [r for r in snap if checks_of(r).get(name) is False]
        a, b = _summ(met), _summ(miss)
        edge = (a["avg_r"] - b["avg_r"]) if a["n"] and b["n"] else None
        checks.append({"name": name, "met": a, "missed": b, "edge": edge})

    # how the trades moved: reversals, fast failures, heat taken by winners
    have = [r for r in rows if r.get("mfe_r") is not None and r.get("checks") is not None]
    losers = [r for r in have if r["net_r"] < 0]
    winners = [r for r in have if r["net_r"] > 0]
    movement = {
        "with_data": len(have), "losers": len(losers), "winners": len(winners),
        "loser_reversal_share": (sum(1 for r in losers if r["mfe_r"] >= 1.0) / len(losers) * 100) if losers else None,
        "loser_never_worked_share": (sum(1 for r in losers if r["mfe_r"] < 0.3) / len(losers) * 100) if losers else None,
        "loser_fast_stop_share": (sum(1 for r in losers if (r.get("held_min") or 1e9) < 60) / len(losers) * 100) if losers else None,
        "winner_avg_heat_r": (sum(r["mae_r"] or 0 for r in winners) / len(winners)) if winners else None,
        "winner_avg_mfe_r": (sum(r["mfe_r"] for r in winners) / len(winners)) if winners else None,
        "winner_avg_result_r": (sum(r["net_r"] for r in winners) / len(winners)) if winners else None,
    }
    return {"n": len(rows), "overall": overall, "groups": groups, "checks": checks, "movement": movement,
            "findings": findings(rows, overall, groups, checks, movement)}


# ---------------------------------------------------------------- plain-language findings
def findings(rows, overall, groups, checks, movement) -> list[dict]:
    n, out = len(rows), []
    early = n < C.REVIEW_MIN_ACT
    if early:
        out.append({"tone": "warn", "text": f"Only {n} closed calls so far. Patterns below are hints, not proof: wait for about "
                                            f"{C.REVIEW_MIN_ACT} before changing any rule, and for more before trusting a small group."})
    avg = overall["avg_r"]
    if n >= C.REVIEW_MIN_GROUP:
        if avg < 0:
            out.append({"tone": "bad", "text": f"After fees and slippage the calls average {avg:+.2f}R. "
                                               f"Without costs they average {overall['gross_avg_r']:+.2f}R."})
        else:
            out.append({"tone": "good", "text": f"After fees and slippage the calls average {avg:+.2f}R per call "
                                                f"({overall['win_rate']}% win rate)."})
        if overall["avg_cost_r"] >= 0.15:
            out.append({"tone": "warn", "text": f"Fees and slippage cost about {overall['avg_cost_r']:.2f}R per call because the stops are "
                                                f"close to the entry. Tight stops let costs eat a large part of any edge."})

    # groups that differ clearly from the whole
    cand = []
    for gname, items in groups.items():
        if gname in ("How the call ended", "Rules version"):
            continue
        for it in items:
            if it["n"] >= C.REVIEW_MIN_GROUP and abs(it["avg_r"] - avg) >= C.REVIEW_EDGE_R:
                cand.append((abs(it["avg_r"] - avg) * it["n"] ** 0.5, gname, it))
    for _, gname, it in sorted(cand, key=lambda x: -x[0])[:4]:
        better = it["avg_r"] > avg
        out.append({"tone": "good" if better else "bad",
                    "text": f"{gname}: \"{it['key']}\" averaged {it['avg_r']:+.2f}R over {it['n']} calls, "
                            f"{'better' if better else 'worse'} than the {avg:+.2f}R of all calls. "
                            + ("Consider favouring it." if better else "Consider skipping or tightening it.")})

    # does higher confidence help?
    conf = {g["key"]: g for g in groups.get("Confidence at entry", [])}
    low = [g for k, g in conf.items() if k in ("under 60", "60-69") and g["n"] >= C.REVIEW_MIN_GROUP]
    high = [g for k, g in conf.items() if k in ("70-79", "80+") and g["n"] >= C.REVIEW_MIN_GROUP]
    if low and high and max(g["avg_r"] for g in high) <= min(g["avg_r"] for g in low):
        out.append({"tone": "warn", "text": "Higher confidence did not do better than lower confidence. The confidence score may not be "
                                            "predictive yet; keep collecting calls before trusting it."})

    # checks that did or did not help
    for c in checks:
        if c["edge"] is not None and c["met"]["n"] >= 5 and c["missed"]["n"] >= 5 and abs(c["edge"]) >= C.REVIEW_EDGE_R:
            if c["edge"] > 0:
                out.append({"tone": "good", "text": f"Check \"{c['name']}\": calls where it was met averaged {c['met']['avg_r']:+.2f}R, "
                                                    f"{c['edge']:+.2f}R better than calls where it was not ({c['missed']['n']} calls). "
                                                    f"It looks useful; a higher weight could be tested."})
            else:
                out.append({"tone": "warn", "text": f"Check \"{c['name']}\": calls where it was met did {abs(c['edge']):.2f}R worse than "
                                                    f"calls where it was not ({c['missed']['n']} calls). It may not be helping; "
                                                    f"test a lower weight or removing it."})

    # how the losing trades moved
    m = movement
    if m["losers"] >= 8:
        if m["loser_reversal_share"] is not None and m["loser_reversal_share"] >= 40:
            out.append({"tone": "warn", "text": f"{m['loser_reversal_share']:.0f}% of losing calls first went at least 1R in your favour. "
                                                f"A nearer first target, or moving the stop earlier, may save some of them."})
        if m["loser_never_worked_share"] is not None and m["loser_never_worked_share"] >= 50:
            out.append({"tone": "warn", "text": f"{m['loser_never_worked_share']:.0f}% of losing calls never went more than 0.3R in your favour. "
                                                f"The entry timing or direction was wrong from the start."})
        if m["loser_fast_stop_share"] is not None and m["loser_fast_stop_share"] >= 50:
            out.append({"tone": "warn", "text": f"{m['loser_fast_stop_share']:.0f}% of losing calls were stopped within an hour. "
                                                f"Calls may be opened right before a pause or reversal."})
    if m["winners"] >= 8 and m["winner_avg_mfe_r"] is not None and m["winner_avg_result_r"] is not None \
            and m["winner_avg_mfe_r"] - m["winner_avg_result_r"] >= 1.0:
        out.append({"tone": "info", "text": f"Winning calls moved {m['winner_avg_mfe_r']:.1f}R in your favour on average but ended with "
                                            f"{m['winner_avg_result_r']:.1f}R: part of the move is given back."})

    if overall["longest_losing_streak"] >= 5:
        out.append({"tone": "info", "text": f"The longest losing run was {overall['longest_losing_streak']} calls and the worst drop "
                                            f"{overall['max_drawdown_r']:.1f}R. Size positions so a run like that is survivable."})
    if len(out) == (1 if early else 0):
        out.append({"tone": "info", "text": "No group, check or movement pattern stands out yet."})
    return out
