"""Group a page's glyphs into columns, rows and expression groups.

MuPDF's own block/line structure is useless for maths here: it shatters every
formula into a dozen one-glyph blocks while keeping prose in tidy paragraphs.
So grouping is done from scratch on glyph geometry.

Rows come from a horizontal projection.  A run of prose leaves a clean gap
between successive lines, while every piece of a built-up formula -- numerator,
bar, denominator, an operator and its limits -- overlaps the band of its
neighbours, so one display equation projects to a single tall band.  Bands that
turn out to hold several full-size text baselines are then split back into
prose lines.
"""

import statistics
from dataclasses import dataclass, field
from . import pagemodel as PM

TOP_MARGIN = 56.0
BOT_MARGIN = 700.0
BAND_GAP = 0.6           # y-gap that separates two bands


@dataclass
class Group:
    glyphs: list
    rules: list = field(default_factory=list)
    region_left: float = 0.0     # left margin of the column this group sits in
    region_width: float = 0.0
    page: int = 0
    region_idx: int = 0
    measure_left: float = 0.0    # the text measure this group is centred against
    measure_right: float = 0.0
    margin: bool = False         # sits in the margin beside the text block
    order: float = 0.0           # baseline used for reading order

    @property
    def x0(self):
        return min(g.x0 for g in self.glyphs)

    @property
    def x1(self):
        return max(g.x1 for g in self.glyphs)

    @property
    def y0(self):
        return min(g.y0 for g in self.glyphs)

    @property
    def y1(self):
        return max(g.y1 for g in self.glyphs)

    @property
    def base(self):
        big = self.size
        cand = [g.oy for g in self.glyphs if g.size >= big * 0.92]
        return statistics.median(cand) if cand else self.y1

    @property
    def size(self):
        return max(g.size for g in self.glyphs)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def text(self):
        if self.margin:
            # a margin label is stacked over several lines, so read it by line
            rows, cur = [], []
            for g in sorted(self.glyphs, key=lambda g: (g.oy, g.ox)):
                if cur and g.oy - cur[-1].oy > self.size * 0.5:
                    rows.append(cur)
                    cur = []
                cur.append(g)
            if cur:
                rows.append(cur)
            return " ".join("".join(g.text for g in sorted(r, key=lambda g: g.ox))
                            for r in rows)
        return "".join(g.text for g in sorted(self.glyphs, key=lambda g: g.ox))

    def math_ratio(self):
        sig = [g for g in self.glyphs if not is_blank(g)]
        if not sig:
            return 0.0
        return sum(1 for g in sig if g.is_math_font or g.is_italic) / len(sig)

    def head_ratio(self):
        sig = [g for g in self.glyphs if not is_blank(g)]
        if not sig:
            return 0.0
        return sum(1 for g in sig if g.is_head) / len(sig)

    @property
    def order_y(self):
        """Baseline used for reading order.

        A merged margin label spans two lines, so its median baseline falls
        between them; it belongs with the first line it titles.
        """
        if self.margin:
            return min(g.oy for g in self.glyphs)
        return self.base

    def baselines(self, tol=1.2):
        out = []
        for g in sorted(self.glyphs, key=lambda g: g.oy):
            if not out or g.oy - out[-1] > tol:
                out.append(g.oy)
        return out


def strip_furniture(glyphs, rules):
    """Drop the running head and folio.  Space glyphs are kept -- they carry the
    word boundaries -- but they have degenerate boxes, so every geometric step
    below works on `solid` glyphs only and reattaches spaces at the end."""
    gs = [g for g in glyphs if TOP_MARGIN < g.oy < BOT_MARGIN]
    rs = [r for r in rules if TOP_MARGIN < r.y < BOT_MARGIN]
    return gs, rs


is_blank = PM.is_blank


def solid(glyphs):
    return [g for g in glyphs if not is_blank(g)]


def body_font_size(glyphs):
    sizes = [round(g.size, 1) for g in glyphs if g.is_roman]
    if not sizes:
        return 10.0
    try:
        return statistics.mode(sizes)
    except statistics.StatisticsError:
        return 10.0


