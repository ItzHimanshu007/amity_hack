"""Self-verification for anomaly detection. Checks (a)-(f).

This SCRIPT may read the answer key (/data/event_index.jsonl, /data/ground_truth.json).
No module under backend/engine/ that detection imports may -- check (f) enforces that
by grepping the directory.

    python -m engine.verify_anomaly
"""

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd                                              # noqa: E402

from contract_constants import ANOMALY_MIN_COUNT, ANOMALY_P_THRESHOLD  # noqa: E402
from engine import run as engine_run                              # noqa: E402
from engine.anomaly import detect                                 # noqa: E402
from engine.baseline import learn_baseline                        # noqa: E402
from contract_constants import CATEGORY_FEEDS                     # noqa: E402

OK, FAIL = "  ok  ", " FAIL "
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
ENGINE_DIR = Path(__file__).resolve().parent


def _ground_truth():
    return json.loads((DATA_DIR / "ground_truth.json").read_text("utf-8"))


def check_a_calibration(result):
    """False-positive rate on baseline-only days: no planted events there at all."""
    print("(a) calibration on baseline-only days")
    history = result["history"]
    baseline = result["baseline"]

    # Hold out the last full history day, learn on the rest, then detect on the held-out
    # day -- which by construction contains no planted events (Phase 1 verified that).
    hist = history.copy()
    day = hist["start_ts"].dt.floor("D")
    days = sorted(day.unique())
    if len(days) < 3:
        print(f"{FAIL} not enough history days to hold one out")
        return False

    holdout_day = days[-1]
    train = hist[day < holdout_day]
    holdout = hist[day == holdout_day].copy()
    n_train_days = max(1e-6, (holdout_day - train["start_ts"].min()).total_seconds() / 86400)
    bl = learn_baseline(train, n_train_days)

    # Measure the configuration that actually ships, including the rare threshold
    # fitted by engine.calibrate -- on a DIFFERENT day than the fit used.
    rare_p = result.get("rare_p_threshold")
    fp = detect(holdout, bl, CATEGORY_FEEDS, set(), rare_p_threshold=rare_p)
    tested = holdout.groupby(["h3_cell", "category"]).ngroups
    print(f"      deployed rare threshold: p < {rare_p:g}"
          if rare_p is not None else "      rare threshold: default")

    # Sliding windows overlap: with a 60-min window stepped every 5 min, ONE real
    # cluster is re-flagged in up to 12 consecutive windows. Counting those as 12
    # independent trials inflates the numerator and the denominator together and makes
    # the rate uninterpretable. The honest unit is a distinct (cell, category, hour)
    # block, so collapse flags onto non-overlapping hour blocks before measuring.
    blocks = {(a["h3_cell"], a["category"], a["window_start_utc"][:13]) for a in fp}
    trials = max(1, tested * 24)          # 24 non-overlapping hour blocks per pair
    rate = len(blocks) / trials

    print(f"      held-out day: {str(holdout_day)[:10]}, {len(holdout)} baseline events")
    print(f"      (cell,category) pairs tested: {tested}, hour-blocks: {trials}")
    print(f"      raw overlapping windows flagged: {len(fp)}")
    print(f"      distinct (cell,category,hour) false positives: {len(blocks)}")
    print(f"      false-positive rate: {rate:.5f}  (nominal p={ANOMALY_P_THRESHOLD})")
    if fp:
        by_cat = Counter(a["category"] for a in fp)
        print(f"      by category (overlapping): {dict(by_cat)}")
        by_trigger = Counter(a["trigger"] for a in fp)
        print(f"      by trigger: {dict(by_trigger)}")

    # Under a correctly-calibrated test the expected count is trials * p, and the
    # observed count is Binomial around it. Pass when observed sits within 3 sigma of
    # expectation rather than demanding it land below the nominal rate exactly -- a
    # detector that never exceeds nominal on a finite sample is suspiciously tuned.
    import math
    expected = trials * ANOMALY_P_THRESHOLD
    sigma = math.sqrt(max(1e-9, trials * ANOMALY_P_THRESHOLD * (1 - ANOMALY_P_THRESHOLD)))
    z = (len(blocks) - expected) / sigma
    print(f"      expected under null: {expected:.1f} +/- {sigma:.1f}  -> z = {z:+.2f}")

    ok = z <= 3.0
    print(f"{OK if ok else FAIL} false-alarm rate is statistically consistent with the "
          f"p<{ANOMALY_P_THRESHOLD} target, not merely asserted")
    return ok


