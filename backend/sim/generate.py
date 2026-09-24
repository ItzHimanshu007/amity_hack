"""CLI entry: regenerates every file under /data from one seed.

    python -m sim.generate                     # monsoon_evening, seed 42
    python -m sim.generate --scenario calm     # baseline history only
    python -m sim.generate --seed 7

Order matters: registries before the feeds that reference their ids, writers before
ground truth (the writers are what assign raw_ref, and the ids derive from raw_ref).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.zones import assert_bbox_cell_count                      # noqa: E402
from sim import (air_feed, complaints_feed, config, ground_truth,     # noqa: E402
                 power_feed, registries, scenario, transit_feed, weather_feed)

SCENARIOS = ("monsoon_evening", "calm")


def run(scenario_name: str = "monsoon_evening", seed: int = config.SEED) -> dict:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    scenario.reset_spec_counter()

    n_cells = assert_bbox_cell_count()
    print(f"  zones           {n_cells} res-8 cells in the bbox")

    feeder_reg = registries.build_feeder_registry(seed)
    stop_reg = registries.build_stop_registry(seed)
    (config.DATA_DIR / config.OUTPUT_FILES["feeder_registry"]).write_text(
        json.dumps(feeder_reg, indent=2) + "\n", encoding="utf-8")
    (config.DATA_DIR / config.OUTPUT_FILES["stop_registry"]).write_text(
        json.dumps(stop_reg, indent=2) + "\n", encoding="utf-8")
    print(f"  registries      {len(feeder_reg)} feeders, {len(stop_reg)} stops")

    world = scenario.build_world(feeder_reg, stop_reg, seed)

    specs = scenario.baseline_specs(world, seed)
    n_baseline = len(specs)
    truths, decoys = [], []
    if scenario_name != "calm":
        planted, truths = scenario.planted_specs(world, seed)
        decoyed, decoys = scenario.decoy_specs(world, seed)
        specs += planted + decoyed
    print(f"  specs           {n_baseline} baseline, {len(specs) - n_baseline} planted/decoy")

    counts, index = {}, []
    for name, writer in (("weather_imd", weather_feed),
                         ("air_sensors", air_feed),
                         ("power_discom", power_feed),
                         ("transit_gtfs", transit_feed),
                         ("civic_complaints", complaints_feed)):
        n, idx = writer.write(world, specs, seed)
        counts[name] = n
        index += idx
        print(f"  {name:<16}{n:>7} records, {len(idx):>6} events")

    index.sort(key=lambda e: (e["start_utc"], e["event_id"]))
    with (config.DATA_DIR / config.OUTPUT_FILES["event_index"]).open(
            "w", encoding="utf-8") as fh:
        for e in index:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    gt = ground_truth.write(scenario_name, truths, decoys)
    print(f"  ground_truth    {len(gt['planted_situations'])} planted, "
          f"{len(gt['decoys'])} decoys")

    return {"counts": counts, "index": index, "ground_truth": gt, "world": world}


def main():
    ap = argparse.ArgumentParser(description="Generate NagarNaadi synthetic feeds.")
    ap.add_argument("--scenario", default="monsoon_evening", choices=SCENARIOS)
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()

    print(f"nagar naadi -- generating '{args.scenario}' with seed {args.seed}")
    print(f"  history         {config.HISTORY_START:%Y-%m-%d %H:%M}Z -> "
          f"{config.HISTORY_END:%Y-%m-%d %H:%M}Z ({config.HISTORY_DAYS} days)")
    print(f"  demo window     {config.SIM_START:%Y-%m-%d %H:%M}Z -> "
          f"{config.SIM_END:%H:%M}Z")
    run(args.scenario, args.seed)

    if not args.skip_verify:
        print()
        from sim import verify
        if not verify.run_all():
            sys.exit(1)


if __name__ == "__main__":
    main()