def find_gutter(glyphs, min_width=6.0):
    """-> (x0, x1) of the vertical gutter if this region is set in two columns.

    Coverage is counted per glyph, not per row: a row's bounding box in a
    two-column region already spans both columns, so row extents can never
    reveal the gap between them.
    """
    body = [g for g in solid(glyphs) if g.size >= 7.0]
    if len(body) < 120:
        return None
    left = min(g.x0 for g in body)
    right = max(g.x1 for g in body)
    width = right - left
    if width < 300:
        return None

    n = int(width) + 2
    cover = [0] * n
    for g in body:
        a = max(0, int(g.x0 - left))
        b = min(n - 1, int(g.x1 - left) + 1)
        for k in range(a, b):
            cover[k] += 1

    lo, hi = int(n * 0.30), int(n * 0.70)
    best, run = None, None
    for k in range(lo, hi + 1):
        if cover[k] <= 1:
            if run is None:
                run = k
        else:
            if run is not None:
                if best is None or k - run > best[1] - best[0]:
                    best = (run, k)
                run = None
    if run is not None and (best is None or hi + 1 - run > best[1] - best[0]):
        best = (run, hi + 1)
    if best is None or (best[1] - best[0]) < min_width:
        return None

    g0, g1 = left + best[0], left + best[1]
    if sum(1 for g in body if g.cx < g0) < 40 or sum(1 for g in body if g.cx > g1) < 40:
        return None
    # A handful of lines can leave a clear vertical gap by chance -- three
    # footnotes that happen to break at the same x look exactly like a gutter.
    # A column break needs a region deep enough for the gap to mean something.
    if len(bands(body)) < 8:
        return None
    return g0, g1


# --------------------------------------------------------------------------
# horizontal banding
# --------------------------------------------------------------------------
def bands(glyphs, gap=BAND_GAP):
    """Split glyphs into maximal vertically-connected bands."""
    glyphs = solid(glyphs)
    if not glyphs:
        return []
    ivs = sorted(((g.y0, g.y1, g) for g in glyphs), key=lambda t: t[0])
    out, cur, hi = [], [ivs[0][2]], ivs[0][1]
    for y0, y1, g in ivs[1:]:
        if y0 - hi > gap:
            out.append(cur)
            cur, hi = [g], y1
        else:
            cur.append(g)
            hi = max(hi, y1)
    out.append(cur)
    return out


TALL_MARKS = PM.TALL_MARKS
_is_tall = PM.is_tall
_tall_spans = PM.tall_spans


def split_prose_band(band, body_size, rules=(), region_width=None, region_left=None):
    """Cut a band back into the lines it is made of.

    A band can hold more than one line for two reasons: tall inline maths -- a
    binomial, a fraction -- bridges consecutive prose lines, or a display sits
    directly against the prose around it.  Baselines are taken from every
    full-size glyph, and a baseline counts as a line when the glyphs sitting on
    it fill the measure or start at the margin.  The band is only cut if at
    least one of those lines is real prose, so that a multi-line display stays
    whole for the maths parser.
    """
    full = [g for g in band if g.size >= body_size * 0.92]
    if len(full) < 6:
        return [band]
    clusters = []
    for g in sorted(full, key=lambda g: g.oy):
        if not clusters or g.oy - clusters[-1][-1].oy > body_size * 0.55:
            clusters.append([g])
        else:
            clusters[-1].append(g)
    if len(clusters) < 2:
        return [band]

    if region_width is None:
        region_width = max(g.x1 for g in band) - min(g.x0 for g in band)
    if region_left is None:
        region_left = min(g.x0 for g in band)

    centres = [statistics.median([g.oy for g in c]) for c in clusters]

    def row_of(y):
        return min(range(len(centres)), key=lambda i: abs(y - centres[i]))

    # measure each baseline over everything that sits on it, not just the
    # upright text: a display line is mostly italic and symbol fonts
    extent = [[1e9, -1e9] for _ in centres]
    romans = [0] * len(centres)
    for g in band:
        k = row_of(g.oy)
        extent[k][0] = min(extent[k][0], g.x0)
        extent[k][1] = max(extent[k][1], g.x1)
        if g.is_roman and g.text.isalpha() and g.size >= body_size * 0.92:
            romans[k] += 1

    rows_ok = [i for i, (a, b) in enumerate(extent)
               if b - a > region_width * 0.55 or a <= region_left + 3]
    if len(rows_ok) < 2 or not any(romans[i] >= 8 for i in rows_ok):
        return [band]

    tall_ids = {id(g) for g in band if _is_tall(g)}

    def on_a_baseline(g):
        return any(abs(g.oy - c) <= 2.0 for c in centres)

    rows = [[] for _ in centres]
    claimed = set()
    for a, b in _tall_spans(band, rules):
        # Only the built-up part of the formula is claimed: its delimiters and
        # the pieces that sit off every text baseline.  Ordinary words on the
        # line above or below happen to fall in the same narrow x-range and
        # must stay with their own line.
        cluster = [g for g in band if a <= g.cx <= b
                   and (id(g) in tall_ids or not on_a_baseline(g))]
        if not cluster:
            continue
        mid = (min(g.y0 for g in cluster) + max(g.y1 for g in cluster)) / 2
        k = row_of(mid)
        for g in cluster:
            claimed.add(id(g))
            rows[k].append(g)
    for g in band:
        if id(g) not in claimed:
            rows[row_of(g.oy)].append(g)
    for r in rows:
        r.sort(key=lambda g: g.ox)
    return [r for r in rows if r]


