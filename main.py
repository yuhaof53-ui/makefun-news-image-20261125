#!/usr/bin/env python3
"""The World Today - 25 November 2026.

A program that draws one newspaper front page summing up the world's news
of Wednesday 25 November 2026 and writes it to /output/entry.png.

Pure Python 3 standard library. No network. The text is rendered with a
small TrueType rasterizer written for this entry: it reads the DejaVu fonts
shipped in this repository, flattens their quadratic outlines, and fills
them with a non-zero scanline fill at 2x supersampling.

Layout: masthead + date, one lead story with a drawn illustration, then
six region sections and a sport-and-markets strip - 35 stories in all.
"""

import os
import struct
import sys
import zlib
from binascii import crc32

# ---------------------------------------------------------------------------
# TrueType parsing
# ---------------------------------------------------------------------------


class TTF:
    """Minimal reader for a TrueType font: cmap, hmtx, loca, glyf."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            self.data = fh.read()
        d = self.data
        if len(d) < 12 or struct.unpack(">I", d[0:4])[0] != 0x00010000:
            raise ValueError("not a TrueType font: %s" % path)
        ntab = struct.unpack(">H", d[4:6])[0]
        self.tables = {}
        for i in range(ntab):
            off = 12 + i * 16
            tag = d[off:off + 4].decode("latin-1")
            toff, tlen = struct.unpack(">II", d[off + 8:off + 16])
            self.tables[tag] = (toff, tlen)
        head = self.table("head")
        self.units_per_em = struct.unpack(">H", head[18:20])[0]
        self.index_to_loc_format = struct.unpack(">h", head[50:52])[0]
        maxp = self.table("maxp")
        self.num_glyphs = struct.unpack(">H", maxp[4:6])[0]
        hhea = self.table("hhea")
        self.num_hmetrics = struct.unpack(">H", hhea[34:36])[0]
        self._cmap = None
        self._loca = None
        self._glyf = None

    def table(self, tag):
        off, ln = self.tables[tag]
        return self.data[off:off + ln]

    def cmap(self):
        if self._cmap is not None:
            return self._cmap
        c = self.table("cmap")
        nsub = struct.unpack(">H", c[2:4])[0]
        subs = []
        for i in range(nsub):
            _plat, _enc, soff = struct.unpack(">HHI", c[4 + i * 8:12 + i * 8])
            subs.append(soff)
        mapping = {}
        for soff in subs:
            if struct.unpack(">H", c[soff:soff + 2])[0] == 12:
                ngrp = struct.unpack(">I", c[soff + 12:soff + 16])[0]
                for j in range(ngrp):
                    s, e, g = struct.unpack(
                        ">III", c[soff + 16 + j * 12:soff + 28 + j * 12])
                    for ch in range(s, e + 1):
                        mapping[ch] = g + (ch - s)
                self._cmap = mapping
                return mapping
        for soff in subs:
            if struct.unpack(">H", c[soff:soff + 2])[0] == 4:
                seg2 = struct.unpack(">H", c[soff + 6:soff + 8])[0]
                seg = seg2 // 2
                base = soff + 14
                ends = [struct.unpack(">H", c[base + 2 * i:base + 2 * i + 2])[0]
                        for i in range(seg)]
                base += 2 * seg + 2  # skip reservedPad
                starts = [struct.unpack(">H", c[base + 2 * i:base + 2 * i + 2])[0]
                          for i in range(seg)]
                base += 2 * seg
                deltas = [struct.unpack(">h", c[base + 2 * i:base + 2 * i + 2])[0]
                          for i in range(seg)]
                base += 2 * seg
                ro_base = base
                for i in range(seg):
                    ro = struct.unpack(
                        ">H", c[ro_base + 2 * i:ro_base + 2 * i + 2])[0]
                    for ch in range(starts[i], ends[i] + 1):
                        if ch == 0xFFFF:
                            continue
                        if ro == 0:
                            mapping[ch] = (ch + deltas[i]) % 65536
                        else:
                            addr = ro_base + 2 * i + ro + 2 * (ch - starts[i])
                            mapping[ch] = struct.unpack(
                                ">H", c[addr:addr + 2])[0]
                self._cmap = mapping
                return mapping
        self._cmap = mapping
        return mapping

    def glyph_id(self, ch):
        return self.cmap().get(ord(ch), 0)

    def advance(self, gid):
        h = self.table("hmtx")
        if gid < self.num_hmetrics:
            return struct.unpack(">H", h[4 * gid:4 * gid + 2])[0]
        return struct.unpack(
            ">H", h[4 * (self.num_hmetrics - 1):4 * (self.num_hmetrics - 1) + 2])[0]

    def glyph_offset(self, gid):
        if self._loca is None:
            self._loca = self.table("loca")
            self._glyf = self.table("glyf")
        if self.index_to_loc_format == 0:
            o0 = struct.unpack(">H", self._loca[2 * gid:2 * gid + 2])[0] * 2
            o1 = struct.unpack(">H", self._loca[2 * gid + 2:2 * gid + 4])[0] * 2
        else:
            o0 = struct.unpack(">I", self._loca[4 * gid:4 * gid + 4])[0]
            o1 = struct.unpack(">I", self._loca[4 * gid + 4:4 * gid + 8])[0]
        return o0, o1

    def glyph_outlines(self, gid, depth=0):
        """Contours of one glyph: lists of (x, y, on_curve) font units."""
        if depth > 4:
            return []
        o0, o1 = self.glyph_offset(gid)
        if o1 <= o0:
            return []
        g = self._glyf[o0:o1]
        ncont = struct.unpack(">h", g[0:2])[0]
        if ncont >= 0:
            return self._simple_outlines(g, ncont)
        contours = []
        p = 10
        while True:
            flags, comp = struct.unpack(">HH", g[p:p + 4])
            p += 4
            if flags & 0x0001:
                a1, a2 = struct.unpack(">hh", g[p:p + 4])
                p += 4
            else:
                a1, a2 = struct.unpack(">bb", g[p:p + 2])
                p += 2
            dx, dy = (a1, a2) if (flags & 0x0002) else (0, 0)
            if flags & 0x0008:
                s = struct.unpack(">h", g[p:p + 2])[0] / 16384.0
                p += 2
                m = (s, 0.0, 0.0, s)
            elif flags & 0x0040:
                sx, sy = struct.unpack(">hh", g[p:p + 4])
                p += 4
                m = (sx / 16384.0, 0.0, 0.0, sy / 16384.0)
            elif flags & 0x0080:
                xs, s01, s10, ys = struct.unpack(">hhhh", g[p:p + 8])
                p += 8
                m = (xs / 16384.0, s01 / 16384.0,
                     s10 / 16384.0, ys / 16384.0)
            else:
                m = (1.0, 0.0, 0.0, 1.0)
            for cont in self.glyph_outlines(comp, depth + 1):
                contours.append([(x * m[0] + y * m[2] + dx,
                                  x * m[1] + y * m[3] + dy, on)
                                 for (x, y, on) in cont])
            if not (flags & 0x0020):
                break
        return contours

    def _simple_outlines(self, g, ncont):
        ends = [struct.unpack(">H", g[10 + 2 * i:12 + 2 * i])[0]
                for i in range(ncont)]
        npts = ends[-1] + 1 if ends else 0
        ilen = struct.unpack(">H", g[10 + 2 * ncont:12 + 2 * ncont])[0]
        p = 12 + 2 * ncont + ilen
        flags = []
        while len(flags) < npts:
            f = g[p]
            p += 1
            flags.append(f)
            if f & 0x08:
                rep = g[p]
                p += 1
                flags.extend([f] * rep)
        xs = []
        x = 0
        for f in flags:
            if f & 0x02:
                v = g[p]
                p += 1
                x += v if (f & 0x10) else -v
            elif f & 0x10:
                pass
            else:
                x += struct.unpack(">h", g[p:p + 2])[0]
                p += 2
            xs.append(x)
        ys = []
        y = 0
        for f in flags:
            if f & 0x04:
                v = g[p]
                p += 1
                y += v if (f & 0x20) else -v
            elif f & 0x20:
                pass
            else:
                y += struct.unpack(">h", g[p:p + 2])[0]
                p += 2
            ys.append(y)
        contours = []
        start = 0
        for end in ends:
            pts = [(xs[i], ys[i], bool(flags[i] & 0x01))
                   for i in range(start, end + 1)]
            contours.append(pts)
            start = end + 1
        return contours

# ---------------------------------------------------------------------------
# Outline flattening and rasterization
# ---------------------------------------------------------------------------

SS = 2  # supersample factor


def _flatten_quad(p0, p1, p2, out, depth=0):
    """Subdivide a quadratic Bezier into line segments, appended to out."""
    mx = (p0[0] + 2 * p1[0] + p2[0]) / 4.0
    my = (p0[1] + 2 * p1[1] + p2[1]) / 4.0
    cx = (p0[0] + p2[0]) / 2.0
    cy = (p0[1] + p2[1]) / 2.0
    if depth >= 5 or (mx - cx) ** 2 + (my - cy) ** 2 < 0.09:
        out.append(p2)
        return
    q0 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    q1 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    m = ((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0)
    _flatten_quad(p0, q0, m, out, depth + 1)
    _flatten_quad(m, q1, p2, out, depth + 1)


def _contour_segments(pts):
    """Turn one contour's (x, y, on_curve) points into line segments."""
    n = len(pts)
    if n < 2:
        return []
    if pts[0][2]:
        start = (pts[0][0], pts[0][1])
        idx = 1
    elif pts[-1][2]:
        start = (pts[-1][0], pts[-1][1])
        idx = 0
    else:
        start = ((pts[-1][0] + pts[0][0]) / 2.0,
                 (pts[-1][1] + pts[0][1]) / 2.0)
        idx = 0
    order = list(range(idx, n)) + list(range(0, idx))
    segs = []
    cur = start
    i = 0
    while i < len(order):
        x, y, on = pts[order[i]]
        if on:
            segs.append((cur, (x, y)))
            cur = (x, y)
            i += 1
        else:
            j = order[(i + 1) % len(order)]
            nx, ny, non = pts[j]
            if non:
                end = (nx, ny)
                nxt = i + 2
            else:
                end = ((x + nx) / 2.0, (y + ny) / 2.0)
                nxt = i + 1
            out = []
            _flatten_quad(cur, (x, y), end, out)
            for p in out:
                segs.append((cur, p))
                cur = p
            i = nxt
    if cur != start:
        segs.append((cur, start))
    return segs


