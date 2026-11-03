"""Pool eligibility and the value board."""
from .signals import is_out, is_questionable


def eligible_pool(players: list[dict], params: dict, mode: str) -> list[dict]:
    """salary >= 5500 OR starter_consensus OR minutes >= 24, then drop contingent,
    and for CASH drop questionable and apply min_minutes. This exact form beat every
    stricter variant tested -- do not add value-play filters here."""
    pp = params["pool"]
    out = []
    for p in players:
        if is_out(p) or float(p.get("proj_adj") or 0) <= 0:
            continue
        minutes = float(p.get("minutes") or 0)
        if not (p["salary"] >= pp["eligible_min_salary"] or p.get("starter_consensus")
                or minutes >= pp["eligible_min_minutes"]):
            continue
        if p.get("contingent"):
            continue
        if mode == "CASH":
            cp = params["CASH"]
            if cp.get("exclude_questionable") and is_questionable(p):
                continue
            if minutes < cp.get("min_minutes", 0):
                continue
        out.append(p)
    return out


def value_grade(p: dict, params: dict):
    """A/B/C/D for sub-$5,500 players with 12+ projected minutes, by boxes ticked:
    consensus starter, 24+ projected minutes, projected value 5.0+. None if not graded."""
    vb = params["value_board"]
    minutes = float(p.get("minutes") or 0)
    if p["salary"] >= vb["max_salary"] or minutes < vb["min_minutes"] or is_out(p):
        return None, []
    boxes = []
    if p.get("starter_consensus"):
        boxes.append("starter")
    if minutes >= vb["minutes_box"]:
        boxes.append("minutes")
    if float(p.get("value") or 0) >= vb["value_box"]:
        boxes.append("value")
    return "DCBA"[len(boxes)], boxes


def value_board(players: list[dict], params: dict) -> list[dict]:
    rows = []
    for p in players:
        grade, boxes = value_grade(p, params)
        p["value_grade"] = grade
        if grade:
            rows.append({**p, "boxes": boxes})
    rows.sort(key=lambda r: ("ABCD".index(r["value_grade"]), -r["value"]))
    return rows