def attach_rules(groups, rules):
    for r in rules:
        best, score = None, 0.0
        for grp in groups:
            if grp.x0 - 3 <= r.x0 and r.x1 <= grp.x1 + 3 and grp.y0 - 4 <= r.y <= grp.y1 + 4:
                s = 1.0 / (1.0 + abs(r.y - grp.base))
                if s > score:
                    best, score = grp, s
        if best is None:
            for grp in groups:
                if grp.y0 - 6 <= r.y <= grp.y1 + 6:
                    best = grp
                    break
        if best is not None:
            best.rules.append(r)
    return groups


def hsplit(glyphs, gap):
    """Split glyphs into vertical regions separated by white bands wider than `gap`."""
    gs = solid(glyphs)
    if not gs:
        return []
    ivs = sorted(((g.y0, g.y1, g) for g in gs), key=lambda t: t[0])
    out, cur, hi = [], [ivs[0][2]], ivs[0][1]
    for y0, y1, g in ivs[1:]:
        if y0 - hi > gap:
            out.append(cur)
            cur, hi = [g], y1
        else:
            cur.append(g)
            hi = max(hi, y1)
    out.append(cur)
    return out


def xy_cut(glyphs, body_size, depth=0):
    """Recursive XY-cut: alternate horizontal region splits and column splits.

    The page is not uniform -- a chapter's body is one indented column, while
    the Summary and Problems below it are set in two full-width columns that
    start further left.  Cutting on white space recursively finds each region's
    own layout instead of forcing one decision on the whole page.
    """
    gs = solid(glyphs)
    if not gs:
        return []
    if depth >= 4:
        return [gs]

    regions = hsplit(gs, body_size * 0.95) if depth == 0 else [gs]
    out = []
    for reg in regions:
        gut = find_gutter(reg)
        if gut is None:
            if depth == 0 and len(regions) > 1:
                out.extend(xy_cut(reg, body_size, depth + 1))
            else:
                out.append(reg)
            continue
        g0, g1 = gut
        mid = (g0 + g1) / 2
        cross_ids = {id(g) for g in reg if g.x0 < g0 - 1 and g.x1 > g1 + 1}
        cross = [g for g in reg if id(g) in cross_ids]
        left = [g for g in reg if id(g) not in cross_ids and g.cx <= mid]
        right = [g for g in reg if id(g) not in cross_ids and g.cx > mid]
        if cross:
            out.append(cross)
        out.extend(xy_cut(left, body_size, depth + 1))
        out.extend(xy_cut(right, body_size, depth + 1))
    return out


