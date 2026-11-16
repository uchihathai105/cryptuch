"""Journal statistics: results in USDT and R, win rate, and what each kind of mistake has cost."""
from __future__ import annotations

from . import config as C


def pnl(j: dict) -> float | None:
    """Result in USDT after fees; None while the trade is still open."""
    if j.get("exit") is None or j.get("entry") is None or j.get("qty") is None:
        return None
    gross = (j["exit"] - j["entry"]) * j["qty"] * (-1 if j.get("side") == "short" else 1)
    return gross - (j.get("fees") or 0.0)


def result_r(j: dict) -> float | None:
    """Result as a multiple of the money risked (needs the planned stop)."""
    p = pnl(j)
    if p is None or j.get("stop") is None or j.get("entry") is None or j.get("qty") is None:
        return None
    risk = abs(j["entry"] - j["stop"]) * j["qty"]
    return p / risk if risk > 0 else None


def tags_of(j: dict) -> list[str]:
    return [t for t in (j.get("tags") or "").split(",") if t]


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _group(rows: list[dict]) -> dict:
    ps = [pnl(j) for j in rows]
    return {"n": len(rows), "total": sum(ps), "avg": _avg(ps),
            "win_rate": round(sum(1 for p in ps if p > 0) / len(ps) * 100) if ps else None}


def stats(rows: list[dict]) -> dict:
    closed = [j for j in rows if pnl(j) is not None]
    if not closed:
        return {"closed": 0, "open": len(rows)}
    ps = [pnl(j) for j in closed]
    wins, losses = [p for p in ps if p > 0], [p for p in ps if p < 0]
    rs = [r for r in (result_r(j) for j in closed) if r is not None]
    mistakes = set(C.JOURNAL_MISTAKE_TAGS)
    with_mistake = [j for j in closed if mistakes & set(tags_of(j))]
    clean = [j for j in closed if not mistakes & set(tags_of(j))]
    by_tag = {t: _group([j for j in closed if t in tags_of(j)])
              for t in (*C.JOURNAL_MISTAKE_TAGS, *C.JOURNAL_GOOD_TAGS)
              if any(t in tags_of(j) for j in closed)}
    linked = [j for j in closed if j.get("call_r") is not None and result_r(j) is not None]
    return {
        "closed": len(closed), "open": len(rows) - len(closed),
        "win_rate": round(len(wins) / len(closed) * 100), "total": sum(ps), "expectancy": sum(ps) / len(closed),
        "avg_win": _avg(wins), "avg_loss": _avg(losses),
        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
        "avg_r": _avg(rs), "r_trades": len(rs),
        "followed": _group([j for j in closed if j.get("followed")]) if any(j.get("followed") for j in closed) else None,
        "not_followed": _group([j for j in closed if not j.get("followed")]) if any(not j.get("followed") for j in closed) else None,
        "with_mistake": _group(with_mistake) if with_mistake else None,
        "without_mistake": _group(clean) if clean else None,
        "by_tag": by_tag,
        "vs_app": {"n": len(linked), "my_avg_r": _avg([result_r(j) for j in linked]),
                   "app_avg_r": _avg([j["call_r"] for j in linked])} if linked else None,
    }