def _rasterize_glyph(font, gid, px):
    """Render one glyph at px pixels em height, 2x supersampled.

    Returns (width, height, left, top, advance_px, coverage bytes).
    left/top are the bitmap's top-left offset from the pen position /
    baseline, in final pixels.
    """
    scale = px / font.units_per_em
    contours = font.glyph_outlines(gid)
    adv = int(round(font.advance(gid) * scale))
    if not contours:
        return (0, 0, 0, 0, adv, b"")
    segs = []
    for cont in contours:
        for (ax, ay), (bx, by) in _contour_segments(cont):
            segs.append(((ax * scale * SS, -ay * scale * SS),
                         (bx * scale * SS, -by * scale * SS)))
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    x0 = (int(min(xs) // 1) // SS) * SS
    x1 = -((-int(max(xs) // 1) - 1) // SS) * SS
    y0 = (int(min(ys) // 1) // SS) * SS
    y1 = -((-int(max(ys) // 1) - 1) // SS) * SS
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return (0, 0, 0, 0, adv, b"")
    crossings = {}
    for (ax, ay), (bx, by) in segs:
        if ay == by:
            continue
        lo, hi = (ay, by) if ay < by else (by, ay)
        direction = 1 if by > ay else -1
        for yy in range(int(lo // 1), int(hi // 1) + 1):
            yc = yy + 0.5
            if yc < lo or yc >= hi:
                continue
            t = (yc - ay) / (by - ay)
            xc = ax + t * (bx - ax)
            crossings.setdefault(yy, []).append((xc, direction))
    buf = bytearray(w * h)
    for yy, cl in crossings.items():
        if yy < y0 or yy >= y1:
            continue
        cl.sort()
        winding = 0
        x_prev = None
        for xc, d in cl:
            if x_prev is not None and winding != 0:
                a = max(int(x_prev // 1), x0)
                b = min(int(xc // 1), x1 - 1)
                for xx in range(a, b + 1):
                    buf[(yy - y0) * w + (xx - x0)] = 255
            winding += d
            x_prev = xc
    ow, oh = w // SS, h // SS
    out = bytearray(ow * oh)
    for yy in range(oh):
        for xx in range(ow):
            s = 0
            for dy in range(SS):
                for dx in range(SS):
                    s += buf[(yy * SS + dy) * w + xx * SS + dx]
            out[yy * ow + xx] = s // (SS * SS)
    return (ow, oh, x0 // SS, y0 // SS, adv, bytes(out))


class Font:
    """A TrueType font at arbitrary sizes, with a glyph cache."""

    def __init__(self, path):
        self.ttf = TTF(path)
        self.cache = {}

    def glyph(self, ch, px):
        key = (ch, px)
        g = self.cache.get(key)
        if g is None:
            gid = self.ttf.glyph_id(ch)
            g = _rasterize_glyph(self.ttf, gid, px)
            self.cache[key] = g
        return g

    def text_width(self, text, px, tracking=0):
        w = 0
        for ch in text:
            w += self.glyph(ch, px)[4] + tracking
        return w - tracking if text else 0

    def wrap(self, text, px, max_w, tracking=0):
        """Greedy word wrap; returns a list of lines."""
        lines = []
        cur = ""
        for wd in text.split(" "):
            trial = (cur + " " + wd) if cur else wd
            if self.text_width(trial, px, tracking) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines


# ---------------------------------------------------------------------------
# Canvas and PNG output
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, w, h, bg):
        self.w = w
        self.h = h
        self.pix = bytearray(bytes(bg)) * (w * h)

    def rect(self, x, y, w, h, color):
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w, x + w)
        y1 = min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            o = (yy * self.w + x0) * 3
            self.pix[o:o + len(row)] = row

    def _blit(self, bx, by, gw, gh, cov, color):
        r, g, b = color
        for yy in range(gh):
            py = by + yy
            if py < 0 or py >= self.h:
                continue
            row = py * self.w * 3
            for xx in range(gw):
                a = cov[yy * gw + xx]
                if a == 0:
                    continue
                px = bx + xx
                if px < 0 or px >= self.w:
                    continue
                o = row + px * 3
                if a == 255:
                    self.pix[o] = r
                    self.pix[o + 1] = g
                    self.pix[o + 2] = b
                else:
                    self.pix[o] = (r * a + self.pix[o] * (255 - a)) // 255
                    self.pix[o + 1] = (g * a + self.pix[o + 1] * (255 - a)) // 255
                    self.pix[o + 2] = (b * a + self.pix[o + 2] * (255 - a)) // 255

    def draw_text(self, font, x, y, text, px, color, tracking=0):
        """Draw text with its baseline at y. Returns the pen x after it."""
        pen = x
        for ch in text:
            gw, gh, left, top, adv, cov = font.glyph(ch, px)
            if gw and gh:
                self._blit(pen + left, y + top, gw, gh, cov, color)
            pen += adv + tracking
        return pen

    def draw_centered(self, font, cx, y, text, px, color, tracking=0):
        w = self.text_width(font, text, px, tracking)
        return self.draw_text(font, cx - w // 2, y, text, px, color, tracking)

    @staticmethod
    def text_width(font, text, px, tracking=0):
        return font.text_width(text, px, tracking)


def write_png(path, canvas):
    w, h = canvas.w, canvas.h
    raw = bytearray()
    for yy in range(h):
        raw.append(0)
        raw += canvas.pix[yy * w * 3:(yy + 1) * w * 3]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", comp) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)

# ---------------------------------------------------------------------------
# Content: 35 stories, all verified as 25 November 2026 news
# ---------------------------------------------------------------------------

LEAD_KICKER = "THE IRAN WAR - HORMUZ"
LEAD_HEADLINE = "TWO TANKERS HIT MINES AND BURN IN STRAIT OF HORMUZ"
LEAD_SUB = ("Iran's Revolutionary Guards say the ships struck mines on an "
            "'unauthorised route' - as the Houthis claim strikes on a Saudi "
            "refinery and Riyadh's financial district.")

SECTIONS_LEFT = [
    ("NORTH AMERICA", (30, 64, 175), [
        "Abrego Garcia leaves US voluntarily for Spain",
        "US ready to slap tariffs on China, EU and 14 others",
        "Army to detonate 5-ton lead azide stockpile in Iowa",
        "Plane crash off Mississippi coast kills 6, including 2 children",
        "Grand jury indicts Thomas Markle on sex abuse charges",
    ]),
    ("EUROPE", (14, 116, 144), [
        "Spain's leaders clash in TV debate as housing decides snap vote",
        "Puigdemont to return to Spain on Dec 12, ending exile",
        "Record 2,100 migrants reach Canary Islands in a day",
        "Bulgaria intercepts first migrant boat in months, 35 aboard",
        "Two charged over burglary at William and Kate's country home",
    ]),
    ("MIDDLE EAST", (146, 64, 14), [
        "Saudi coalition says it hit Hodeidah port and Yemen targets",
        "Jordan sentences man to death over attack that killed officer",
        "Iran vows 'all available means' to counter US blockade",
        "Five Turkish air force crew killed in helicopter crash",
        "ICC judges lose bid to block Trump sanctions",
    ]),
]

SECTIONS_RIGHT = [
    ("ASIA-PACIFIC", (153, 27, 27), [
        "North Korea orders Swiss office shut, expels diplomat",
        "China launches Shenzhou-24 with first foreign astronaut",
        "China probes Shanghai party chief in graft crackdown",
        "Rescuers hunt miners trapped in flooded Indonesian mine",
        "Bangladesh raises garment wage 36% after protests",
        "Australia and Solomons sign treaty checking China",
    ]),
    ("AFRICA", (21, 101, 52), [
        "Ebola teams trapped in Ituri hospital as fighting rages",
        "DR Congo halts all cobalt exports to lift prices",
        "Ramaphosa wins court bid to scrap Phala Phala report",
        "Zambia charges opposition leader with treason",
        "Sudan drone strikes kill 15 in Damazin and Roseires",
    ]),
    ("LATIN AMERICA", (120, 53, 15), [
        "All hostages freed as forces storm Cartagena supermarket",
        "Sheinbaum rejects US claims tying governor to cartel",
        "Colombia blames FARC dissidents for Villavicencio car bomb",
    ]),
]

STRIP = ("SPORT & MARKETS", (51, 65, 85), [
    "Asian stocks tumble on tariff threats; Seoul -5.3%",
    "Nvidia falls 5.9% as AI rebound stumbles",
    "Steelers bench Rodgers; Rudolph to start vs Broncos",
    "Cubs add Rogers on $120M deal and slugger Sato",
    "Nine Champions League ties tonight: Liverpool at Bruges",
])

FOOTER = ("Sources: Reuters | AP | BBC | Al Jazeera | UN News | Radio Dabanga "
          "| UEFA - All events of Wednesday, 25 November 2026")

PAPER = (251, 250, 243)
INK = (20, 20, 20)
RED = (185, 28, 28)
GRAY = (96, 96, 96)
SUB = (64, 64, 64)
WHITE = (255, 255, 255)

PAGE_W, PAGE_H = 1024, 1024
MARGIN = 26
COL_W = 476
COL_GAP = 20
LEFT_X = MARGIN
RIGHT_X = MARGIN + COL_W + COL_GAP
CONTENT_W = PAGE_W - 2 * MARGIN
HEADER_H = 25
FOOTER_RULE_Y = 996


def draw_disc(cv, cx, cy, r, color):
    r2 = r * r
    for yy in range(cy - r, cy + r + 1):
        if yy < 0 or yy >= cv.h:
            continue
        for xx in range(cx - r, cx + r + 1):
            if xx < 0 or xx >= cv.w:
                continue
            if (xx - cx) ** 2 + (yy - cy) ** 2 <= r2:
                o = (yy * cv.w + xx) * 3
                cv.pix[o] = color[0]
                cv.pix[o + 1] = color[1]
                cv.pix[o + 2] = color[2]


def draw_illustration(cv, ix, iy, iw, ih):
    """Geometric scene: two tankers burning in the Strait of Hormuz."""
    cv.rect(ix, iy, iw, 84, (213, 229, 240))          # sky
    draw_disc(cv, ix + 272, iy + 26, 16, (240, 196, 88))  # sun
    cv.rect(ix, iy + 84, iw, ih - 84, (23, 69, 107))  # sea
    cv.rect(ix, iy + 83, iw, 2, (200, 210, 220))      # horizon
    # Tanker one, nearer, on fire.
    cv.rect(ix + 18, iy + 98, 132, 16, (17, 17, 17))
    cv.rect(ix + 18, iy + 94, 132, 4, (46, 46, 46))
    cv.rect(ix + 112, iy + 78, 26, 16, (232, 232, 228))
    cv.rect(ix + 34, iy + 86, 14, 8, RED)
    draw_disc(cv, ix + 60, iy + 88, 9, (240, 120, 20))
    draw_disc(cv, ix + 60, iy + 79, 7, (240, 120, 20))
    draw_disc(cv, ix + 82, iy + 90, 7, (240, 120, 20))
    draw_disc(cv, ix + 82, iy + 83, 5, (240, 120, 20))
    draw_disc(cv, ix + 60, iy + 90, 5, (250, 200, 60))
    draw_disc(cv, ix + 60, iy + 82, 4, (250, 200, 60))
    draw_disc(cv, ix + 82, iy + 92, 4, (250, 200, 60))
    draw_disc(cv, ix + 62, iy + 74, 6, (130, 130, 130))
    draw_disc(cv, ix + 70, iy + 66, 5, (155, 155, 155))
    # Tanker two, farther off.
    cv.rect(ix + 196, iy + 104, 104, 12, (60, 60, 60))
    cv.rect(ix + 268, iy + 92, 18, 12, (200, 200, 196))
    draw_disc(cv, ix + 216, iy + 98, 6, (240, 120, 20))
    # Waves and caption.
    cv.rect(ix + 180, iy + 124, 50, 2, (220, 232, 240))
    cv.rect(ix + 260, iy + 132, 46, 2, (220, 232, 240))
    cv.rect(ix + 140, iy + 126, 34, 2, (220, 232, 240))
    cv.draw_text(BOLD, ix + 10, iy + 140, "STRAIT OF HORMUZ", 11, WHITE)
    # Frame.
    cv.rect(ix, iy, iw, 1, INK)
    cv.rect(ix, iy + ih - 1, iw, 1, INK)
    cv.rect(ix, iy, 1, ih, INK)
    cv.rect(ix + iw - 1, iy, 1, ih, INK)


def measure_column(font, sections, item_px, line_h, item_gap):
    total = 0
    for _title, _color, items in sections:
        total += HEADER_H + 10
        for it in items:
            total += len(font.wrap(it, item_px, COL_W)) * line_h + item_gap
        total += 8
    return total


def render_column(cv, font, x, sections, y0, item_px, line_h, item_gap):
    y = y0
    for title, color, items in sections:
        cv.rect(x, y, COL_W, HEADER_H, color)
        cv.draw_text(BOLD, x + 10, y + HEADER_H - 7, title, 14, WHITE)
        y += HEADER_H + 10
        for it in items:
            for ln in font.wrap(it, item_px, COL_W):
                cv.draw_text(font, x, y, ln, item_px, INK)
                y += line_h
            y += item_gap
        y += 8
    return y


def build_page():
    cv = Canvas(PAGE_W, PAGE_H, PAPER)
    # Masthead.
    cv.draw_centered(BOLD, PAGE_W // 2, 74, "THE WORLD TODAY", 54, INK)
    cv.rect(MARGIN, 88, CONTENT_W, 3, INK)
    cv.rect(MARGIN, 94, CONTENT_W, 1, INK)
    cv.draw_centered(BOLD, PAGE_W // 2, 122,
                     "WEDNESDAY, 25 NOVEMBER 2026", 22, INK)
    cv.draw_centered(REGULAR, PAGE_W // 2, 142,
                     "WORLD NEWS IN ONE PAGE", 12, GRAY, tracking=3)
    cv.rect(MARGIN, 152, CONTENT_W, 1, INK)
    # Lead story, left; illustration, right.
    lead_x = MARGIN
    lead_w = 620
    cv.draw_text(BOLD, lead_x, 180, LEAD_KICKER, 13, RED, tracking=1)
    y = 216
    for line in BOLD.wrap(LEAD_HEADLINE, 32, lead_w):
        cv.draw_text(BOLD, lead_x, y, line, 32, INK)
        y += 38
    y += 4
    for line in REGULAR.wrap(LEAD_SUB, 15, lead_w):
        cv.draw_text(REGULAR, lead_x, y, line, 15, SUB)
        y += 20
    draw_illustration(cv, 668, 168, 330, 144)
    lead_bottom = max(y - 20, 168 + 144)
    cv.rect(MARGIN, lead_bottom + 12, CONTENT_W, 1, INK)
    sections_y = lead_bottom + 24
    # Sport & markets strip height (fixed, computed from wrapping).
    strip_col_w = (CONTENT_W - 4 * 12) // 5
    strip_lines = max(len(REGULAR.wrap(it, 12, strip_col_w))
                      for it in STRIP[2])
    strip_h = HEADER_H + 6 + strip_lines * 16 + 8
    avail = FOOTER_RULE_Y - 12 - strip_h - sections_y
    # Pick the largest item size that fits both columns.
    chosen = None
    for item_px in (14, 13.5, 13):
        line_h = round(item_px * 1.3)
        hl = measure_column(REGULAR, SECTIONS_LEFT, item_px, line_h, 7)
        hr = measure_column(REGULAR, SECTIONS_RIGHT, item_px, line_h, 7)
        if max(hl, hr) <= avail:
            chosen = (item_px, line_h, 7, hl, hr)
            break
    if chosen is None:
        item_px, line_h = 13, 17
        hl = measure_column(REGULAR, SECTIONS_LEFT, item_px, line_h, 7)
        hr = measure_column(REGULAR, SECTIONS_RIGHT, item_px, line_h, 7)
        chosen = (item_px, line_h, 7, hl, hr)
    item_px, line_h, item_gap, hl, hr = chosen
    # Spread any slack as extra breathing room between items.
    slack = avail - max(hl, hr)
    total_items = sum(len(s[2]) for s in SECTIONS_LEFT + SECTIONS_RIGHT)
    item_gap += min(5, slack // total_items)
    hl = measure_column(REGULAR, SECTIONS_LEFT, item_px, line_h, item_gap)
    hr = measure_column(REGULAR, SECTIONS_RIGHT, item_px, line_h, item_gap)
    print("layout: item_px=%s line_h=%s gap=%s left_h=%s right_h=%s "
          "avail=%s strip_h=%s" % (item_px, line_h, item_gap, hl, hr,
                                   avail, strip_h))
    render_column(cv, REGULAR, LEFT_X, SECTIONS_LEFT, sections_y,
                  item_px, line_h, item_gap)
    render_column(cv, REGULAR, RIGHT_X, SECTIONS_RIGHT, sections_y,
                  item_px, line_h, item_gap)
    # Sport & markets strip.
    strip_y = sections_y + max(hl, hr) + 12
    cv.rect(MARGIN, strip_y, CONTENT_W, HEADER_H, STRIP[1])
    cv.draw_text(BOLD, MARGIN + 10, strip_y + HEADER_H - 7,
                 STRIP[0], 14, WHITE)
    sy = strip_y + HEADER_H + 6 + 13
    for i, it in enumerate(STRIP[2]):
        sx = MARGIN + i * (strip_col_w + 12)
        yy = sy
        for ln in REGULAR.wrap(it, 12, strip_col_w):
            cv.draw_text(REGULAR, sx, yy, ln, 12, INK)
            yy += 16
    # Footer.
    cv.rect(MARGIN, FOOTER_RULE_Y, CONTENT_W, 1, INK)
    cv.draw_centered(REGULAR, PAGE_W // 2, FOOTER_RULE_Y + 18,
                     FOOTER, 11, GRAY)
    return cv


REGULAR = None
BOLD = None


def main():
    global REGULAR, BOLD
    here = os.path.dirname(os.path.abspath(__file__))
    REGULAR = Font(os.path.join(here, "DejaVuSans.ttf"))
    BOLD = Font(os.path.join(here, "DejaVuSans-Bold.ttf"))
    out = sys.argv[1] if len(sys.argv) > 1 else "/output/entry.png"
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    cv = build_page()
    size = write_png(out, cv)
    total = (1 + sum(len(s[2]) for s in SECTIONS_LEFT + SECTIONS_RIGHT)
             + len(STRIP[2]))
    print("wrote %s (%d bytes), %d stories" % (out, size, total))


if __name__ == "__main__":
    main()
