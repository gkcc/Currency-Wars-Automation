#!/usr/bin/env python3
# Read-only, dependency-free consistency checks for screenshot coaching.
# The assistant prepares the JSON from evidence; no game/OCR/keyboard access.
import argparse
import hashlib
import json
import math
import sys

VERSION = "1.0"
ALLOWED_CHECKS = ("route", "economy", "gear")


class InputError(ValueError):
    pass


def obj(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        raise InputError("%s must be an object" % key)
    return value


def nonempty(parent, key):
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError("%s must be a nonempty string" % key)
    return value.strip()


def boolean(parent, key):
    value = parent.get(key)
    if type(value) is not bool:
        raise InputError("%s must be true or false (not an assumption)" % key)
    return value


def number(parent, key, minimum=0, integer=False):
    value = parent.get(key)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise InputError("%s must be a finite number" % key)
    if integer and type(value) is not int:
        raise InputError("%s must be an integer" % key)
    if value < minimum:
        raise InputError("%s must be >= %s" % (key, minimum))
    return value


def strings(parent, key):
    value = parent.get(key)
    if not isinstance(value, list) or any(
        not isinstance(x, str) or not x.strip() for x in value
    ):
        raise InputError("%s must be a list of nonempty strings" % key)
    return value


def issue(issues, severity, code, message):
    issues.append({"severity": severity, "code": code, "message": message})


def route_check(data, issues, calculations):
    r = obj(data, "route")
    name = nonempty(r, "name")
    goal = nonempty(r, "core_goal")
    level = number(r, "current_level", 1, integer=True)
    proposed = number(r, "planned_level", 1, integer=True)
    roll_level = number(r, "roll_level", 1, integer=True)
    done = boolean(r, "core_goal_complete")
    if proposed < level:
        issue(issues, "BLOCK", "LEVEL_REGRESSION",
              "Planned level cannot be lower than current level.")
    if level > roll_level and not done:
        issue(issues, "REVIEW", "ROUTE_ALREADY_DRIFTED",
              "%s: level %d already exceeds the route's roll level %d; reconcile the route." %
              (name, level, roll_level))
    if proposed > roll_level and not done:
        replacement = r.get("switch")
        required = ("to", "reason", "new_core_goal", "new_roll_level", "buy_priority")
        if not isinstance(replacement, dict) or any(
            x not in replacement for x in required
        ):
            issue(issues, "BLOCK", "ROUTE_CHANGE_UNDECLARED",
                  "Leveling past %d before '%s' is complete requires an explicit alternative route, core goal, buy priority and reason." %
                  (roll_level, goal))
        else:
            try:
                to = nonempty(replacement, "to")
                why = nonempty(replacement, "reason")
                nonempty(replacement, "new_core_goal")
                number(replacement, "new_roll_level", 1, integer=True)
                priority = strings(replacement, "buy_priority")
                if not priority:
                    raise InputError("buy_priority must not be empty for route switch")
                issue(issues, "REVIEW", "ROUTE_SWITCH_REQUIRES_COMPARISON",
                      "Switch from %s to %s for %s: compare whole-team value and the opportunity cost of abandoning '%s'." %
                      (name, to, why, goal))
            except InputError as exc:
                issue(issues, "BLOCK", "INVALID_ROUTE_SWITCH", str(exc))
    if "core_owned_copies" in r or "core_target_stars" in r or "claim_complete" in r:
        copies = number(r, "core_owned_copies", 0, integer=True)
        stars = number(r, "core_target_stars", 2, integer=True)
        claimed = boolean(r, "claim_complete")
        if stars not in (2, 3):
            raise InputError("core_target_stars currently supports only 2 or 3")
        required_copies = 3 if stars == 2 else 9
        calculations["core_copies_missing"] = max(0, required_copies - copies)
        if claimed and copies < required_copies:
            issue(issues, "BLOCK", "UNREACHABLE_STAR_CLAIM",
                  "%s has %d copies; %d-star requires %d, not yet completed." %
                  (goal, copies, stars, required_copies))
    calculations["route"] = name
    calculations["route_goal"] = goal


def economy_check(data, issues, calculations):
    e = obj(data, "economy")
    gold = number(e, "gold", 0, integer=True)
    refresh = number(e, "refresh_cost", 0, integer=True)
    rolls = number(e, "planned_paid_refreshes", 0, integer=True)
    buy = number(e, "planned_buy_cost", 0, integer=True)
    exp = number(e, "planned_exp_cost", 0, integer=True)
    reserve = number(e, "reserve_for_target", 0, integer=True)
    final = boolean(e, "final_battle")
    if refresh == 0 and rolls > 0:
        issue(issues, "BLOCK", "FREE_VS_PAID_UNKNOWN",
              "Paid refreshes with zero listed price: distinguish free refresh count from paid refresh price.")
    spend = rolls * refresh + buy + exp
    remaining = gold - spend
    calculations["economy"] = {"spend": spend, "remaining": remaining,
                               "reserve_for_target": reserve}
    if refresh > 0:
        calculations["economy"]["maximum_paid_refreshes_at_budget"] = max(
            0, (gold - buy - exp - reserve) // refresh
        )
    if remaining < 0:
        issue(issues, "BLOCK", "OVERSPEND",
              "Planned spend %d exceeds observed gold %d." % (spend, gold))
    elif remaining < reserve:
        issue(issues, "BLOCK", "PURCHASE_RESERVE_BREACH",
              "Remaining %d is less than target-card reserve %d." %
              (remaining, reserve))
    if final and remaining >= max(2, refresh + reserve):
        reason = e.get("unspent_reason")
        if not isinstance(reason, str) or not reason.strip():
            issue(issues, "REVIEW", "FINAL_GOLD_UNUSED",
                  "Final battle leaves %d gold: evaluate all currently purchasable upgrades, bench liquidity and alternatives; never assume future interest." %
                  remaining)
    if not final and "unspent_reason" in e and not isinstance(e["unspent_reason"], str):
        raise InputError("unspent_reason must be a string if supplied")


def gear_check(data, issues, calculations):
    g = obj(data, "gear")
    carry = nonempty(g, "main_carry")
    position = nonempty(g, "position")
    if position not in ("front", "back"):
        raise InputError("gear.position must be 'front' or 'back'")
    threshold = number(g, "damage_reduction_threshold_pct", 0)
    if threshold > 100:
        raise InputError("damage_reduction_threshold_pct must be <= 100")
    changed = boolean(g, "team_effect_changed")
    defense = strings(g, "defensive_items_on_carry")
    offense = strings(g, "available_offense_items")
    stacking = nonempty(g, "stacking")
    if stacking not in ("additive", "multiplicative", "maximum", "unknown"):
        raise InputError("stacking must be additive/multiplicative/maximum/unknown")
    sources = g.get("team_dr_excluding_carry_items")
    if not isinstance(sources, list):
        raise InputError("team_dr_excluding_carry_items must be an array")
    values = []
    unverified = []
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            raise InputError("team_dr_excluding_carry_items[%d] must be an object" % i)
        label = nonempty(src, "name")
        pct = number(src, "pct", 0)
        if pct > 100:
            raise InputError("%s.pct must be <= 100" % label)
        scope = nonempty(src, "scope")
        if scope not in ("front", "back", "all"):
            raise InputError("%s.scope must be front/back/all" % label)
        verified = boolean(src, "verified_active")
        if scope in ("all", position):
            if verified:
                values.append(float(pct))
            else:
                unverified.append(label)
    if stacking == "additive":
        dr = min(100.0, sum(values))
    elif stacking == "multiplicative":
        product = 1.0
        for pct in values:
            product *= 1.0 - pct / 100.0
        dr = 100.0 * (1.0 - product)
    elif stacking == "maximum":
        dr = max(values, default=0.0)
    else:
        dr = None
    info = {"main_carry": carry, "verified_sources": len(values),
            "unverified_sources": unverified, "stacking": stacking,
            "threshold_pct": threshold}
    if dr is not None:
        info["team_dr_without_carry_items_pct"] = round(dr, 3)
    calculations["gear"] = info
    if unverified:
        issue(issues, "REVIEW", "TEAM_EFFECT_UNVERIFIED",
              "Unverified buffs (%s) cannot certify a damage-reduction threshold." %
              ", ".join(unverified))
    if stacking == "unknown" and len(values) > 1:
        issue(issues, "REVIEW", "STACKING_UNVERIFIED",
              "Multiple team damage-reduction sources exist, but stacking is unknown; do not assert the 20%% threshold from addition.")
    # A single source at threshold works without stacking assumptions.
    guaranteed = max(values, default=0) >= threshold
    reached = (dr is not None and dr >= threshold and not unverified) or guaranteed
    if defense and reached:
        issue(issues, "REVIEW", "CARRY_GEAR_MARGINAL_VALUE",
              "%s's team reduction already reaches %g%% without its defensive item(s) under the stated stacking rule; compare their survival benefit with available offense gear (%s). Do not auto-unequip." %
              (carry, threshold, ", ".join(offense) if offense else "not yet inspected"))
    elif defense and changed:
        issue(issues, "REVIEW", "CARRY_GEAR_NEEDS_RECHECK",
              "Team effects changed while %s carries defensive gear. Re-evaluate threshold, survival and actual offense alternatives before locking slots." % carry)
    if defense and not offense:
        issue(issues, "REVIEW", "OFFENSE_ALTERNATIVES_UNREAD",
              "Read actual available output gear before declaring the main carry's defensive slot optimal.")


def inspect(data):
    issues, calculations = [], {}
    if not isinstance(data, dict):
        raise InputError("root must be a JSON object")
    snapshot = nonempty(data, "snapshot_id")
    checks = strings(data, "checks")
    if not checks or len(set(checks)) != len(checks) or any(
        check not in ALLOWED_CHECKS for check in checks
    ):
        raise InputError("checks must be a nonempty unique subset of route/economy/gear")
    # Hash proves only which JSON was checked; NOT screenshot correctness.
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    for name, fn in (("route", route_check), ("economy", economy_check),
                     ("gear", gear_check)):
        if name in checks:
            try:
                fn(data, issues, calculations)
            except InputError as exc:
                issue(issues, "BLOCK", "INVALID_" + name.upper() + "_INPUT", str(exc))
    status = "BLOCK" if any(x["severity"] == "BLOCK" for x in issues) else (
        "REVIEW" if issues else "PASS"
    )
    return {"guard_version": VERSION, "snapshot_id": snapshot,
            "input_sha256": digest, "checks_run": checks,
            "status": status, "issues": issues, "calculations": calculations,
            "boundary": "Read-only JSON consistency; not screenshot authentication or a win prediction."}


def main():
    parser = argparse.ArgumentParser(description="Read-only currency-wars decision checker")
    parser.add_argument("--input", default="-", help="JSON path, or - for stdin")
    parser.add_argument("--output", default="-", help="JSON report path, or - for stdout")
    args = parser.parse_args()
    try:
        if args.input == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as handle:
                data = json.load(handle)
        result = inspect(data)
    except (InputError, ValueError, OSError) as exc:
        result = {"guard_version": VERSION, "status": "BLOCK",
                  "issues": [{"severity": "BLOCK", "code": "INVALID_INPUT",
                              "message": str(exc)}],
                  "boundary": "Input rejected: no decision validated."}
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        sys.stdout.write(output)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output)
    return 0 if result["status"] == "PASS" else (1 if result["status"] == "REVIEW" else 2)


if __name__ == "__main__":
    sys.exit(main())