def footnote_rule(glyphs, rules):
    """The short rule that separates footnotes from the text block, if present."""
    body = solid(glyphs)
    if not body:
        return None
    # the separator aligns with the text measure, which is not the leftmost
    # glyph on the page -- a running head or a margin label starts further left
    starts = []
    for band in bands(body):
        x0 = min(g.x0 for g in band)
        if max(g.x1 for g in band) - x0 > 150:
            starts.append(round(x0, 1))
    if not starts:
        return None
    try:
        left = statistics.mode(starts)
    except statistics.StatisticsError:
        left = min(starts)
    width = max(g.x1 for g in body) - left
    mid = (min(g.y0 for g in body) + max(g.y1 for g in body)) / 2
    best = None
    for r in rules:
        if not r.horizontal or r.y <= mid:
            continue
        if not (25 < r.length < width * 0.45):
            continue
        if abs(r.x0 - left) > 24:
            continue
        above = sum(1 for g in body if g.y1 < r.y - 1)
        below = sum(1 for g in body if g.y0 > r.y + 1)
        if above < 30 or below < 10:
            continue
        if best is None or r.y < best.y:
            best = r
    return best


def _regions(glyphs, rules, bs):
    """Regions in reading order, footnotes kept apart from the text block.

    Footnotes are set below a short rule, in a smaller size and often in two
    columns of their own.  Without cutting there first they band together with
    the last lines of the page and interleave.
    """
    sep = footnote_rule(glyphs, rules)
    if sep is None:
        return xy_cut(glyphs, bs)
    above = [g for g in glyphs if g.y1 <= sep.y + 1]
    below = [g for g in glyphs if g.y1 > sep.y + 1]
    out = xy_cut(above, bs)
    if below:
        out += xy_cut(below, min(bs, 8.0))
    return out


def analyse(doc, pno):
    """-> (body_size, [Group]) in reading order."""
    glyphs, rules = PM.read_page(doc, pno)
    glyphs, rules = strip_furniture(glyphs, rules)
    if not glyphs:
        return 10.0, []
    bs = body_font_size(glyphs)

    frules = PM.frac_rules(rules)
    groups = []
    for ri, region in enumerate(_regions(glyphs, rules, bs)):
        rl = min(g.x0 for g in region)
        rw = max(g.x1 for g in region) - rl
        # a region sets its own measure: footnotes and two-column exercises run
        # smaller than the page's body text, and judging their baselines by the
        # page size would leave every line of them merged
        rbs = body_font_size(region) or bs
        for band in bands(region):
            y0 = min(g.y0 for g in band) - 4
            y1 = max(g.y1 for g in band) + 4
            br = [r for r in frules if y0 <= r.y <= y1]
            for row in split_prose_band(band, rbs, br, rw, rl):
                groups.append(Group(sorted(row, key=lambda g: g.ox),
                                    region_left=rl, region_width=rw, page=pno,
                                    region_idx=ri))

    _reattach_spaces(groups, [g for g in glyphs if is_blank(g)])
    attach_rules(groups, frules)
    groups = _split_margin_notes(groups)
    _set_paragraph_margin(groups)
    _set_local_measure(groups)
    _set_order(groups)
    groups.sort(key=lambda g: (g.region_idx, g.order, 0 if g.margin else 1, g.x0))
    return bs, groups


def _set_order(groups, snap=9.0):
    """Give every group the baseline it should be read at.

    A margin label is set a shade lower than the line it titles, so comparing
    raw baselines can order it after that line.  Each label is snapped onto the
    nearest body baseline instead, and the sort then puts labels first.
    """
    body = [g for g in groups if not g.margin]
    for g in body:
        g.order = g.base
    for g in groups:
        if not g.margin:
            continue
        g.order = g.order_y
        cand = [b for b in body if b.region_idx == g.region_idx
                and abs(b.base - g.order_y) <= snap]
        if cand:
            g.order = min(cand, key=lambda b: abs(b.base - g.order_y)).base


def _x_runs(glyphs, gap=11.0):
    runs, cur = [], []
    for g in sorted(glyphs, key=lambda g: g.x0):
        if cur and g.x0 - max(q.x1 for q in cur) > gap:
            runs.append(cur)
            cur = []
        cur.append(g)
    if cur:
        runs.append(cur)
    return runs


