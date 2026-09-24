"""Raw civic complaints: CSV, IST text dates, landmark text addresses. CONTRACT.md §D.2.

The messiest feed by design. Everything Phase 3 has to survive is in here on purpose:
  - `lodged_at` is DD/MM/YYYY h:mm AM/PM in IST, day first, no timezone marker
  - no coordinates at all; only a landmark string with a Near/Opp/Behind prefix
  - `complaint_type` is free text with inconsistent case and trailing spaces
  - some rows resolve to nothing and must be dropped
  - `text` is Hinglish and carries a name and a mobile number (see PII note below)

PII: the names and numbers here are generated and refer to nobody. CONTRACT.md §D.2
requires that canonical events drop `text` entirely and that GET /raw/{feed} masks it
before the data room ever renders it. This file on disk stays unmasked -- that is the
thing the scrubber is demonstrated against.
"""

import csv
from datetime import timedelta

from sim import config
from sim.ids import make_event_id, ref_complaint
from sim.index import entry
from sim.profiles import IST_OFFSET, rng

HEADER = ["complaint_id", "lodged_at", "ward", "locality", "landmark",
          "complaint_type", "text", "status"]

# Case and spacing variants Phase 3's synonym table has to fold together.
TYPE_VARIANTS = {
    "complaint.waterlogging": ["Water Logging", "water logging  ", "WATERLOGGING",
                               "Waterlogging", " Water logging"],
    "complaint.garbage": ["Garbage", "garbage not lifted", "GARBAGE", "Solid Waste",
                          "Garbage  "],
    "complaint.streetlight": ["Street Light Not Working", "streetlight",
                              "STREET LIGHT", "Street light not working "],
    "complaint.road_damage": ["Road Damage", "road damage", "POTHOLE", "Pothole  "],
    "complaint.smoke": ["Smoke", "burning", "SMOKE/BURNING", "Garbage Burning"],
    "traffic.signal_down": ["Signal Not Working", "traffic signal not working",
                            "SIGNAL DOWN", "Signal not working "],
}

# Deliberately unmappable. CONTRACT.md §D.2: an unmapped type is a dropped row.
JUNK_TYPES = ["Other", "misc", "General Complaint", "", "Query"]

PREFIXES = ["Near ", "Opp ", "Behind ", "Nr. ", "", "In front of ", "Next to "]

FIRST = ["Ramesh", "Sunita", "Mohammed", "Priya", "Vikram", "Anjali", "Rakesh",
         "Kavita", "Imran", "Deepak", "Meena", "Suresh", "Farhan", "Pooja", "Gopal"]
LAST = ["Meena", "Sharma", "Khan", "Gupta", "Saini", "Jain", "Yadav", "Choudhary",
        "Verma", "Agarwal", "Rathore", "Bairwa"]

TEXT_BY_CATEGORY = {
    "complaint.waterlogging": [
        "paani bhar gaya hai gate ke samne",
        "knee deep water, koi nikalne ka rasta nahi",
        "ghutno tak paani, bacche school nahi ja paa rahe",
        "drain choked hai, water logging since morning",
    ],
    "complaint.garbage": [
        "kachra 4 din se nahi utha",
        "garbage pile near the corner, badbu aa rahi hai",
        "dustbin overflow ho gaya hai",
    ],
    "complaint.streetlight": [
        "3 poles dark since evening",
        "street light band hai, raat me dar lagta hai",
        "light nahi jal rahi puri gali me",
    ],
    "complaint.road_damage": [
        "bada gaddha hai road pe, bike slip ho rahi",
        "road tut gayi hai, pothole bahut deep hai",
        "sadak kharab hai, accident ho sakta hai",
    ],
    "complaint.smoke": [
        "koi kachra jala raha hai, dhuan bahut hai",
        "smoke se saans lene me dikkat, khidki band karni padi",
        "garbage burning ho rahi hai khali plot me",
    ],
    "traffic.signal_down": [
        "signal band hai, jam lag gaya hai",
        "traffic light not working, no police here",
        "chowk pe signal dead hai since 20 min",
    ],
}


def _lodged_at(dt) -> str:
    """DD/MM/YYYY h:mm AM/PM in IST, hour NOT zero-padded. CONTRACT.md §D.2."""
    ist = dt + IST_OFFSET
    h12 = ist.hour % 12 or 12
    ampm = "AM" if ist.hour < 12 else "PM"
    return f"{ist.day:02d}/{ist.month:02d}/{ist.year} {h12}:{ist.minute:02d} {ampm}"


def _ward(cell: str) -> str:
    return f"Ward {int(cell[-6:], 16) % 150 + 1}"


def write(world, specs, seed: int = config.SEED):
    r = rng("complaints_writer", seed)
    path = config.DATA_DIR / config.OUTPUT_FILES["civic_complaints"]

    rows = sorted(
        [s for s in specs
         if s.source == "civic_complaints" and s.category in TYPE_VARIANTS],
        key=lambda s: s.start)

    index = []
    next_id = [100000]

    def cid():
        next_id[0] += r.randint(1, 4)
        return f"JPR-2026-{next_id[0]}"

    written = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)

        for s in rows:
            complaint_id = cid()
            landmark_name = s.extra.get("landmark", "")

            # ~12% of rows lose their landmark and fall back to locality.
            landmark_field = "" if r.random() < 0.12 else \
                r.choice(PREFIXES) + landmark_name
            locality = landmark_name

            name = f"{r.choice(FIRST)} {r.choice(LAST)}"
            phone = f"{r.choice('6789')}{r.randrange(10**8, 10**9)}"
            body = r.choice(TEXT_BY_CATEGORY[s.category])
            if r.random() < 0.08:
                text = ""                                   # text may be empty
            elif r.random() < 0.5:
                text = f"{name} {phone} - {body}"
            else:
                text = f"{body}. contact {name} {phone}"

            closed = r.random() < 0.22
            w.writerow([complaint_id, _lodged_at(s.start), _ward(s.h3_cell), locality,
                        landmark_field, r.choice(TYPE_VARIANTS[s.category]), text,
                        "CLOSED" if closed else "OPEN"])
            written += 1

            raw_ref = ref_complaint(complaint_id)
            eid = make_event_id(raw_ref, s.category)
            s.raw_ref, s.event_id = raw_ref, eid
            index.append(entry(eid, raw_ref, s.category, "civic_complaints", s.start,
                               s.h3_cell, s.lat, s.lon, s.measure,
                               s.truth_id, s.decoy_id))

            # Unresolvable and unmappable rows, injected between real ones. These are
            # dropped by Phase 3 and never become events, so they are NOT indexed.
            if r.random() < 0.06:
                junk_id = cid()
                if r.random() < 0.5:
                    w.writerow([junk_id, _lodged_at(s.start + timedelta(minutes=1)),
                                _ward(s.h3_cell), r.choice(["NA", "-", "Unknown", ""]),
                                "", r.choice(TYPE_VARIANTS[s.category]),
                                f"{r.choice(FIRST)} {r.choice(LAST)} "
                                f"{r.choice('6789')}{r.randrange(10**8, 10**9)} - "
                                "location nahi pata", "OPEN"])
                else:
                    w.writerow([junk_id, _lodged_at(s.start + timedelta(minutes=1)),
                                _ward(s.h3_cell), locality,
                                r.choice(PREFIXES) + landmark_name,
                                r.choice(JUNK_TYPES), "", "OPEN"])
                written += 1

    return written, index
