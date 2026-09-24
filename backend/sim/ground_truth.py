"""Writes /data/ground_truth.json -- the answer key Phase 10 scores against.

CONTRACT.md §G. Every member_event_ids entry must be an id that really exists in a raw
feed, which is why this runs after the writers, not before: the writers are what assign
raw_ref, and raw_ref is what the id is derived from.
"""

import json

from contract_constants import alert_level_for
from sim import config
from sim.index import iso
from sim.scenario import expected_pulse


def _confidence(spec) -> float:
    """CONTRACT.md §A confidence rules, as they apply to each feed."""
    if spec.source == "weather_imd":
        return 0.90
    if spec.source == "air_sensors":
        return 0.90                      # planted sensors are calibrated ones
    if spec.source == "civic_complaints":
        return 0.60                      # 0.70 resident x 0.85 landmark-resolved
    if spec.source in ("power_discom", "transit_gtfs"):
        return 0.72                      # 0.90 sensor x 0.80 registry-resolved
    return 0.70


def _members(specs):
    missing = [s for s in specs if not s.event_id]
    if missing:
        raise AssertionError(
            "planted specs produced no event -- ground truth would dangle: "
            + ", ".join(f"{s.category}@{iso(s.start)}" for s in missing[:5])
        )
    return [s.event_id for s in specs]


def build(scenario_name: str, truths, decoys) -> dict:
    planted = []
    for t in truths:
        specs = t["specs"]
        pulse = expected_pulse(specs, [_confidence(s) for s in specs])
        planted.append({
            "truth_id": t["truth_id"],
            "label": t["label"],
            "root_cause_category": t["root_cause_category"],
            "expected_chain": t["expected_chain"],
            "expected_pulse_score": pulse,
            "expected_alert_level": alert_level_for(pulse),
            "expected_zone_cells": t["expected_zone_cells"],
            "member_event_ids": _members(specs),
            "onset_utc": iso(t["onset"]),
            "detect_by_utc": iso(t["detect_by"]),
        })

    out_decoys = []
    for d in decoys:
        out_decoys.append({
            "decoy_id": d["decoy_id"],
            "label": d["label"],
            "member_event_ids": _members(d["specs"]),
            "why_unrelated": d["why_unrelated"],
            "must_not_alert_above": d["must_not_alert_above"],
        })

    return {
        "scenario": scenario_name,
        "description": DESCRIPTIONS.get(scenario_name, scenario_name),
        "generated_utc": iso(config.HISTORY_END),
        "sim_start_utc": iso(config.SIM_START),
        "sim_end_utc": iso(config.SIM_END),
        "planted_situations": planted,
        "decoys": out_decoys,
    }


DESCRIPTIONS = {
    "monsoon_evening": "Evening cloudburst over the walled city during peak bus hours, "
                       "plus an unrelated transformer failure and a garbage fire",
    "calm": "Fourteen days of baseline noise with nothing planted",
}


def write(scenario_name: str, truths, decoys) -> dict:
    gt = build(scenario_name, truths, decoys)
    path = config.DATA_DIR / config.OUTPUT_FILES["ground_truth"]
    path.write_text(json.dumps(gt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return gt
