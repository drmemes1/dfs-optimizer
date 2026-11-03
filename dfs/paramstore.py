"""The only way new params versions get written. Rules are enforced here, in code:

- params are append-only (also enforced by a database trigger);
- every change records key, old -> new, origin ('measured_bias' or 'manual') and a reason;
- automated ('measured_bias') changes may move a key by at most 1% per slate, need a minimum
  sample, may touch a given key once per slate, and may never touch a frozen key;
- 'manual' changes need a graded result behind them (evidence naming graded slates).
Data-integrity guards (starter consensus, name matching, box-score verification) are not params."""
import copy
import json

from .params import FROZEN_KEYS

MAX_STEP = 0.01            # 1% of the current value per slate
MIN_PLAYER_GAMES = 40      # observations in the bucket behind an automated nudge
MIN_SLATES = 3             # distinct graded slates behind an automated nudge
# Keys whose evidence bar is higher. Vegas totals were inverted on the one graded slate; let data
# decide pace only after ~5 slates (binding lesson 4).
MIN_SLATES_FOR = {("signals", "pace_exponent"): 5}


class ParamsGuardError(ValueError):
    pass


def _get(payload, key):
    section, name = key
    try:
        return payload[section][name]
    except KeyError as e:
        raise ParamsGuardError(f"unknown param {section}.{name}") from e


def apply_changes(conn, changes: list[dict], kind: str, reason: str, evidence: dict) -> int:
    """changes: [{"key": ("signals", "b2b_mult"), "new": 0.97, "sample_n": 55, "slates": 4}, ...]
    Returns the new params version. Raises ParamsGuardError (nothing written) if any change is invalid."""
    if kind not in ("measured_bias", "manual"):
        raise ParamsGuardError("kind must be measured_bias or manual")
    if not reason or not reason.strip():
        raise ParamsGuardError("a reason is required")
    if not changes:
        raise ParamsGuardError("no changes")
    if kind == "manual" and not (evidence or {}).get("graded_slates"):
        raise ParamsGuardError("manual changes need a graded result behind them (evidence.graded_slates)")
    slate_id = (evidence or {}).get("slate_id")
    if kind == "measured_bias" and slate_id is None:
        raise ParamsGuardError("measured_bias changes must name the graded slate (evidence.slate_id)")

    with conn.cursor() as cur:
        cur.execute("SELECT version, payload FROM params ORDER BY version DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            raise ParamsGuardError("no params yet")
        payload = copy.deepcopy(row["payload"])
        already = set()
        if kind == "measured_bias":
            cur.execute("""SELECT changes FROM params WHERE kind='measured_bias'
                           AND (evidence->>'slate_id')::bigint = %s""", (slate_id,))
            for r in cur.fetchall():
                for c in r["changes"] or []:
                    already.add(tuple(c["key"]))

        recorded = []
        for c in changes:
            key = tuple(c["key"])
            old = _get(payload, key)
            new = c["new"]
            if not isinstance(old, (int, float)) or isinstance(old, bool):
                raise ParamsGuardError(f"{key} is not numeric")
            if kind == "measured_bias":
                if key in FROZEN_KEYS:
                    raise ParamsGuardError(f"{'.'.join(key)} is frozen; only a human with a graded "
                                           f"dominance result may change it")
                if key in already:
                    raise ParamsGuardError(f"{'.'.join(key)} was already adjusted for slate {slate_id}")
                need_slates = MIN_SLATES_FOR.get(key, MIN_SLATES)
                if (c.get("sample_n") or 0) < MIN_PLAYER_GAMES or (c.get("slates") or 0) < need_slates:
                    raise ParamsGuardError(f"{'.'.join(key)}: sample too small ({c.get('sample_n')} player-games "
                                           f"over {c.get('slates')} slates; need {MIN_PLAYER_GAMES} over {need_slates})")
                limit = abs(old) * MAX_STEP + 1e-12
                if abs(new - old) > limit:
                    raise ParamsGuardError(f"{'.'.join(key)}: {old} -> {new} moves more than 1% in one slate")
            payload[key[0]][key[1]] = type(old)(new) if isinstance(old, int) and float(new).is_integer() else new
            recorded.append({"key": list(key), "old": old, "new": new,
                             "sample_n": c.get("sample_n"), "slates": c.get("slates")})

        cur.execute("""INSERT INTO params(payload, reason, author, kind, changes, evidence)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING version""",
                    (json.dumps(payload), reason, "learner" if kind == "measured_bias" else "human", kind,
                     json.dumps(recorded), json.dumps(evidence or {})))
        version = cur.fetchone()["version"]
    conn.commit()
    return version


def history(conn, limit=50):
    with conn.cursor() as cur:
        cur.execute("""SELECT version, created_at, kind, author, reason, changes, evidence FROM params
                       ORDER BY version DESC LIMIT %s""", (limit,))
        return cur.fetchall()
