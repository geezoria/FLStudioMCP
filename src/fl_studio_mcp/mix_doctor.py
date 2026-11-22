"""Mix review: turn a mixer snapshot (and optionally peaks held over playback) into
concrete findings, each with its evidence and the tool call that fixes it.

Pure functions over plain dicts, so they are tested without FL. The bridge's
`mixer.snapshot` supplies the data; `mixer_peak_watch` supplies held peaks.
"""

from __future__ import annotations

import math
from typing import Any

CLIP = 1.0  # linear peak of 0 dBFS
HEADROOM_DB = -1.0  # master peaks above this leave too little room for mastering
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def to_db(level: float | None) -> float | None:
    if level is None or level <= 0:
        return None
    return round(20 * math.log10(level), 1)


def _finding(severity: str, problem: str, evidence: str, fix: str,
             track: int | None = None, tool: str | None = None, args: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"severity": severity, "problem": problem, "evidence": evidence, "fix": fix}
    if track is not None:
        out["track"] = track
    if tool:
        out["tool"] = tool
        out["args"] = args or {}
    return out


def _label(t: dict) -> str:
    """'Insert 3', or 'Insert 3 (Kick)' when the track has a name of its own."""
    base = f"Insert {t['index']}"
    name = t.get("name")
    return f"{base} ({name})" if name and name != base else base


def diagnose(snapshot: dict[str, Any], held: dict[int, float] | None = None) -> dict[str, Any]:
    """Findings for a mixer snapshot. `held` maps track -> loudest linear peak heard
    during playback; without it the snapshot's instantaneous peaks are used."""
    tracks = {t["index"]: t for t in snapshot.get("tracks", [])}
    chans = snapshot.get("channels", [])
    peaks = held if held is not None else {i: float(t.get("peak") or 0.0) for i, t in tracks.items()}
    heard = held is not None or bool(snapshot.get("playing"))
    fed: dict[int, list[dict]] = {}
    for ch in chans:
        if isinstance(ch.get("fx_track"), int):
            fed.setdefault(ch["fx_track"], []).append(ch)
    findings: list[dict[str, Any]] = []

    master = tracks.get(0)
    master_peak = peaks.get(0, 0.0)
    if master and heard:
        if master_peak >= CLIP:
            loud = sorted((i for i in peaks if i != 0 and peaks[i] > 0), key=lambda i: -peaks[i])[:3]
            over = to_db(master_peak) or 0.0
            names = ", ".join(_label(tracks[i]) for i in loud if i in tracks) or "the loudest inserts"
            findings.append(_finding(
                "high", "Master clips",
                f"Master peaked at {over:+.1f} dBFS.",
                f"Lower the sources rather than the master: take about {over + 1:.1f} dB off {names}.",
                0, "mixer_set_volume_db",
                {"track": loud[0], "db": round((tracks[loud[0]].get("volume_db") or 0.0) - over - 1, 1)}
                if loud and loud[0] in tracks else {}))
        elif master_peak > 10 ** (HEADROOM_DB / 20):
            findings.append(_finding(
                "medium", "Little headroom on Master",
                f"Master peaked at {to_db(master_peak):+.1f} dBFS.",
                "Leave 3-6 dB for mastering: lower the busiest inserts a few dB.", 0))

    for i, t in sorted(tracks.items()):
        if i == 0:
            continue
        label = _label(t)
        peak = peaks.get(i, 0.0)
        sources = fed.get(i, [])
        if heard and peak >= CLIP:
            findings.append(_finding(
                "high", f"{label} runs hot",
                f"It peaked at {to_db(peak):+.1f} dBFS.",
                "Lower the fader or the gain of the plugins before it so it peaks below -6 dBFS.",
                i, "mixer_set_volume_db", {"track": i, "db": round((t.get("volume_db") or 0.0) - (to_db(peak) or 0) - 6, 1)}))
        if sources and t.get("sends") == [] and not t.get("is_muted"):
            findings.append(_finding(
                "high", f"{label} goes nowhere",
                f"{len(sources)} channel(s) feed it, but it sends to no track, not even Master.",
                "Route it to Master (or a bus).", i, "mixer_route", {"src_track": i, "dst_track": 0, "enabled": True}))
        if t.get("is_solo"):
            findings.append(_finding(
                "medium", f"{label} is soloed",
                "Solo silences every other track, so the mix you hear isn't the whole mix.",
                "Unsolo it when you're done.", i, "mixer_solo", {"track": i, "solo": False}))
        if t.get("slots") and t.get("slots_enabled") is False:
            findings.append(_finding(
                "medium", f"Effects of {label} are switched off",
                f"{len(t['slots'])} plugin(s) on the track, but its FX chain is disabled.",
                "Turn the chain back on if that wasn't on purpose.", i,
                "mixer_set_slots_enabled", {"track": i, "enabled": True}))
        for slot in t.get("slots") or []:
            if slot.get("enabled") is False:
                findings.append(_finding(
                    "low", f"{slot.get('name')} on {label} is bypassed",
                    f"Slot {slot['slot']} is turned off.",
                    "Remove it or turn it on.", i, "mixer_set_slot",
                    {"track": i, "slot": slot["slot"], "enabled": True}))
        if sources and t.get("is_muted"):
            findings.append(_finding(
                "low", f"{label} is muted",
                f"It mutes {len(sources)} channel(s): " + ", ".join(str(c.get('name')) for c in sources[:5]) + ".",
                "Unmute it if it should be heard.", i, "mixer_mute", {"track": i, "muted": False}))
        if heard and sources and peak <= 0 and not t.get("is_muted") and not any(t2.get("is_solo") for t2 in tracks.values()):
            findings.append(_finding(
                "low", f"No signal on {label}",
                f"{len(sources)} channel(s) feed it but it stayed silent while FL played.",
                "Check that the channels have notes in what played and aren't muted.", i))
        db = t.get("volume_db")
        if isinstance(db, (int, float)) and db > 3.0:
            findings.append(_finding(
                "low", f"Fader of {label} is pushed up",
                f"It sits at {db:+.1f} dB.",
                "Prefer gain at the source and keep faders near 0 dB.", i, "mixer_set_volume_db", {"track": i, "db": 0.0}))

    direct = [c for c in chans if c.get("fx_track") == 0]
    if direct and len(direct) < len(chans):
        names = ", ".join(f"{c['index']} ({c.get('name')})" for c in direct[:8])
        findings.append(_finding(
            "low", "Channels play straight into Master",
            f"{len(direct)} channel(s) have no insert of their own: {names}.",
            "Give each its own free insert with channel_route_to_mixer, so it can be leveled and processed."))

    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
    levels = [{"track": i, "name": tracks[i].get("name") if i in tracks else None,
               "peak_db": to_db(p), "clipping": p >= CLIP}
              for i, p in sorted(peaks.items(), key=lambda kv: -kv[1]) if p > 0]
    summary = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER}
    return {"findings": findings, "summary": summary, "levels": levels,
            "measured": "held peaks over playback" if held is not None
            else ("live peaks" if heard else "no levels: FL wasn't playing")}
