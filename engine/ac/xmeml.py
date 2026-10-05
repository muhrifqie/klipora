"""FCP7 XML (xmeml v4) for Premiere: a multi-source sequence writer and `cut()`, which removes time ranges
from a sequence exported by Premiere (port of proto/lab/xml_cut.py, verified in Premiere 26.2.2: tick-identical
to QE extract on 300 and 120 random ranges, docs/research/premiere_timeline_api.md section 1a).

Writer:
    xml = build_sequence("Name (Klipora)", clips=[
        {"path": "C:/a.mp4", "start": 0.0, "src_in": 1.61, "src_out": 2.55},             # V1 + A1, linked
        {"path": "C:/b.mp4", "start": 0.94, "src_in": 0, "src_out": 3, "vtrack": 1, "audio": False},
    ], fps=120, width=2292, height=960)
    write(path, xml)
  Times are seconds; frames use the SEQUENCE rate for start/end/in/out (verified: clip in/out are sequence
  frames even when the media rate differs), <file> carries the media's own rate/duration. Each source file
  is fully defined once (first use), later uses reference its id. Optional per clip: enabled (False ->
  <enabled>FALSE</enabled>), vtrack/atrack, video/audio flags, name, filters (list of ET.Element or XML
  strings appended to the VIDEO clipitem, e.g. Basic Motion for Angles/Zoom).

Cutter:
    info = cut_file("export.xml", "cut.xml", [[10, 12], [25, 26.5]], new_name="X (Klipora)")
  ranges in SEQUENCE seconds. Splits every clipitem/generatoritem on every track, ripples, relinks pieces,
  shifts markers, keeps filters/keyframes (keyframe <when> is media-frame based, no shift needed),
  de-duplicates <file>/<sequence> definitions, drops <uuid>. Warns on transitions spanning a cut, adjacent
  transitions (start/end -1) and time remap (warnings in the UI language, ac.i18n).
"""
from __future__ import annotations

import bisect
import copy
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from .i18n import tr

TICKS_PER_SEC = 254016000000
HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


# ====================================================================== writer

def _rate(parent, fps):
    """<rate><timebase/><ntsc/></rate>; 29.97/59.94/23.976 -> ntsc TRUE with the rounded timebase."""
    f = Fraction(fps).limit_denominator(1001)
    ntsc = f.denominator == 1001 or abs(float(fps) * 1.001 - round(float(fps) * 1.001)) < 1e-3 and \
        abs(float(fps) - round(float(fps))) > 1e-3
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(round(float(fps) * 1.001) if ntsc else round(float(fps)))
    ET.SubElement(r, "ntsc").text = "TRUE" if ntsc else "FALSE"
    return r


def _sub(parent, tag, text):
    e = ET.SubElement(parent, tag)
    e.text = str(text)
    return e


def _probe(path, probe):
    if probe is not None:
        return probe(path)
    from .media import probe as p
    return p(path)


