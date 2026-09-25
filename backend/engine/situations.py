"""Phase 5: builds the situation object -- alert_level, event chain, evidence,
confidence. See CONTRACT.md F.

Pure object-builders. No file I/O, no clustering, no candidate generation -- that is
engine/linker.py's job. Everything here takes an already-decided CLUSTER (a list of
episodes, engine.linker's merged-anomaly evidence nodes) and turns it into the object
shapes CONTRACT.md fixes.
"""

import hashlib
from datetime import datetime, timezone

from contract_constants import (CONFIDENCE_HIGH_MAX_GAP_SEC,
                                CONFIDENCE_HIGH_MIN_SOURCES,
                                CONFIDENCE_LOW_MEMBER_CONFIDENCE,
                                CONFIDENCE_MED_MIN_SOURCES, CONFIDENCE_ORDER,
                                LIFT_MIN_COOCCURRENCES_TO_CITE, LIFT_STRONG,
                                STANDALONE_MAX_CONFIDENCE, alert_level_for)
from engine.lift import lift_for
from engine.plausibility import successors
from ingest.normalize import from_iso_z
from ingest.zones import grid_distance, zone_label

# --- plain-language templates (DESIGN.md "Plain-language vocabulary") ---------------

_CATEGORY_EN = {
    "weather.rain": "Heavy rain", "weather.heat": "Extreme heat", "air.pm25": "Poor air",
    "power.outage": "A power cut", "traffic.signal_down": "A signal not working",
    "drain.overflow": "Overflowing drains", "complaint.waterlogging": "Waterlogging",
    "complaint.garbage": "Uncleared garbage", "complaint.streetlight": "A dark streetlight",
    "complaint.road_damage": "Road damage", "complaint.smoke": "Smoke or burning",
}
_CATEGORY_HI = {
    "weather.rain": "तेज़ बारिश", "weather.heat": "अत्यधिक गर्मी", "air.pm25": "खराब हवा",
    "power.outage": "बिजली कटौती", "traffic.signal_down": "बंद सिग्नल",
    "drain.overflow": "उफनते नाले", "complaint.waterlogging": "जलभराव",
    "complaint.garbage": "कचरा", "complaint.streetlight": "बंद स्ट्रीटलाइट",
    "complaint.road_damage": "सड़क खराब", "complaint.smoke": "धुआँ",
}

_START_EN = {
    "weather.rain": "Heavy rain started", "weather.heat": "Extreme heat set in",
    "air.pm25": "Air quality dropped", "power.outage": "Power went out",
    "traffic.signal_down": "A traffic signal went dark",
    "drain.overflow": "Storm drains started overflowing", "complaint.waterlogging": "Residents reported waterlogging",
    "complaint.garbage": "Residents reported uncleared garbage",
    "complaint.streetlight": "A streetlight was reported dark",
    "complaint.road_damage": "Residents reported road damage",
    "complaint.smoke": "Residents reported smoke",
}
_START_HI = {
    "weather.rain": "तेज़ बारिश शुरू हुई", "weather.heat": "अत्यधिक गर्मी शुरू हुई",
    "air.pm25": "हवा की गुणवत्ता गिरी", "power.outage": "बिजली चली गई",
    "traffic.signal_down": "एक ट्रैफिक सिग्नल बंद हो गया",
    "drain.overflow": "नाले उफनने लगे", "complaint.waterlogging": "निवासियों ने जलभराव की शिकायत की",
    "complaint.garbage": "निवासियों ने कचरा न उठने की शिकायत की",
    "complaint.streetlight": "एक स्ट्रीटलाइट बंद बताई गई",
    "complaint.road_damage": "निवासियों ने सड़क खराब होने की शिकायत की",
    "complaint.smoke": "निवासियों ने धुएं की शिकायत की",
}
_THEN_EN = {
    "weather.rain": "heavy rain started", "weather.heat": "extreme heat set in",
    "air.pm25": "air quality dropped", "power.outage": "power went out",
    "traffic.signal_down": "a traffic signal went dark",
    "drain.overflow": "storm drains started overflowing", "complaint.waterlogging": "residents reported waterlogging",
    "complaint.garbage": "residents reported uncleared garbage",
    "complaint.streetlight": "a streetlight was reported dark",
    "complaint.road_damage": "residents reported road damage",
    "complaint.smoke": "residents reported smoke",
}
_THEN_HI = {
    "weather.rain": "तेज़ बारिश शुरू हुई", "weather.heat": "अत्यधिक गर्मी शुरू हुई",
    "air.pm25": "हवा की गुणवत्ता गिरी", "power.outage": "बिजली चली गई",
    "traffic.signal_down": "एक ट्रैफिक सिग्नल बंद हो गया",
    "drain.overflow": "नाले उफनने लगे", "complaint.waterlogging": "जलभराव की शिकायत आई",
    "complaint.garbage": "कचरा न उठने की शिकायत आई",
    "complaint.streetlight": "स्ट्रीटलाइट बंद बताई गई",
    "complaint.road_damage": "सड़क खराब होने की शिकायत आई",
    "complaint.smoke": "धुएं की शिकायत आई",
}


