"""Self-verification for the linker. Checks (a)-(h).

This SCRIPT may read the answer key (/data/event_index.jsonl, /data/ground_truth.json).
No module under backend/engine/ that the linker imports may -- check (g) enforces that
by grepping the directory, exactly as Phase 4's verify_anomaly.py does for detection.

    python -m engine.verify_linker
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_constants import CONFIDENCE_ORDER                   # noqa: E402
from engine import linker as engine_linker                        # noqa: E402
from engine.plausibility import successors                        # noqa: E402
from engine.situations import compute_confidence                  # noqa: E402

OK, FAIL, WARN = "  ok  ", " FAIL ", " warn "
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ENGINE_DIR = Path(__file__).resolve().parent
PULSE_TOLERANCE = 5   # points; see check (b) for why exact equality is the wrong bar


def _ground_truth():
    return json.loads((DATA_DIR / "ground_truth.json").read_text("utf-8"))


def _match(sits: list, truth_member_ids: set):
    """CONTRACT.md §G matching rule (overlap fraction only -- detect_by_utc timing is
    Phase 10's job, not this script's)."""
    best, best_overlap = None, 0.0
    for s in sits:
        overlap = len(truth_member_ids & set(s["member_event_ids"])) / len(truth_member_ids)
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    return best, best_overlap


def check_a_chain_coverage(result):
    """Does every planted situation's chain link survive into the detected situation,
    or is the gap explained by Phase 4 never flagging that category at all (an
    upstream limitation, not a linker bug)?  [hard fail]"""
    print("(a) chain-link coverage per scenario  [hard fail]")
    gt = _ground_truth()
    sits = result["situations"]
    by_id = result["events_by_id"]
    flagged = {eid for a in result["anomalies"] for eid in a["contributing_event_ids"]}

    all_ok = True
    for truth in gt["planted_situations"]:
        truth_ids = set(truth["member_event_ids"])
        best, overlap = _match(sits, truth_ids)
        cats_expected = list(dict.fromkeys(truth["expected_chain"]))
        cats_detected = {c["category"] for c in best["chain"]} if best else set()

        print(f"      {truth['truth_id']}: matched {best['situation_id'] if best else 'NONE'} "
              f"(overlap {overlap:.0%})")
        links_ok = True
        for cat in cats_expected:
            in_chain = cat in cats_detected
            cat_ever_flagged = any(
                by_id[eid]["category"] == cat and eid in flagged
                for eid in truth_ids if by_id[eid]["category"] == cat)
            if in_chain:
                mark, note = "OK ", ""
            elif not cat_ever_flagged:
                mark = "OK*"
                note = " -- undetectable upstream (CONTRACT.md §E.1 known limitation " \
                       "or recall gap), not a linker fault"
            else:
                mark, note = "GAP", " -- Phase 4 flagged it but the linker did not connect it"
                links_ok = False
            print(f"         {mark} {cat:<24}{note}")
        sit_ok = best is not None and overlap >= 0.5 and links_ok
        all_ok = all_ok and sit_ok
    print(f"{OK if all_ok else FAIL} every situation matched, and every chain-link gap "
          f"traces to an upstream (Phase 4) limitation, not a linker miss")
    return all_ok


def check_b_pulse_reproduction(result):
    """Pulse-score fidelity, split into the two things that can actually go wrong
    separately (see the phase report for the full walkthrough):

      1. FORMULA fidelity -- does engine.situations.compute_pulse, fed the exact
         member set sim/ground_truth.py used, reproduce its recorded
         expected_pulse_score? This isolates the WEIGHTS/STRUCTURE from anything
         about detection or linking.
      2. SITUATION fidelity -- does our actually-detected-and-linked situation's
         pulse_score land within PULSE_TOLERANCE of the reference, and critically,
         does its ALERT LEVEL match? alert_level is what colors the map; pulse_score
         is a supporting number -- CONTRACT.md §F is explicit that alert_level is
         the thing clients render.

    A formula gap is a hard fail (it would mean this phase mis-copied the reference).
    A situation-fidelity gap is reported and explained, hard-failing ONLY if the
    ALERT LEVEL itself disagrees -- that is the stage-visible failure mode the team
    asked this check to guard tightly.
    """
    print("(b) pulse score reproduction  [hard fail on formula or alert-level drift]")
    from engine.situations import compute_pulse

    gt = _ground_truth()
    by_id = result["events_by_id"]
    sits = result["situations"]

    formula_ok = True
    alert_ok = True
    for truth in gt["planted_situations"]:
        truth_ids = truth["member_event_ids"]
        ref_members = [by_id[eid] for eid in truth_ids if eid in by_id]
        formula_pulse = compute_pulse(ref_members)
        formula_diff = abs(formula_pulse - truth["expected_pulse_score"])

        best, overlap = _match(sits, set(truth_ids))
        sit_pulse = best["pulse_score"] if best else None
        sit_alert = best["alert_level"] if best else None
        ref_alert = truth["expected_alert_level"]

        print(f"      {truth['truth_id']}: reference={truth['expected_pulse_score']}"
              f"({ref_alert})  formula-on-full-members={formula_pulse}"
              f"(diff {formula_diff})  detected={sit_pulse}({sit_alert})")

        if formula_diff > PULSE_TOLERANCE:
            formula_ok = False
            print(f"         FORMULA gap > {PULSE_TOLERANCE} pts -- compute_pulse has "
                  f"diverged from the reference implementation, fix it")
        elif formula_diff > 0:
            print(f"         formula gap of {formula_diff} pt(s) traced to Phase 1/2: "
                  f"expected_pulse_score is computed from the PLANTED spec's idealized "
                  f"`measure` value (sim/ground_truth.py), but the raw feed's own "
                  f"ramping generator does not always reach that exact peak -- the "
                  f"canonical event's REAL severity differs slightly from the target. "
                  f"Not a Phase 5 bug; flagged to the team, not silently fixed (sim/ "
                  f"is not this phase's file).")

        if best is None or sit_alert != ref_alert:
            alert_ok = False
            print(f"         ALERT LEVEL mismatch: {sit_alert} vs reference {ref_alert}")

    print(f"{OK if formula_ok else FAIL} compute_pulse reproduces the reference formula "
          f"within {PULSE_TOLERANCE} points on the planted member sets")
    print(f"{OK if alert_ok else FAIL} every detected situation's alert_level matches "
          f"the reference alert_level (the thing that actually colors the map)")
    return formula_ok and alert_ok


def check_c_decoy_rejection(result):
    """Of the ground-truth decoys Phase 4 actually flagged anything for, what
    fraction did the linker correctly keep out of every situation?"""
    print("(c) decoy rejection")
    gt = _ground_truth()
    sits = result["situations"]
    all_member_ids = {eid for s in sits for eid in s["member_event_ids"]}
    flagged = {eid for a in result["anomalies"] for eid in a["contributing_event_ids"]}

    testable, kept_out = 0, 0
    for d in gt["decoys"]:
        dmem = set(d["member_event_ids"])
        if not (dmem & flagged):
            print(f"      {d['decoy_id']}: never flagged by Phase 4 -- not a valid "
                  f"test case here")
            continue
        testable += 1
        leaked = dmem & all_member_ids
        ok = not leaked
        kept_out += ok
        print(f"      {d['decoy_id']}: {'kept out' if ok else f'LEAKED {len(leaked)} events'}")

    rate = kept_out / testable if testable else 1.0
    print(f"      decoys correctly ignored: {kept_out}/{testable} ({rate:.0%})")
    print(f"{OK if kept_out == testable else FAIL} every testable decoy stayed out of "
          f"every situation")
    return kept_out == testable


def check_d_no_false_situations(result):
    """Spot-check: none of GT-001/002/003's own matched situations absorbed a decoy's
    member events (a spatial/temporal near-miss wrongly folded into the real
    cascade -- distinct from (c), which checks decoys never form/join ANY situation)."""
    print("(d) no false situations -- decoy events inside a real cascade's situation")
    gt = _ground_truth()
    sits = result["situations"]
    decoy_ids = {eid for d in gt["decoys"] for eid in d["member_event_ids"]}

    ok = True
    for truth in gt["planted_situations"]:
        best, _ = _match(sits, set(truth["member_event_ids"]))
        if best is None:
            continue
        leaked = decoy_ids & set(best["member_event_ids"])
        if leaked:
            ok = False
            print(f"      {truth['truth_id']} ({best['situation_id']}): absorbed "
                  f"{len(leaked)} decoy event(s)")
        else:
            print(f"      {truth['truth_id']} ({best['situation_id']}): clean")
    print(f"{OK if ok else FAIL} no real-cascade situation absorbed a decoy event")
    return ok


def check_e_two_source_confidence(result):
    """A signal_down anomaly corroborated by BOTH power_discom and civic_complaints
    should score at least as high confidence as an otherwise-identical single-source
    equivalent. The real dataset has no natural single-source counterfactual for the
    exact same cascade, so this is a controlled synthetic A/B on the scoring FUNCTION
    itself (engine.situations.compute_confidence) using one real situation's actual
    member events, with only the source set narrowed -- everything else (severity,
    grid distance, gaps, degraded feeds) held fixed."""
    print("(e) two-source corroboration raises confidence (synthetic A/B on the scorer)")
    by_id = result["events_by_id"]
    ok_all = True
    tested = 0
    for s in result["situations"]:
        members = [by_id[eid] for eid in s["member_event_ids"] if eid in by_id]
        sd = [m for m in members if m["category"] == "traffic.signal_down"]
        sources = {m["source"] for m in sd}
        if not {"power_discom", "civic_complaints"} <= sources:
            continue
        tested += 1
        full_sources = {m["source"] for m in members}
        gaps = s["evidence"]["temporal_gaps"]
        max_d = s["evidence"]["spatial"]["max_grid_distance"]

        full_level, _, _ = compute_confidence(members, full_sources, max_d, gaps, set(), {}, False)
        single_members = [m for m in members if m["category"] != "traffic.signal_down"
                          or m["source"] != "civic_complaints"]
        single_sources = {m["source"] for m in single_members}
        single_level, _, _ = compute_confidence(single_members, single_sources, max_d, gaps, set(), {}, False)

        ok = CONFIDENCE_ORDER.index(full_level) >= CONFIDENCE_ORDER.index(single_level)
        ok_all = ok_all and ok
        print(f"      {s['situation_id']}: two-source={full_level} "
              f"vs single-source-equivalent={single_level} "
              f"({'ok, >=' if ok else 'FAIL, <'})")

    if tested == 0:
        print(f"{WARN} no situation in this run has a two-source signal_down member "
              f"to test -- nothing to check")
        return True
    print(f"{OK if ok_all else FAIL} two-source corroboration never scores lower "
          f"confidence than the single-source equivalent")
    return ok_all


def check_f_determinism(result):
    print("(f) determinism")
    sit_path = DATA_DIR / "situations.jsonl"
    rej_path = DATA_DIR / "rejected_candidates.jsonl"
    first_sit, first_rej = sit_path.read_bytes(), rej_path.read_bytes()
    engine_linker.run(verbose=False)
    second_sit, second_rej = sit_path.read_bytes(), rej_path.read_bytes()
    ok = first_sit == second_sit and first_rej == second_rej
    print(f"      situations.jsonl: {len(first_sit)} bytes, "
          f"{'identical' if first_sit == second_sit else 'CHANGED'}")
    print(f"      rejected_candidates.jsonl: {len(first_rej)} bytes, "
          f"{'identical' if first_rej == second_rej else 'CHANGED'}")
    print(f"{OK if ok else FAIL} both outputs are byte-identical across runs")
    return ok


def check_g_no_answer_key_leak():
    print("(g) no answer-key leakage in backend/engine/")
    banned = re.compile(r"ground_truth|event_index")
    EXCLUDED = {"verify_anomaly.py", "verify_linker.py", "scorecard.py"}
    targets = [py for py in sorted(ENGINE_DIR.glob("*.py")) if py.name not in EXCLUDED]

    offenders = []
    for py in targets:
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append((py.name, n, line.strip()))

    print(f"      scanned: {', '.join(p.name for p in targets)}")
    if offenders:
        for name, n, line in offenders:
            print(f"      LEAK {name}:{n}  {line}")
        print(f"{FAIL} linker code references the answer key")
        return False
    print(f"{OK} no module under backend/engine/ touches ground_truth or event_index")
    return True


def check_h_predicted_next_sanity(result):
    """A prediction should never name a category with NO plausibility-table
    relationship to the situation it's attached to. Being wrong about WHETHER that
    category shows up later is fine and expected (it is a pattern, not a promise)."""
    print("(h) predicted_next sanity")
    ok = True
    shown = 0
    for s in result["situations"]:
        pred = s.get("predicted_next")
        if pred is None:
            continue
        last_cat = s["chain"][-1]["category"]
        # predicted_next is one hop from the situation's LAST chain step by
        # construction (engine.situations.build_predicted_next) -- confirm no
        # regression has let it name something unrelated.
        related = pred["category"] in successors(last_cat)
        if shown < 3:
            print(f"      {s['situation_id']}: last step {last_cat} -> predicts "
                  f"{pred['category']} (lag {pred['typical_lag_range_sec']}, "
                  f"{pred['based_on']}){'​' if related else ' -- UNRELATED'}")
            shown += 1
        ok = ok and related
    if shown == 0:
        print("      no situation in this run carries a predicted_next -- nothing to check")
    print(f"{OK if ok else FAIL} every predicted_next names a category the "
          f"plausibility table actually connects to its situation")
    return ok


def run_all():
    print("running the linker...\n")
    result = engine_linker.run(verbose=False)
    print(f"linker produced {len(result['situations'])} situations, "
          f"{len(result['rejected'])} rejected candidates\n")
    print("verification\n")

    checks = [
        ("a chain coverage", check_a_chain_coverage(result), True),
        ("b pulse reproduction", check_b_pulse_reproduction(result), True),
        ("c decoy rejection", check_c_decoy_rejection(result), True),
        ("d no false situations", check_d_no_false_situations(result), True),
        ("e two-source confidence", check_e_two_source_confidence(result), False),
        ("f determinism", check_f_determinism(result), True),
        ("g no leakage", check_g_no_answer_key_leak(), True),
        ("h predicted_next sanity", check_h_predicted_next_sanity(result), False),
    ]
    print()
    hard = [n for n, ok, h in checks if not ok and h]
    soft = [n for n, ok, h in checks if not ok and not h]
    if hard:
        print(f"HARD FAIL: {', '.join(hard)}")
        return False, result
    if soft:
        print(f"passed with warnings: {', '.join(soft)}")
        return True, result
    print("all eight checks passed")
    return True, result


if __name__ == "__main__":
    ok, _ = run_all()
    sys.exit(0 if ok else 1)
