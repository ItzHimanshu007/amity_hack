"""CONTRACT.md §F.1 -- which category can cause which, and within what gap.

This is hand-written domain knowledge, not a learned model, and that is deliberate:
fourteen days of history is nowhere near enough data to LEARN that a tripped feeder
darkens traffic signals, but every electrical engineer in the room already knows it.
The table states the mechanism; engine.lift measures how often the pair actually shows
up together and reports that as evidence. Mechanism decides whether a link is allowed;
frequency decides how strongly we believe this particular instance.

Two structural facts fall out of the table and are used elsewhere:

  * ROOT-CAPABLE: a category with at least one outgoing edge can start something.
    `air.pm25`, `traffic.signal_down` and `complaint.streetlight` have none -- they are
    pure consequences. A lone dark streetlight is not a "situation": lights fail for a
    hundred reasons a civic feed cannot see, so claiming a situation from one is a claim
    we cannot support. See linker.standalone_eligible.

  * TERMINAL / ISOLATED: `complaint.garbage` appears on neither side. Uncollected
    garbage is an accumulation condition measured in days, not an event with
    minute-scale causes or effects inside this category set. Refuse burning is a real
    phenomenon, but the lag from "bin not emptied" to "someone sets it alight" is days,
    an order of magnitude outside every window in this table, so putting an edge there
    would let any routine garbage backlog absorb an unrelated smoke report.

Windows are minutes, `(min_gap, max_gap)`, measured between the two anomalies' EARLIEST
contributing events. `min_gap` is 0 rather than 1: a DISCOM feeder trip emits its
`power.outage` and its `traffic.signal_down` from the same raw record at the same
second (CONTRACT.md §D.3), so a strictly-positive minimum would reject the single most
certain link in the system.
"""

# (cause, effect) -> (min_gap_min, max_gap_min, why_en, why_hi)
PLAUSIBLE_PAIRS = {
    ("weather.rain", "complaint.waterlogging"): (
        0, 90,
        "heavy rain pools in low-lying streets within the hour",
        "तेज़ बारिश का पानी एक घंटे के भीतर नीची सड़कों पर भर जाता है"),
    ("weather.rain", "power.outage"): (
        0, 120,
        "water reaching a feeder or transformer trips the circuit",
        "फीडर या ट्रांसफॉर्मर में पानी जाने से सर्किट ट्रिप होता है"),
    ("weather.rain", "drain.overflow"): (
        0, 60,
        "a burst of rain fills the storm drains faster than they can carry it away",
        "तेज़ बारिश नालों को उनकी क्षमता से तेज़ भर देती है"),
    ("drain.overflow", "complaint.waterlogging"): (
        0, 60,
        "a surcharged drain backs up and spills onto the street",
        "भरा हुआ नाला उफनकर सड़क पर पानी फैला देता है"),
    ("drain.overflow", "complaint.road_damage"): (
        0, 240,
        "water forced out of a full drain undermines the road beside it",
        "उफनते नाले का पानी बगल की सड़क को नीचे से खोखला कर देता है"),
    ("weather.rain", "complaint.road_damage"): (
        0, 240,
        "standing water opens up potholes that residents then report",
        "जमा पानी से गड्ढे खुल जाते हैं जिनकी शिकायत लोग करते हैं"),
    ("weather.heat", "power.outage"): (
        0, 180,
        "peak cooling load on a hot afternoon overloads distribution feeders",
        "गर्म दोपहर में कूलिंग लोड बढ़ने से बिजली फीडर पर दबाव आता है"),
    ("complaint.waterlogging", "power.outage"): (
        0, 60,
        "water in a street-level substation or feeder pillar trips it",
        "सड़क के सबस्टेशन या फीडर में पानी जाने से बिजली जाती है"),
    ("complaint.waterlogging", "complaint.road_damage"): (
        0, 240,
        "water under the surface breaks the road up",
        "सतह के नीचे पानी जाने से सड़क टूट जाती है"),
    ("power.outage", "traffic.signal_down"): (
        0, 30,
        "a tripped feeder carrying signal circuits leaves junctions dark",
        "सिग्नल सर्किट वाला फीडर ट्रिप होने पर चौराहे अँधेरे रहते हैं"),
    ("power.outage", "complaint.streetlight"): (
        0, 60,
        "the same dead feeder takes the street lights with it",
        "वही बंद फीडर स्ट्रीटलाइट भी बंद कर देता है"),
    ("complaint.smoke", "air.pm25"): (
        0, 30,
        "burning close by pushes particulate readings up downwind",
        "पास में कुछ जलने से हवा में कण बढ़ जाते हैं"),
}

# Categories that appear on neither side of any edge. Kept explicit so the omission
# reads as a decision rather than an oversight -- CONTRACT.md §F.1 names the reason.
ISOLATED_CATEGORIES = ("complaint.garbage",)


def window_for(cause: str, effect: str):
    """(min_gap_sec, max_gap_sec) for a directed pair, or None if not plausible."""
    entry = PLAUSIBLE_PAIRS.get((cause, effect))
    if entry is None:
        return None
    return entry[0] * 60, entry[1] * 60


def reason_for(cause: str, effect: str):
    """(why_en, why_hi) for a directed pair, or None."""
    entry = PLAUSIBLE_PAIRS.get((cause, effect))
    return (entry[2], entry[3]) if entry else None


def successors(category: str) -> tuple:
    """Categories this one can plausibly cause, sorted for determinism."""
    return tuple(sorted(e for (c, e) in PLAUSIBLE_PAIRS if c == category))


def predecessors(category: str) -> tuple:
    return tuple(sorted(c for (c, e) in PLAUSIBLE_PAIRS if e == category))


def is_root_capable(category: str) -> bool:
    """True when this category can start a chain. A category that only ever appears as
    an effect cannot, on its own, be evidence that something is happening."""
    return bool(successors(category))


def related(a: str, b: str) -> bool:
    """Plausible in either direction -- used when order is not yet known."""
    return (a, b) in PLAUSIBLE_PAIRS or (b, a) in PLAUSIBLE_PAIRS


def directed_pairs() -> tuple:
    """Every (cause, effect, min_sec, max_sec), sorted. The lift table iterates this."""
    return tuple(sorted(
        (c, e, v[0] * 60, v[1] * 60) for (c, e), v in PLAUSIBLE_PAIRS.items()))