def _minutes(sec: int) -> int:
    return max(1, round(sec / 60))


def _area_phrase(distance: int) -> tuple:
    if distance == 0:
        return "at the same spot", "उसी जगह"
    if distance == 1:
        return "one area away", "एक क्षेत्र दूर"
    return "a couple of areas away", "कुछ क्षेत्र दूर"


def build_chain(episodes: list) -> list:
    """CONTRACT.md §F `chain`: one row per episode (one evidence node), earliest first.

    `episodes` are engine.linker's merged-anomaly nodes, each already carrying a
    `rep_event` (the contributing event with the earliest start_utc -- the one that
    "opened" the cluster, mirroring §A's raw_ref convention) and `events` (every
    canonical event dict for its contributing_event_ids, for count-aware wording).
    """
    ordered = sorted(episodes, key=lambda ep: (ep["rep_event"]["start_utc"], ep["h3_cell"]))
    chain = []
    prev = None
    for i, ep in enumerate(ordered, start=1):
        rep = ep["rep_event"]
        n = len(ep["events"])
        if prev is None:
            text_en = _START_EN[ep["category"]]
            text_hi = _START_HI[ep["category"]]
            if n > 1:
                text_en += f" ({n} reports)"
                text_hi += f" ({n} रिपोर्ट)"
        else:
            gap_sec = int(round(
                (ep["rep_t"] - prev["rep_t"])))
            mins = _minutes(gap_sec)
            dist = grid_distance(prev["h3_cell"], ep["h3_cell"])
            area_en, area_hi = _area_phrase(dist)
            then_en, then_hi = _THEN_EN[ep["category"]], _THEN_HI[ep["category"]]
            count_en = f", {n} reports" if n > 1 else ""
            count_hi = f", {n} रिपोर्ट" if n > 1 else ""
            text_en = f"{mins} minutes later, {then_en} {area_en}{count_en}"
            text_hi = f"{mins} मिनट बाद, {area_hi} {then_hi}{count_hi}"
        chain.append({
            "step": i,
            "event_id": rep["event_id"],
            "t_utc": rep["start_utc"],
            "category": ep["category"],
            "h3_cell": ep["h3_cell"],
            "text_en": text_en,
            "text_hi": text_hi,
        })
        prev = ep
    return chain