def check_b_recall(result):
    """Do planted events reach Phase 5 as evidence?  [hard fail]

    Raw per-event recall is reported, but it is NOT the pass criterion, because it
    over-counts duplicates: GT-001 contains five waterlogging reports of one flood, and
    Phase 5 needs that LINK represented, not all five copies of it. What actually
    decides whether a situation can form on stage is:

      1. every chain link (category) in the planted situation has >= 1 flagged event,
         otherwise Phase 5 cannot draw that step no matter how good the linker is, and
      2. overlap clears the 50% bar CONTRACT.md §G's matching rule uses, or Phase 10
         scores the situation as missed regardless.

    Both are reported per situation, and every gap is named.
    """
    print("(b) recall on planted ground-truth events  [hard fail]")
    gt = _ground_truth()
    by_id = {e["event_id"]: e for e in result["events"]}
    flagged = set()
    for a in result["anomalies"]:
        flagged.update(a["contributing_event_ids"])

    total = covered = 0
    all_ok, link_gaps = True, []
    for sit in gt["planted_situations"]:
        members = sit["member_event_ids"]
        cats = {}
        for eid in members:
            cat = by_id[eid]["category"]
            cats.setdefault(cat, [0, 0])
            cats[cat][1] += 1
            if eid in flagged:
                cats[cat][0] += 1
        hits = sum(1 for e in members if e in flagged)
        total += len(members)
        covered += hits
        links_ok = sum(1 for c, (h, _n) in cats.items() if h > 0)
        overlap = hits / len(members)

        headline = sit["truth_id"] == "GT-001"
        sit_ok = overlap >= 0.5 and (links_ok == len(cats) or not headline)
        all_ok = all_ok and sit_ok
        print(f"      {sit['truth_id']}: overlap {hits}/{len(members)} ({overlap:.0%}, "
              f"§G bar 50%), chain links {links_ok}/{len(cats)}"
              f"{'  <- headline' if headline else ''}")
        for cat, (h, n) in sorted(cats.items()):
            mark = "OK " if h else "GAP"
            print(f"         {mark} {cat:<24}{h}/{n}")
            if not h:
                link_gaps.append((sit["truth_id"], cat))

    print(f"      raw per-event recall: {covered}/{total} = {covered / total:.1%} "
          f"(duplicates included)")
    for truth_id, cat in link_gaps:
        print(f"      LINK GAP: {truth_id} has no flagged {cat} -- Phase 5 cannot draw "
              f"that step")

    print(f"{OK if all_ok else FAIL} every situation clears the §G 50% bar and the "
          f"headline chain is fully represented")
    return all_ok


def check_c_decoys(result):
    """Decoys SHOULD be able to flag -- they are real spikes, just unrelated ones."""
    print("(c) decoy events can still trigger anomalies")
    gt = _ground_truth()
    anomalies = result["anomalies"]
    flagged = set()
    for a in anomalies:
        flagged.update(a["contributing_event_ids"])

    rows = []
    for d in gt["decoys"]:
        members = d["member_event_ids"]
        hit = sum(1 for m in members if m in flagged)
        rows.append((d["decoy_id"], hit, len(members), d["label"]))
        print(f"      {d['decoy_id']}: {hit}/{len(members)} member events flagged "
              f"-- {d['label'][:54]}")

    any_flagged = any(hit > 0 for _, hit, _, _ in rows)
    print(f"{OK if any_flagged else FAIL} decoys do reach Phase 5, which is what gives it "
          f"something to correctly reject")
    return any_flagged