def build_sequence(name, clips, fps, width, height, sample_rate=48000, channels=2, probe=None,
                   markers=None):
    """FCP7 xmeml string for a new sequence. clips: dicts with path, start, src_in, src_out (seconds) and
    optional video/audio (default True when the media has it), vtrack/atrack (0-based), enabled, name, filters.
    markers: [{"t", "end"?, "name", "comment"?}] sequence markers (seconds).
    probe(path) -> media.probe() dict (injectable for tests)."""
    fr = lambda t: int(round(float(t) * float(fps)))  # noqa: E731  sequence frames
    infos, file_ids = {}, {}
    root = ET.Element("xmeml", version="4")
    seq = ET.SubElement(root, "sequence", id="sequence-1")
    _sub(seq, "name", name)
    _rate(seq, fps)
    media_el = ET.SubElement(seq, "media")
    video = ET.SubElement(media_el, "video")
    sc = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    _rate(sc, fps)
    _sub(sc, "width", width)
    _sub(sc, "height", height)
    _sub(sc, "pixelaspectratio", "square")
    audio = ET.SubElement(media_el, "audio")
    _sub(audio, "numOutputChannels", min(max(channels, 1), 2))
    asc = ET.SubElement(ET.SubElement(audio, "format"), "samplecharacteristics")
    _sub(asc, "depth", 16)
    _sub(asc, "samplerate", sample_rate)

    vtracks, atracks = {}, {}

    def track(kind, i):
        bag, parent = (vtracks, video) if kind == "video" else (atracks, audio)
        while len(bag) <= i:
            bag[len(bag)] = ET.SubElement(parent, "track")
        return bag[i]

    def file_el(parent, path, info):
        fid = file_ids.get(path)
        if fid:
            ET.SubElement(parent, "file", id=fid)
            return
        fid = f"file-{len(file_ids) + 1}"
        file_ids[path] = fid
        f = ET.SubElement(parent, "file", id=fid)
        p = Path(path).resolve()
        _sub(f, "name", p.name)
        _sub(f, "pathurl", p.as_uri())
        sfps = info.get("fps") or fps
        _rate(f, sfps)
        _sub(f, "duration", int(round(info["duration"] * sfps)))
        fm = ET.SubElement(f, "media")
        if info.get("has_video"):
            fv = ET.SubElement(ET.SubElement(fm, "video"), "samplecharacteristics")
            _rate(fv, sfps)
            _sub(fv, "width", info["width"])
            _sub(fv, "height", info["height"])
        if info.get("has_audio", True):
            fa = ET.SubElement(fm, "audio")
            fas = ET.SubElement(fa, "samplecharacteristics")
            _sub(fas, "depth", 16)
            _sub(fas, "samplerate", info.get("sample_rate", 48000))
            _sub(fa, "channelcount", info.get("channels", 2))

    counters = {"video": {}, "audio": {}}
    pending_links = []
    end_max = 0
    for k, c in enumerate(clips):
        path = str(c["path"])
        if path not in infos:
            infos[path] = _probe(path, probe)
        info = infos[path]
        s, a, b = fr(c["start"]), fr(c["src_in"]), fr(c["src_out"])
        length = b - a
        if length <= 0:
            continue
        kinds = []
        if c.get("video", info.get("has_video", True)) and info.get("has_video", True):
            kinds.append(("video", int(c.get("vtrack", 0))))
        if c.get("audio", info.get("has_audio", True)) and info.get("has_audio", True):
            kinds.append(("audio", int(c.get("atrack", 0))))
        ids = []
        for kind, ti in kinds:
            tr = track(kind, ti)
            n = counters[kind].get(ti, 0) + 1
            counters[kind][ti] = n
            cid = f"clipitem-{kind[0]}{ti + 1}-{n}"
            el = ET.SubElement(tr, "clipitem", id=cid)
            _sub(el, "name", c.get("name") or Path(path).name)
            _sub(el, "enabled", "TRUE" if c.get("enabled", True) else "FALSE")
            _sub(el, "duration", int(round(info["duration"] * float(fps))))
            _rate(el, fps)
            for tag, val in (("start", s), ("end", s + length), ("in", a), ("out", b)):
                _sub(el, tag, val)
            file_el(el, path, info)
            if kind == "audio":
                st = ET.SubElement(el, "sourcetrack")
                _sub(st, "mediatype", "audio")
                _sub(st, "trackindex", 1)
            else:
                for flt in c.get("filters") or []:
                    el.append(ET.fromstring(flt) if isinstance(flt, str) else flt)
            ids.append((cid, kind, ti, n, el))
        if len(ids) > 1:
            pending_links.append(ids)
        end_max = max(end_max, s + length)

    for ids in pending_links:
        for _cid, _kind, _ti, _n, el in ids:
            for lid, lkind, lti, ln, _ in ids:
                lk = ET.SubElement(el, "link")
                _sub(lk, "linkclipref", lid)
                _sub(lk, "mediatype", lkind)
                _sub(lk, "trackindex", lti + 1)
                _sub(lk, "clipindex", ln)
    if not vtracks:
        track("video", 0)
    if not atracks:
        track("audio", 0)
    for m in markers or []:
        mk = ET.SubElement(seq, "marker")
        _sub(mk, "comment", m.get("comment", ""))
        _sub(mk, "name", m.get("name", ""))
        _sub(mk, "in", fr(m["t"]))
        _sub(mk, "out", fr(m["end"]) if m.get("end") is not None and m.get("end") > m["t"] else -1)
    _sub(seq, "duration", end_max)  # same place as the legacy writer (verified import layout)
    ET.indent(root)
    return HEADER + ET.tostring(root, encoding="unicode")


def keep_clips(path, keeps, info=None):
    """Kept source ranges of ONE file -> clips for build_sequence, butted end to end (ripple)."""
    t, out = 0.0, []
    for a, b in keeps:
        if b > a:
            out.append({"path": str(path), "start": t, "src_in": a, "src_out": b})
            t += b - a
    return out