def build_evidence(episodes: list, chain: list, lift_table: dict, used_pair) -> dict:
    """CONTRACT.md §F `evidence`. `used_pair` is the (cause, effect) that scored
    highest among the links actually used to connect this cluster, or None for a
    standalone (single-episode) situation."""
    cells = sorted({ep["h3_cell"] for ep in episodes})
    max_d = 0
    for i, a in enumerate(cells):
        for b in cells[i + 1:]:
            max_d = max(max_d, grid_distance(a, b))

    if len(cells) <= 1:
        note_en, note_hi = "All reports came from the same area", "सभी रिपोर्ट एक ही क्षेत्र से आईं"
    elif max_d <= 1:
        note_en = f"All {len(cells)} areas involved are adjacent to each other"
        note_hi = f"सभी {len(cells)} क्षेत्र आपस में सटे हुए हैं"
    elif max_d <= 2:
        note_en = f"The {len(cells)} areas involved are close but not adjacent"
        note_hi = f"शामिल {len(cells)} क्षेत्र पास हैं लेकिन सटे नहीं हैं"
    else:
        note_en = f"Reports span {len(cells)} areas across the wider zone"
        note_hi = f"रिपोर्ट व्यापक क्षेत्र के {len(cells)} इलाकों में फैली हैं"

    gaps = []
    for a, b in zip(chain, chain[1:]):
        ta = datetime.strptime(a["t_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        tb = datetime.strptime(b["t_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        gaps.append({"from_event_id": a["event_id"], "to_event_id": b["event_id"],
                     "gap_sec": int((tb - ta).total_seconds())})

    if used_pair is not None:
        row = lift_for(lift_table, used_pair[0], used_pair[1])
    elif len(episodes) >= 2:
        # Fallback: report the strongest pair actually present in the cluster's own
        # category set, even if it wasn't the specific edge that connected it.
        cats = {ep["category"] for ep in episodes}
        best = None
        for c1 in cats:
            for c2 in successors(c1):
                if c2 in cats:
                    row = lift_for(lift_table, c1, c2)
                    if best is None or row["value"] > best["value"]:
                        best = row
        row = best or lift_for(lift_table, chain[0]["category"], chain[0]["category"])
    else:
        # Standalone: no second link yet. Report against the most plausible next
        # category so the card still says something informative rather than nothing.
        cat = episodes[0]["category"]
        nxt = successors(cat)
        row = (lift_for(lift_table, cat, nxt[0]) if nxt
               else lift_for(lift_table, cat, cat))

    if row["cooccurrences"] >= LIFT_MIN_COOCCURRENCES_TO_CITE and row["value"] >= LIFT_STRONG:
        hrs = round(1 / row["observed_rate_per_hour"]) if row["observed_rate_per_hour"] > 0 else None
        lift_note = (f"These normally appear together about once every {hrs} hours here"
                     if hrs else "These appear together more often than chance here")
    elif row["cooccurrences"] >= LIFT_MIN_COOCCURRENCES_TO_CITE:
        lift_note = "These appear together about as often as chance would predict here"
    else:
        lift_note = "Too few past cases here to say how unusual this combination is"

    return {
        "spatial": {"h3_cells": cells, "cells_involved": len(cells),
                    "max_grid_distance": max_d, "note_en": note_en, "note_hi": note_hi},
        "temporal_gaps": gaps,
        "lift": {"value": row["value"], "pair": row["pair"], "window_sec": row["window_sec"],
                 "baseline_rate_per_hour": row["baseline_rate_per_hour"],
                 "observed_rate_per_hour": row["observed_rate_per_hour"],
                 "note_en": lift_note},
    }


def compute_confidence(member_events: list, sources: set, max_grid_distance: int,
                       temporal_gaps: list, degraded_feeds: set, feed_ages: dict,
                       standalone: bool):
    """CONTRACT.md §F confidence_level rule, plus the §E.1 stale-feed degradation
    (Phase 5 brief step 6/10) and the standalone cap (contract_constants
    STANDALONE_MAX_CONFIDENCE). Returns (level, reason_en, reason_hi)."""
    max_gap = max((g["gap_sec"] for g in temporal_gaps), default=0)
    n_sources = len(sources)
    low_conf_member = any(e["confidence"] < CONFIDENCE_LOW_MEMBER_CONFIDENCE for e in member_events)

    if (n_sources >= CONFIDENCE_HIGH_MIN_SOURCES and max_grid_distance <= 1
            and max_gap <= CONFIDENCE_HIGH_MAX_GAP_SEC):
        level = "high"
        reason_en = f"{n_sources} separate feeds, all within one area, in the expected order"
        reason_hi = f"{n_sources} अलग-अलग फीड, एक ही क्षेत्र में, अपेक्षित क्रम में"
    elif low_conf_member or max_grid_distance == 2 or n_sources < CONFIDENCE_MED_MIN_SOURCES:
        level = "low"
        if low_conf_member:
            reason_en, reason_hi = ("At least one report is low-confidence on its own",
                                    "कम से कम एक रिपोर्ट खुद अविश्वसनीय है")
        elif max_grid_distance == 2:
            reason_en, reason_hi = ("The areas involved are close but not adjacent",
                                    "शामिल क्षेत्र पास हैं लेकिन सटे नहीं हैं")
        else:
            reason_en, reason_hi = ("Only one feed reported this",
                                    "इसकी रिपोर्ट केवल एक फीड ने की")
    else:
        level = "med"
        if n_sources >= CONFIDENCE_MED_MIN_SOURCES and max_gap > CONFIDENCE_HIGH_MAX_GAP_SEC:
            reason_en = f"{n_sources} feeds agree, but the reports are more than 30 minutes apart"
            reason_hi = f"{n_sources} फीड सहमत हैं, पर रिपोर्ट 30 मिनट से ज़्यादा अलग हैं"
        else:
            reason_en = f"{n_sources} separate feeds agree on this"
            reason_hi = f"{n_sources} अलग-अलग फीड इस पर सहमत हैं"

    if standalone:
        order = CONFIDENCE_ORDER
        if order.index(level) > order.index(STANDALONE_MAX_CONFIDENCE):
            level = STANDALONE_MAX_CONFIDENCE
            reason_en = "A single strong signal with no second report to corroborate it yet"
            reason_hi = "एक मज़बूत संकेत, अभी तक दूसरी पुष्टि रिपोर्ट के बिना"

    relevant_degraded = degraded_feeds & sources if degraded_feeds else set()
    # A situation is only degraded by a feed that's stale AND relevant to one of its
    # OWN member categories -- a stale drain feed says nothing about a smoke report.
    if relevant_degraded:
        order = CONFIDENCE_ORDER
        idx = max(0, order.index(level) - 1)
        level = order[idx]
        feed = sorted(relevant_degraded)[0]
        age = feed_ages.get(feed)
        age_txt = f"{age // 60} min" if age else "a while"
        reason_en = f"{feed.replace('_', ' ')} feed stale {age_txt}, confidence lowered"
        reason_hi = f"{feed.replace('_', ' ')} फीड {age_txt} से पुरानी है, भरोसा कम किया गया"

    return level, reason_en, reason_hi


def build_headline(chain: list) -> tuple:
    """One plain sentence. Built from the first and last chain step, per DESIGN.md's
    "what a resident would say" rule -- never the category id, never "correlation"."""
    root = chain[0]
    last = chain[-1]
    root_label = _CATEGORY_EN[root["category"]]
    root_label_hi = _CATEGORY_HI[root["category"]]

    _EFFECT_EN = {
        "complaint.waterlogging": "is causing waterlogging",
        "power.outage": "has cut power",
        "traffic.signal_down": "has left a signal dark",
        "drain.overflow": "is overflowing the drains",
        "complaint.road_damage": "has damaged the road",
        "complaint.streetlight": "has left a streetlight dark",
        "air.pm25": "is worsening the air",
    }
    _EFFECT_HI = {
        "complaint.waterlogging": "से जलभराव हो रहा है",
        "power.outage": "से बिजली गई है",
        "traffic.signal_down": "से सिग्नल बंद है",
        "drain.overflow": "से नाले उफन रहे हैं",
        "complaint.road_damage": "से सड़क खराब हुई है",
        "complaint.streetlight": "से स्ट्रीटलाइट बंद है",
        "air.pm25": "से हवा खराब हो रही है",
    }

    if len(chain) == 1 or last["category"] == root["category"]:
        return f"{root_label} reported near {{ZONE}}", f"{{ZONE}} के पास {root_label_hi} की सूचना"

    effect_en = _EFFECT_EN.get(last["category"], f"is linked to {_CATEGORY_EN[last['category']].lower()}")
    effect_hi = _EFFECT_HI.get(last["category"], f"{_CATEGORY_HI[last['category']]} से जुड़ा है")
    return f"{root_label} near {{ZONE}} {effect_en}", f"{{ZONE}} के पास {root_label_hi} {effect_hi}"


def build_zone(episodes: list, all_events_by_id: dict) -> dict:
    """CONTRACT.md §C zone label rule: label from the highest-severity cell, count
    of other cells appended."""
    cells = sorted({ep["h3_cell"] for ep in episodes})
    best_cell, best_sev = cells[0], -1.0
    lat_sum = lon_sum = 0.0
    n = 0
    for ep in episodes:
        for e in ep["events"]:
            lat_sum += e["lat"]
            lon_sum += e["lon"]
            n += 1
            if e["severity"] > best_sev:
                best_sev, best_cell = e["severity"], ep["h3_cell"]
    label_en, label_hi = zone_label(best_cell)
    extra = len(cells) - 1
    if extra > 0:
        label_en = f"{label_en} + {extra} nearby area{'s' if extra > 1 else ''}"
        label_hi = f"{label_hi} + {extra} और क्षेत्र"
    return {
        "label_en": label_en, "label_hi": label_hi, "h3_cells": cells,
        "centroid": {"lat": round(lat_sum / n, 5) if n else 0.0,
                     "lon": round(lon_sum / n, 5) if n else 0.0},
    }


def compute_pulse(member_events: list) -> int:
    """CONTRACT.md §F pulse formula -- MUST match backend/sim/scenario.expected_pulse,
    the reference Phase 1 validated GT-001/002/003 (73/76/42) against. Do not diverge:
    same three terms, same weights, computed over the situation's own member events."""
    if not member_events:
        return 0
    sev = max(e["severity"] for e in member_events)
    conf = sum(e["confidence"] for e in member_events) / len(member_events)
    feeds = min(len({e["source"] for e in member_events}), 3) / 3
    return round((sev * 0.6 + conf * 0.2 + feeds * 0.2) * 100)


def situation_id_for(anomaly_ids: list) -> str:
    digest = hashlib.sha1("|".join(sorted(anomaly_ids)).encode()).hexdigest()
    return f"SIT-{digest[:6]}"


def build_situation(episodes: list, all_events_by_id: dict, lift_table: dict,
                    used_pair, degraded_feeds: set, feed_ages: dict,
                    created_utc: str, standalone: bool) -> dict:
    """Assembles one full CONTRACT.md §F Situation from a cluster of episodes.

    `episodes` are expected to already be RESOLVED (engine.linker.resolve_cluster):
    pruned to their own evidence and carrying `anchor_event_id`, the specific event
    that earned this episode its place in the cluster -- used as the chain step's
    event/timestamp so the narrative points at the event that actually mattered, not
    whichever one happens to be earliest. Falls back to the episode's own earliest
    event when `anchor_event_id` is absent, so a caller that skips resolve_cluster
    (a unit test, say) still gets a sane default.
    """
    for ep in episodes:
        ep["events"] = [all_events_by_id[eid] for eid in ep["contributing_event_ids"]
                        if eid in all_events_by_id]
        anchor_id = ep.get("anchor_event_id")
        ep["rep_event"] = (all_events_by_id[anchor_id] if anchor_id in all_events_by_id
                           else min(ep["events"], key=lambda e: (e["start_utc"], e["event_id"])))
        ep["rep_t"] = from_iso_z(ep["rep_event"]["start_utc"]).timestamp()

    member_events = []
    seen = set()
    for ep in episodes:
        for e in ep["events"]:
            if e["event_id"] not in seen:
                seen.add(e["event_id"])
                member_events.append(e)

    chain = build_chain(episodes)
    zone = build_zone(episodes, all_events_by_id)
    evidence = build_evidence(episodes, chain, lift_table, used_pair)
    sources = {e["source"] for e in member_events}
    level, reason_en, reason_hi = compute_confidence(
        member_events, sources, evidence["spatial"]["max_grid_distance"],
        evidence["temporal_gaps"], degraded_feeds, feed_ages, standalone)

    pulse = compute_pulse(member_events)
    alert = alert_level_for(pulse)
    is_decoy = False   # see engine/linker.py module docstring: our own judgment of
                        # coincidence is expressed as a REJECTED CANDIDATE, never as a
                        # Situation the linker itself chose to build.
    if is_decoy and alert in ("orange", "red"):
        alert = "yellow"

    headline_en, headline_hi = build_headline(chain)
    # The zone label may already say "Near X" / "X के पास"; the headline template says
    # "near {ZONE}" itself, so drop the label's own "near" rather than doubling it.
    label_en = zone["label_en"]
    label_en = label_en[5:] if label_en.startswith("Near ") else label_en
    label_hi = zone["label_hi"].removesuffix(" के पास")
    headline_en = headline_en.replace("{ZONE}", label_en)
    headline_hi = headline_hi.replace("{ZONE}", label_hi)

    closed = all(e.get("end_utc") for e in member_events)
    anomaly_ids = sorted({aid for ep in episodes for aid in ep["anomaly_ids"]})
    predicted_next = build_predicted_next(episodes, lift_table, chain[-1]["category"])

    return {
        "situation_id": situation_id_for(anomaly_ids),
        "created_utc": created_utc,
        "updated_utc": created_utc,
        "status": "closed" if closed else "active",
        "pulse_score": pulse,
        "alert_level": alert,
        "headline_en": headline_en,
        "headline_hi": headline_hi,
        "zone": zone,
        "member_event_ids": sorted(e["event_id"] for e in member_events),
        "chain": chain,
        "evidence": evidence,
        "confidence_level": level,
        "confidence_reason_en": reason_en,
        "confidence_reason_hi": reason_hi,
        "is_decoy": is_decoy,
        "predicted_next": predicted_next,
        # engine-internal, stripped before write if a consumer wants a bare §F object;
        # kept here because verify_linker.py and Phase 6 both find it useful.
        "_anomaly_ids": anomaly_ids,
    }


def build_predicted_next(episodes: list, lift_table: dict, last_category: str = None) -> dict:
    """Phase 5 brief step 9: one predictive line, one hop only. Looks at the LAST
    chain step's category, and whether the plausibility table names something that
    commonly follows it which hasn't shown up in this cluster yet.

    Worded as a pattern, not a certainty (DESIGN.md: causal language is reserved for
    the chain, and even there it is temporal). Returns None when there is nothing to
    say -- either no plausible successor, or one that already fired, or one the
    history never saw the cause category do at all (nothing to base a lag range on).
    """
    # The chain's own last step, when given: two episodes can share a timestamp (a
    # feeder trip emits power.outage and traffic.signal_down in one record), and the
    # prediction must hang off the step the chain actually shows last.
    last_cat = last_category or max(episodes, key=lambda ep: ep["rep_t"])["category"]
    present = {ep["category"] for ep in episodes}
    candidates = [c for c in successors(last_cat) if c not in present]
    if not candidates:
        return None

    best_cat, best_row = None, None
    for cat in candidates:
        row = lift_for(lift_table, last_cat, cat)
        if row["n_cause"] == 0:
            continue
        if best_row is None or row["value"] > best_row["value"]:
            best_cat, best_row = cat, row
    if best_cat is None:
        return None

    return {
        "category": best_cat,
        "typical_lag_range_sec": [best_row["min_gap_sec"], best_row["max_gap_sec"]],
        "based_on": f"{best_row['cooccurrences']} historical co-occurrences",
    }


def rejected_id_for(anomaly_ids) -> str:
    digest = hashlib.sha1("|".join(sorted(anomaly_ids)).encode()).hexdigest()
    return f"REJ-{digest[:8]}"


def build_rejected_candidate(ep_a: dict, ep_b: dict, reason_en: str, reason_hi: str,
                             gap_sec: int, distance: int) -> dict:
    """A near-miss the linker looked at and declined to link. CONTRACT.md §F.1 (new)
    -- feeds Phase 8's "probably unrelated" section directly. NOT the same as
    ground-truth is_decoy (see engine/linker.py); this is the system's own judgment,
    scored by Phase 10 against the real decoys separately."""
    ids = sorted(ep_a["anomaly_ids"] + ep_b["anomaly_ids"])
    return {
        "rejected_id": rejected_id_for(ids),
        "anomaly_ids": ids,
        "categories": sorted({ep_a["category"], ep_b["category"]}),
        "h3_cells": sorted({ep_a["h3_cell"], ep_b["h3_cell"]}),
        "gap_sec": gap_sec,
        "grid_distance": distance,
        "reason_en": reason_en,
        "reason_hi": reason_hi,
    }