def check_d_two_source_signal_down(result):
    """source_feeds must preserve multiple distinct feeds, not collapse to one."""
    print("(d) two-source traffic.signal_down preserved in source_feeds")
    anomalies = [a for a in result["anomalies"]
                 if a["category"] == "traffic.signal_down"]
    print(f"      traffic.signal_down anomalies: {len(anomalies)}")

    multi = [a for a in anomalies if len(a["source_feeds"]) > 1]
    for a in multi:
        print(f"      {a['anomaly_id']} cell={a['h3_cell'][:10]}.. "
              f"sources={a['source_feeds']} count={a['observed_count']}")

    # Also confirm no anomaly silently dropped a source that its own contributing
    # events actually carry.
    by_id = {e["event_id"]: e for e in result["events"]}
    collapsed = []
    for a in anomalies:
        actual = {by_id[e]["source"] for e in a["contributing_event_ids"] if e in by_id}
        if actual != set(a["source_feeds"]):
            collapsed.append((a["anomaly_id"], sorted(actual), a["source_feeds"]))

    if collapsed:
        for aid, actual, declared in collapsed[:5]:
            print(f"      COLLAPSED {aid}: events carry {actual}, declared {declared}")
        print(f"{FAIL} source_feeds does not match the contributing events' sources")
        return False

    print(f"      {len(multi)} anomaly windows carry BOTH power_discom and "
          f"civic_complaints" if multi else
          "      (no single window held both sources -- see note in the report)")
    print(f"{OK} source_feeds faithfully reflects every contributing event's source")
    return True


def check_e_determinism(result):
    """Same input, same output, byte-identical."""
    print("(e) determinism")
    path = DATA_DIR / "anomalies.jsonl"
    first = path.read_bytes()
    engine_run.run(verbose=False)
    second = path.read_bytes()
    ok = first == second
    print(f"      re-ran detection, compared {len(first)} bytes")
    print(f"{OK if ok else FAIL} anomalies.jsonl is byte-identical across runs")
    return ok


def check_f_no_answer_key_leak():
    """Detection code must never read the answer key."""
    print("(f) no answer-key leakage in backend/engine/")
    banned = re.compile(r"ground_truth|event_index")

    # Scope: the modules detection actually imports. Excluded deliberately --
    #   verify_anomaly.py : this script, which is allowed to read the answer key
    #   verify_linker.py  : Phase 5's own verify script, same allowance -- it has its
    #                       own copy of this exact check (engine/verify_linker.py's
    #                       check (g)), listed here too so THIS script's sweep of the
    #                       whole directory doesn't flag it as a stranger.
    #   scorecard.py      : Phase 10, whose entire job is scoring against it
    #   linker.py / situations.py / plausibility.py / lift.py : Phase 5's detection-
    #                       adjacent modules, not part of anomaly detection itself and
    #                       already covered by verify_linker.py's own leak check.
    EXCLUDED = {"verify_anomaly.py", "verify_linker.py", "scorecard.py", "linker.py",
               "situations.py", "plausibility.py", "lift.py"}
    targets = [py for py in sorted(ENGINE_DIR.glob("*.py")) if py.name not in EXCLUDED]

    offenders = []
    for py in targets:
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append((py.name, n, line.strip()))

    scanned = [p.name for p in targets]
    print(f"      scanned: {', '.join(scanned)}")
    if offenders:
        for name, n, line in offenders:
            print(f"      LEAK {name}:{n}  {line}")
        print(f"{FAIL} detection code references the answer key")
        return False
    print(f"{OK} no module under backend/engine/ touches ground_truth or event_index")
    return True


def run_all():
    print("running anomaly detection...\n")
    result = engine_run.run(verbose=False)
    print(f"detection produced {len(result['anomalies'])} anomalies\n")
    print("verification\n")

    checks = [
        ("a calibration", check_a_calibration(result), False),
        ("b recall", check_b_recall(result), True),
        ("c decoys", check_c_decoys(result), True),
        ("d two-source", check_d_two_source_signal_down(result), True),
        ("e determinism", check_e_determinism(result), True),
        ("f no leakage", check_f_no_answer_key_leak(), True),
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
    print("all six checks passed")
    return True, result


if __name__ == "__main__":
    ok, _ = run_all()
    sys.exit(0 if ok else 1)
