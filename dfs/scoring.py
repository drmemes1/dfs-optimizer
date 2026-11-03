"""DraftKings NBA Classic scoring."""


def dk_points(pts=0, fg3m=0, reb=0, ast=0, stl=0, blk=0, tov=0, dnp=False) -> float:
    """PTS*1 + 3PM*0.5 + REB*1.25 + AST*1.5 + STL*2 + BLK*2 - TOV*0.5,
    +1.5 for a double-double, +3 more for a triple-double (stacked: +4.5). DNP = 0."""
    if dnp:
        return 0.0
    score = pts + 0.5 * fg3m + 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk - 0.5 * tov
    doubles = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if doubles >= 2:
        score += 1.5
    if doubles >= 3:
        score += 3.0
    return round(score, 2)