def _split_margin_notes(groups):
    """Peel off labels set in the margin beside the text block.

    Example numbers ("Example", then "2b" on the next line) are set to the left
    of the measure, so banding glues each fragment onto the body line beside it
    and the number ends up inside a word.
    """
    body = [g for g in groups if g.width > 200]
    if len(body) < 4:
        return groups
    xs = [round(g.x0, 1) for g in body]
    try:
        body_left = statistics.mode(xs)
    except statistics.StatisticsError:
        return groups

    out = []
    for g in groups:
        if g.x0 >= body_left - 4:
            out.append(g)
            continue
        runs = _x_runs(solid(g.glyphs))
        if len(runs) < 2:
            out.append(g)
            continue
        head = runs[0]
        if max(q.x1 for q in head) >= body_left - 4:
            out.append(g)
            continue
        head_ids = {id(q) for q in head}
        rest = [q for q in g.glyphs if id(q) not in head_ids]
        if not rest:
            out.append(g)
            continue
        out.append(Group(head, [], g.region_left, g.region_width, g.page,
                         g.region_idx, margin=True))
        out.append(Group(sorted(rest, key=lambda q: q.ox), g.rules,
                         g.region_left, g.region_width, g.page, g.region_idx))
    return _merge_margin_notes(out)


def _merge_margin_notes(groups):
    """A margin label runs over as many lines as it needs ("Example" / "2b").

    The fragments are not adjacent in document order -- a body line sits between
    them -- so they are collected and merged among themselves.
    """
    notes = sorted([g for g in groups if g.margin],
                   key=lambda g: (g.region_idx, g.y0))
    body = [g for g in groups if not g.margin]
    merged, dropped = [], set()
    for g in notes:
        if merged and g.region_idx == merged[-1].region_idx \
                and g.y0 - merged[-1].y1 < g.size * 1.8:
            merged[-1].glyphs = merged[-1].glyphs + g.glyphs
            dropped.add(id(g))
            continue
        merged.append(g)
    return body + merged


def _set_local_measure(groups, reach=70.0):
    """Record the text measure each group should be judged against.

    A display or a small array is isolated into a region of its own by the
    XY-cut, so it looks full-width there.  Whether it is centred can only be
    judged against the running text around it, which on a two-column page means
    the neighbouring column, not the page.
    """
    wide = [g for g in groups if g.width > 150 and not g.margin]
    for g in groups:
        near = [w for w in wide if abs(w.base - g.base) <= reach
                and min(w.x1, g.x1) - max(w.x0, g.x0) > -60]
        if not near:
            near = wide
        if not near:
            g.measure_left, g.measure_right = g.x0, g.x1
            continue
        g.measure_left = min(w.x0 for w in near)
        g.measure_right = max(w.x1 for w in near)


def _set_paragraph_margin(groups):
    """Replace each region's left edge with the measure its body text sits on.

    The region's minimum x is not the text margin: a heading, a marginal number
    or a wide figure can start further left, which would make every body line
    look indented and so start a new paragraph.
    """
    by_region = {}
    for g in groups:
        by_region.setdefault(g.region_idx, []).append(g)
    for gs in by_region.values():
        rw = gs[0].region_width
        body = [g for g in gs if g.width > rw * 0.45]
        if not body:
            continue
        xs = [round(g.x0, 1) for g in body]
        try:
            left = statistics.mode(xs)
        except statistics.StatisticsError:
            left = min(xs)
        for g in gs:
            g.region_left = left


def _reattach_spaces(groups, spaces):
    """Give each space glyph to the group whose baseline and x-range fit it."""
    if not groups:
        return
    for sp in spaces:
        best, score = None, -1e9
        for grp in groups:
            dy = abs(sp.oy - grp.base)
            if dy > max(3.0, grp.size * 0.7):
                continue
            if grp.x0 - 14 <= sp.ox <= grp.x1 + 14:
                s = -dy
                if s > score:
                    best, score = grp, s
        if best is not None:
            best.glyphs.append(sp)
    for grp in groups:
        grp.glyphs.sort(key=lambda g: g.ox)