def to_xmeml(path, info, segments, name=None):
    """Legacy engine/autocut.py signature: one source, kept segments [(s, e)] -> xmeml string.
    Frames are rounded per segment edge in the source rate (identical to the old writer's math)."""
    fps = info["fps"]
    clips, pos = [], 0
    for s, e in segments:
        a, b = round(s * fps), round(e * fps)
        if b - a <= 0:
            continue
        clips.append({"path": str(path), "start": pos / float(fps), "src_in": a / float(fps),
                      "src_out": b / float(fps)})
        pos += b - a
    pinfo = {"duration": info["duration"], "fps": float(fps), "width": info.get("width", 1920),
             "height": info.get("height", 1080), "has_video": info.get("has_video", True), "has_audio": True,
             "sample_rate": info.get("sample_rate", 48000), "channels": info.get("channels", 2)}
    return build_sequence(name or f"{Path(path).stem} (Klipora)", clips, float(fps), pinfo["width"],
                          pinfo["height"], pinfo["sample_rate"], pinfo["channels"], probe=lambda p: pinfo)


def write(path, xml_text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(xml_text, encoding="utf-8")
    tmp.replace(p)
    return p


# ====================================================================== cutter (proto/lab/xml_cut.py)

def rate_of(el):
    r = el.find("rate")
    tb = int(r.findtext("timebase"))
    ntsc = (r.findtext("ntsc") or "FALSE").upper() == "TRUE"
    return (tb * 1000 / 1001) if ntsc else float(tb)


def _keep_ranges(remove, total):
    keeps, cur = [], 0
    for a, b in remove:
        if a > cur:
            keeps.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        keeps.append((cur, total))
    return keeps


def _merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _mapper(remove):
    """frame -> new frame after ripple removal (frames inside a removed range map to its start)."""
    starts = [a for a, _ in remove]
    cum = [0]
    for a, b in remove:
        cum.append(cum[-1] + (b - a))

    def m(f):
        i = bisect.bisect_right(starts, f) - 1
        if i < 0:
            return f
        a, b = remove[i]
        if f < b:
            return a - cum[i]
        return f - cum[i + 1]
    return m


def _set(el, tag, val):
    e = el.find(tag)
    if e is not None:
        e.text = str(val)


def cut(tree, ranges_sec, new_name=None):
    """Remove sequence-time ranges from an xmeml ElementTree in place. Returns
    {fps, removedFrames, newDurationFrames, keeps, warnings}."""
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    seq = root.find("sequence")
    fps = rate_of(seq)
    total = int(seq.findtext("duration"))
    remove = _merge([(round(a * fps), round(b * fps)) for a, b in ranges_sec])
    remove = [(max(0, a), min(total, b)) for a, b in remove if a < total]
    keeps = _keep_ranges(remove, total)
    fmap = _mapper(remove)
    warnings = []
    tpf = round(TICKS_PER_SEC / fps)
    pieces_of = {}
    new_items = []
    media = seq.find("media")
    for kind in ("video", "audio"):
        k = media.find(kind)
        if k is None:
            continue
        for track in k.findall("track"):
            for child in list(track):
                if child.tag not in ("clipitem", "generatoritem", "transitionitem"):
                    continue
                track.remove(child)
                st, en = int(child.findtext("start")), int(child.findtext("end"))
                if child.tag == "transitionitem":
                    if any(a <= st and en <= b for a, b in keeps):
                        child.find("start").text, child.find("end").text = str(fmap(st)), str(fmap(en))
                        new_items.append((track, child, None, None))
                    else:
                        warnings.append(tr("xmeml.warn.transitionDropped", id=child.get("id")))
                    continue
                if st < 0 or en < 0:
                    warnings.append(tr("xmeml.warn.adjacentTransition", id=child.get("id")))
                    new_items.append((track, child, None, None))
                    continue
                if child.find("filter/effect[effectid='timeremap']") is not None:
                    warnings.append(tr("xmeml.warn.timeRemap", id=child.get("id")))
                cin = int(child.findtext("in")) if child.findtext("in") is not None else 0
                tin = child.findtext("pproTicksIn")
                oid = child.get("id")
                for ki, (a, b) in enumerate(keeps):
                    s2, e2 = max(st, a), min(en, b)
                    if e2 <= s2:
                        continue
                    piece = copy.deepcopy(child) if (s2, e2) != (st, en) else child
                    nid = oid if piece is child else f"{oid}-k{ki}"
                    piece.set("id", nid)
                    off = s2 - st
                    _set(piece, "start", fmap(s2))
                    _set(piece, "end", fmap(s2) + (e2 - s2))
                    if piece.find("in") is not None:
                        _set(piece, "in", cin + off)
                        _set(piece, "out", cin + off + (e2 - s2))
                    if tin is not None:
                        _set(piece, "pproTicksIn", int(tin) + off * tpf)
                        _set(piece, "pproTicksOut", int(tin) + (off + e2 - s2) * tpf)
                    pieces_of.setdefault(oid, {})[ki] = nid
                    new_items.append((track, piece, oid, ki))
    index_of = {}
    tracks_seen = []
    for track, _el, _, _ in new_items:
        if track not in tracks_seen:
            tracks_seen.append(track)
    for track in tracks_seen:
        items = [(el, oid, ki) for t, el, oid, ki in new_items if t is track]
        items.sort(key=lambda x: int(x[0].findtext("start")))
        n = 0
        for pos, (el, _oid, _ki) in enumerate(items):
            track.insert(pos, el)  # Premiere writes items first, then <enabled>/<locked>/...
            if el.tag != "transitionitem":
                n += 1
                index_of[el.get("id")] = n
    for _, el, oid, ki in new_items:
        if oid is None:
            continue
        for ln in el.findall("link"):
            ref = ln.findtext("linkclipref")
            tgt = pieces_of.get(ref, {}).get(ki)
            if tgt is None:
                el.remove(ln)
                continue
            ln.find("linkclipref").text = tgt
            ci = ln.find("clipindex")
            if ci is not None and tgt in index_of:
                ci.text = str(index_of[tgt])
    full = {}
    for tag in ("file", "sequence"):
        for el in root.iter(tag):
            if el is seq:
                continue
            if el.get("id") and len(el) and el.get("id") not in full:
                full[el.get("id")] = copy.deepcopy(el)
    seen = set()
    for el in list(root.iter()):
        if el.tag in ("file", "sequence") and el is not seq and el.get("id"):
            i = el.get("id")
            if i in seen:
                for c in list(el):
                    el.remove(c)
            else:
                seen.add(i)
                if not len(el) and i in full:
                    for c in full[i]:
                        el.append(copy.deepcopy(c))
    for mk in seq.findall("marker"):
        a = int(mk.findtext("in"))
        b = int(mk.findtext("out") or -1)
        inside = any(ra <= a < rb for ra, rb in remove)
        if inside and b < 0:
            seq.remove(mk)
            continue
        mk.find("in").text = str(fmap(a))
        if b >= 0:
            mk.find("out").text = str(max(fmap(b), fmap(a)))
    new_total = sum(b - a for a, b in keeps)
    seq.find("duration").text = str(new_total)
    if new_name:
        seq.find("name").text = new_name
    u = seq.find("uuid")
    if u is not None:
        seq.remove(u)
    return {"fps": fps, "removedFrames": total - new_total, "newDurationFrames": new_total,
            "keeps": len(keeps), "warnings": warnings}


def cut_file(inp, out, ranges_sec, new_name=None):
    """Read Premiere's exported xmeml, remove ranges (sequence seconds), write `out`. Returns cut() info + ms."""
    t0 = time.perf_counter()
    tree = ET.parse(str(inp))
    info = cut(tree, ranges_sec, new_name)
    ET.indent(tree)
    write(out, HEADER + ET.tostring(tree.getroot(), encoding="unicode"))
    info["ms"] = round((time.perf_counter() - t0) * 1000)
    return info


def summary(xml_text):
    """Quick structural read of an xmeml string (tests/debug): name, fps, duration frames, clip counts per track."""
    root = ET.fromstring(xml_text.split("\n", 2)[-1] if xml_text.startswith("<?xml") else xml_text)
    seq = root.find("sequence")
    out = {"name": seq.findtext("name"), "fps": rate_of(seq), "duration": int(seq.findtext("duration") or 0),
           "video": [], "audio": []}
    for kind in ("video", "audio"):
        k = seq.find("media").find(kind)
        for tr in (k.findall("track") if k is not None else []):
            out[kind].append([(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")),
                               int(c.findtext("out")), c.findtext("enabled")) for c in tr.findall("clipitem")])
    return out
