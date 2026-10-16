"""
Dashboard visuals: dark trading-terminal HTML/CSS components (pure functions returning HTML strings).
Rendered with st.html in the app; ui_kit.preview() writes a standalone page for design checks.
"""
from html import escape

import numpy as np

UP, DN, WARN, ACC = "#22c55e", "#ef4444", "#f59e0b", "#38bdf8"

CSS = """
<style>
:root{--card:#0f1a2e;--card2:#14243f;--line:#1e2d4a;--txt:#e6edf7;--mut:#8ea0bf;--up:#22c55e;--dn:#ef4444;--warn:#f59e0b;--acc:#38bdf8}
.tk *{box-sizing:border-box;font-family:Inter,"Segoe UI",system-ui,sans-serif}
.tk .num, .tk .v, .tk .big, .tk .px{font-variant-numeric:tabular-nums}
.tk .ribbon{display:flex;gap:10px;flex-wrap:wrap;margin:2px 0 14px}
.tk .pill{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:9px 14px;min-width:142px;flex:1}
.tk .pill .l{font-size:10.5px;color:var(--mut);letter-spacing:.09em;text-transform:uppercase;display:flex;align-items:center;gap:6px}
.tk .pill .v{font-size:19px;font-weight:800;color:var(--txt);margin-top:2px}
.tk .up{color:var(--up)} .tk .dn{color:var(--dn)} .tk .mut{color:var(--mut)}
.tk .d{font-size:12px;font-weight:700;margin-left:6px}
.tk .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.tk .dot.live{background:var(--up);animation:tkpulse 1.6s infinite}
.tk .dot.off{background:#64748b}
@keyframes tkpulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.65)}70%{box-shadow:0 0 0 9px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
.tk .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:6px}
.tk .kpi{background:linear-gradient(160deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.tk .kpi .l{font-size:10.5px;color:var(--mut);letter-spacing:.09em;text-transform:uppercase}
.tk .kpi .big{font-size:28px;font-weight:800;color:var(--txt);line-height:1.15;margin-top:4px}
.tk .kpi .s{font-size:12px;color:var(--mut);margin-top:2px}
.tk .bar{height:7px;border-radius:6px;background:#1f2937;overflow:hidden;display:flex;margin-top:9px}
.tk .sec{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:20px 0 10px;font-weight:800;
  display:flex;align-items:center;gap:10px}
.tk .sec:after{content:"";flex:1;height:1px;background:var(--line)}
.tk .ideas{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:14px}
.tk .card{background:linear-gradient(165deg,#13233f 0%,#0c172a 100%);border:1px solid var(--line);border-radius:18px;
  padding:16px 18px 14px;position:relative;overflow:hidden}
.tk .card:before{content:"";position:absolute;inset:0 0 auto 0;height:3px}
.tk .card.ce:before{background:linear-gradient(90deg,#16a34a,#4ade80)}
.tk .card.pe:before{background:linear-gradient(90deg,#dc2626,#f87171)}
.tk .card .glow{position:absolute;width:220px;height:220px;border-radius:50%;top:-120px;right:-80px;filter:blur(10px);opacity:.18}
.tk .card.ce .glow{background:radial-gradient(circle,#22c55e,transparent 70%)}
.tk .card.pe .glow{background:radial-gradient(circle,#ef4444,transparent 70%)}
.tk .hdr{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;position:relative}
.tk .sym{font-size:22px;font-weight:800;color:var(--txt);letter-spacing:.01em}
.tk .sub{font-size:12px;color:var(--mut);margin-top:2px}
.tk .px{font-size:15px;font-weight:700;color:var(--txt)}
.tk .tag{display:inline-block;padding:6px 13px;border-radius:999px;font-weight:800;font-size:12px;letter-spacing:.07em;white-space:nowrap}
.tk .tag.ce{background:rgba(34,197,94,.14);color:#4ade80;border:1px solid rgba(74,222,128,.45);box-shadow:0 0 18px rgba(34,197,94,.25)}
.tk .tag.pe{background:rgba(239,68,68,.14);color:#f87171;border:1px solid rgba(248,113,113,.45);box-shadow:0 0 18px rgba(239,68,68,.25)}
.tk .contract{margin:12px 0 10px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.tk .contract b{font-size:16px;color:var(--txt)}
.tk .contract span{font-size:11.5px;color:var(--mut)}
.tk .nums{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.tk .num{background:rgba(255,255,255,.035);border:1px solid var(--line);border-radius:12px;padding:8px 10px}
.tk .num .l{font-size:9.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.1em}
.tk .num .v{font-size:19px;font-weight:800;color:var(--txt);margin-top:2px}
.tk .num .p{font-size:11px;font-weight:700}
.tk .num.entry{border-color:rgba(56,189,248,.45);background:rgba(56,189,248,.07)}
.tk .ladder{position:relative;height:46px;margin:14px 6px 2px}
.tk .ladder .track{position:absolute;top:13px;left:0;right:0;height:6px;border-radius:4px;background:#1f2937}
.tk .ladder .seg{position:absolute;top:13px;height:6px}
.tk .ladder .mk{position:absolute;top:7px;width:2px;height:18px;background:#94a3b8;transform:translateX(-1px)}
.tk .ladder .lb{position:absolute;top:28px;font-size:10px;color:var(--mut);transform:translateX(-50%);white-space:nowrap}
.tk .ladder .lb b{color:var(--txt);font-weight:700}
.tk .ladder .now{position:absolute;top:9px;width:14px;height:14px;border-radius:50%;background:var(--acc);
  transform:translateX(-50%);box-shadow:0 0 0 3px rgba(56,189,248,.25),0 0 14px var(--acc)}
.tk .foot{display:flex;align-items:center;gap:12px;margin-top:10px}
.tk .ring{width:56px;height:56px;min-width:56px;border-radius:50%;display:grid;place-items:center;
  background:conic-gradient(var(--c) calc(var(--p) * 1%),#1f2937 0)}
.tk .ring span{width:44px;height:44px;border-radius:50%;background:#0e1a2e;display:grid;place-items:center;
  font-weight:800;font-size:15px;color:var(--txt)}
.tk .chips{display:flex;flex-wrap:wrap;gap:6px}
.tk .chip{font-size:10.5px;padding:3px 9px;border-radius:999px;background:rgba(56,189,248,.09);color:#7dd3fc;border:1px solid rgba(56,189,248,.25)}
.tk .chip.g{background:rgba(148,163,184,.08);color:#cbd5e1;border-color:rgba(148,163,184,.25)}
.tk .chip.w{background:rgba(245,158,11,.09);color:#fbbf24;border-color:rgba(245,158,11,.3)}
.tk .warn{font-size:11.5px;color:#fbbf24;margin-top:9px;line-height:1.45;border-top:1px dashed var(--line);padding-top:8px}
.tk .size{font-size:11.5px;color:var(--mut);margin-top:6px}
.tk .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:10px}
.tk .tile{border-radius:14px;padding:11px 12px 9px;border:1px solid var(--line);position:relative;overflow:hidden}
.tk .tile .t1{display:flex;justify-content:space-between;align-items:baseline}
.tk .tile .n{font-weight:800;color:var(--txt);font-size:14.5px}
.tk .tile .sc{font-weight:800;font-size:13px}
.tk .tile .t2{font-size:12px;color:var(--mut);margin:2px 0 4px}
.tk .tile .t3{font-size:11px;color:#cbd5e1;margin-top:3px}
.tk .empty{background:var(--card);border:1px dashed var(--line);border-radius:16px;padding:22px;color:var(--mut);text-align:center}
.kt *{box-sizing:border-box;font-family:Inter,"Segoe UI",system-ui,sans-serif}
.kt .strip{display:flex;gap:26px;flex-wrap:wrap;align-items:baseline;padding:8px 2px 12px;border-bottom:1px solid #1e2d4a;margin-bottom:12px}
.kt .strip .it{display:flex;gap:8px;align-items:baseline;white-space:nowrap}
.kt .strip .n{font-size:12px;color:#8ea0bf;font-weight:600;letter-spacing:.03em}
.kt .strip .v{font-size:15px;font-weight:700;color:#e6edf7;font-variant-numeric:tabular-nums}
.kt .strip .c{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}
.kt .up{color:#4caf50}.kt .dn{color:#ef5350}.kt .mut{color:#8ea0bf}
.kt .qh{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;padding:4px 0 10px;border-bottom:1px solid #1e2d4a}
.kt .qh .s{font-size:22px;font-weight:800;color:#e6edf7}
.kt .qh .ex{font-size:11px;color:#8ea0bf;border:1px solid #2b3b5c;border-radius:4px;padding:1px 6px;margin-left:8px;vertical-align:middle}
.kt .qh .p{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums;color:#e6edf7}
.kt .qh .pc{font-size:14px;font-weight:600;margin-left:8px}
.kt .btns{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 4px}
.kt .b{flex:1;min-width:210px;border-radius:6px;padding:10px 14px;font-weight:700;color:#fff;font-size:14px}
.kt .b small{display:block;font-weight:500;font-size:11.5px;opacity:.9;margin-top:2px}
.kt .b.buy{background:#2e7d32}.kt .b.sell{background:#c62828}.kt .b.off{background:#263247;color:#94a3b8}
.kt table.depth{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
.kt table.depth th{color:#8ea0bf;font-weight:600;text-align:right;padding:5px 8px;border-bottom:1px solid #1e2d4a}
.kt table.depth td{text-align:right;padding:4px 8px;border-bottom:1px solid #131f36}
.kt table.depth td.bid{color:#64b5f6}.kt table.depth td.ask{color:#ef5350}
.kt table.depth tr.tot td{font-weight:700;color:#e6edf7;border-top:1px solid #1e2d4a}
.kt .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px 18px;margin-top:12px;font-size:12.5px}
.kt .stats .l{color:#8ea0bf}.kt .stats .v{color:#e6edf7;font-weight:600;font-variant-numeric:tabular-nums}
.kt .bar{height:5px;background:#1f2937;border-radius:4px;overflow:hidden;display:flex;margin-top:8px}
.tk .tag.nt{background:rgba(148,163,184,.12);color:#cbd5e1;border:1px solid rgba(148,163,184,.4)}
</style>
"""


def _f(x, nd=2):
    return f"{x:,.{nd}f}"


def _chg(c):
    if c is None or not np.isfinite(c):
        return ""
    return f'<span class="d {"up" if c >= 0 else "dn"}">{"▲" if c >= 0 else "▼"} {abs(c) * 100:.2f}%</span>'


def sparkline(vals, w=120, h=34, color=None):
    v = [float(x) for x in vals if x is not None and np.isfinite(x)]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1.0
    color = color or (UP if v[-1] >= v[0] else DN)
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 3 - (x - lo) / rng * (h - 6):.1f}" for i, x in enumerate(v))
    area = f"0,{h} {pts} {w},{h}"
    gid = f"g{abs(hash((tuple(v[:3]), len(v), color))) % 10 ** 8}"
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".35"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="{area}" fill="url(#{gid})"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/></svg>')


def ribbon(items, live, stamp):
    """items: [(label, value, change or None)]."""
    pills = [f'<div class="pill"><div class="l"><span class="dot {"live" if live else "off"}"></span>'
             f'{"Market live" if live else "Market closed"}</div><div class="v">{escape(stamp)}</div></div>']
    for lbl, val, chg in items:
        pills.append(f'<div class="pill"><div class="l">{escape(lbl)}</div><div class="v">{val}{_chg(chg)}</div></div>')
    return f'<div class="tk"><div class="ribbon">{"".join(pills)}</div></div>'


def kpis(n_call, n_put, bull, bear, neutral, pnl_today, n_closed, n_open, unreal, win):
    tot = max(bull + bear + neutral, 1)
    n_ideas = n_call + n_put
    split = (f'<div class="bar"><div style="width:{n_call / max(n_ideas, 1) * 100:.0f}%;background:var(--up)"></div>'
             f'<div style="width:{n_put / max(n_ideas, 1) * 100:.0f}%;background:var(--dn)"></div></div>') if n_ideas else \
        '<div class="bar"></div>'
    mood = (f'<div class="bar"><div style="width:{bull / tot * 100:.0f}%;background:var(--up)"></div>'
            f'<div style="width:{neutral / tot * 100:.0f}%;background:#475569"></div>'
            f'<div style="width:{bear / tot * 100:.0f}%;background:var(--dn)"></div></div>')
    mood_txt = "Bullish" if bull > bear * 1.5 else "Bearish" if bear > bull * 1.5 else "Mixed"
    pc = "up" if pnl_today >= 0 else "dn"
    return (f'<div class="tk"><div class="kpis">'
            f'<div class="kpi"><div class="l">Trade ideas</div><div class="big">{n_ideas}</div>'
            f'<div class="s"><span class="up">{n_call} CALL</span> · <span class="dn">{n_put} PUT</span></div>{split}</div>'
            f'<div class="kpi"><div class="l">Watchlist mood</div><div class="big {"up" if mood_txt == "Bullish" else "dn" if mood_txt == "Bearish" else ""}">{mood_txt}</div>'
            f'<div class="s">{bull} bullish · {neutral} neutral · {bear} bearish</div>{mood}</div>'
            f'<div class="kpi"><div class="l">Paper P&amp;L today</div><div class="big {pc}">₹{pnl_today:,.0f}</div>'
            f'<div class="s">{n_closed} closed today</div></div>'
            f'<div class="kpi"><div class="l">Open paper trades</div><div class="big">{n_open}</div>'
            f'<div class="s">₹{unreal:,.0f} unrealized</div></div>'
            f'<div class="kpi"><div class="l">Paper win rate</div><div class="big">{"–" if win is None else f"{win * 100:.0f}%"}</div>'
            f'<div class="s">all closed paper trades</div></div>'
            f'</div></div>')


def section(title):
    return f'<div class="tk"><div class="sec">{escape(title)}</div></div>'


def _ladder(stop, entry, t1, t2, now):
    pts = [stop, entry, t1, t2, now]
    lo, hi = min(pts), max(pts)
    pad = (hi - lo) * 0.06 or 1.0
    lo, hi = lo - pad, hi + pad
    pos = lambda x: (x - lo) / (hi - lo) * 100
    a, b = sorted((pos(stop), pos(entry)))
    c, e = sorted((pos(entry), pos(t2)))
    marks = "".join(f'<div class="mk" style="left:{pos(x):.1f}%"></div>' for x in (stop, entry, t1, t2))
    labels = "".join(f'<div class="lb" style="left:{pos(x):.1f}%">{n} <b>{_f(x)}</b></div>'
                     for n, x in (("SL", stop), ("Entry", entry), ("T1", t1), ("T2", t2)))
    return (f'<div class="ladder"><div class="track"></div>'
            f'<div class="seg" style="left:{a:.1f}%;width:{b - a:.1f}%;background:linear-gradient(90deg,#7f1d1d,#ef4444)"></div>'
            f'<div class="seg" style="left:{c:.1f}%;width:{e - c:.1f}%;background:linear-gradient(90deg,#15803d,#4ade80)"></div>'
            f'{marks}<div class="now" style="left:{pos(now):.1f}%" title="Now {_f(now)}"></div>{labels}</div>')


def idea_card(sym, a, chg=None, spark=None):
    p, row, spec = a["plan"], a["row"], a["spec"]
    ce = p["kind"] == "CE"
    k = "ce" if ce else "pe"
    conf = int(a["conf"] or 0)
    ring_c = UP if conf >= 60 else WARN if conf >= 40 else DN
    pc = lambda x: f'<div class="p {"up" if x >= p["prem"] else "dn"}">{(x / p["prem"] - 1) * 100:+.0f}%</div>' if p["prem"] else ""
    exp_txt = p["expiry"] or f"~{p['dte']:.0f}d"
    chips = [f'<span class="chip">{escape(s.strip())}</span>' for s in (row.Setups or "").split(",") if s.strip()]
    chips += [f'<span class="chip g">{escape(row.Regime)} · score {row.Score:+.1f}</span>']
    if a.get("bt"):
        bt = a["bt"]
        chips.append(f'<span class="chip {"g" if bt["t1"] >= .5 else "w"}">T1 hit {bt["t1"] * 100:.0f}% of '
                     f'{bt["trades"]} past signals</span>')
        chips.append(f'<span class="chip g">avg {bt["exp"]:+.2f}R</span>')
    if not p["live"]:
        chips.append('<span class="chip w">model premium</span>')
    warns = [n for n in p["notes"] if "budget" not in n]
    size = (f"📦 {p['lots']} lot × {p['lot']} · max risk ₹{p['risk']:,.0f}" if p["lots"]
            else f"📦 1 lot ({p['lot']}) risks ₹{(p['prem'] - p['p_sl']) * p['lot']:,.0f}, above your risk budget")
    return (f'<div class="card {k}"><div class="glow"></div>'
            f'<div class="hdr"><div><div class="sym">{escape(sym)}</div>'
            f'<div class="sub"><span class="px">{_f(a["ltp"])}</span>{_chg(chg)}</div></div>'
            f'<div style="text-align:right">{spark or ""}<div style="margin-top:4px"><span class="tag {k}">'
            f'{"BUY CALL ▲" if ce else "BUY PUT ▼"}</span></div></div></div>'
            f'<div class="contract"><b>{escape(spec["name"])} {p["strike"]:g} {p["kind"]}</b>'
            f'<span>exp {escape(str(exp_txt))} · Δ {p["delta"]:+.2f} · θ {-p["theta"]:.1f}/day</span></div>'
            f'<div class="nums">'
            f'<div class="num entry"><div class="l">Entry</div><div class="v">{_f(p["prem"])}</div><div class="p mut">premium</div></div>'
            f'<div class="num"><div class="l">Stop</div><div class="v">{_f(p["p_sl"])}</div>{pc(p["p_sl"])}</div>'
            f'<div class="num"><div class="l">Target 1</div><div class="v">{_f(p["p_t1"])}</div>{pc(p["p_t1"])}</div>'
            f'<div class="num"><div class="l">Target 2</div><div class="v">{_f(p["p_t2"])}</div>{pc(p["p_t2"])}</div></div>'
            f'{_ladder(p["stop"], p["entry"], p["t1"], p["t2"], a["ltp"])}'
            f'<div class="foot"><div class="ring" style="--p:{conf};--c:{ring_c}"><span>{conf}</span></div>'
            f'<div><div class="chips">{"".join(chips)}</div><div class="size">{size}</div></div></div>'
            + (f'<div class="warn">⚠ {" · ".join(escape(w) for w in warns[:2])}</div>' if warns else "")
            + '</div>')


def ideas_grid(cards):
    if not cards:
        return ('<div class="tk"><div class="empty">No active setups right now.<br>'
                'The heat-map below shows where momentum is building.</div></div>')
    return f'<div class="tk"><div class="ideas">{"".join(cards)}</div></div>'


def tile(sym, a, chg=None, spark_vals=None):
    row, tg, sa = a["row"], a["tg"], a["sa"]
    s = float(row.Score)
    inten = min(abs(s) / 16, 1)
    base = (34, 197, 94) if s > 0 else (239, 68, 68) if s < 0 else (100, 116, 139)
    bg = f"linear-gradient(160deg, rgba({base[0]},{base[1]},{base[2]},{.10 + .32 * inten:.2f}), rgba(15,26,46,.95))"
    k = "CE" if s >= 0 else "PE"
    side = sa["sides"][k]
    zs = tg["up"] if k == "CE" else tg["down"]
    st = a["status"]
    state = ("BUY CALL" if st.startswith("BUY CALL") else "BUY PUT" if st.startswith("BUY PUT")
             else "CALL bias" if "CALL" in st else "PUT bias" if "PUT" in st else "Neutral")
    col = "up" if s > 0 else "dn" if s < 0 else "mut"
    sp = sparkline(spark_vals or [], w=150, h=26) if spark_vals is not None else ""
    return (f'<div class="tile" style="background:{bg}"><div class="t1"><span class="n">{escape(sym)}</span>'
            f'<span class="sc {col}">{s:+.1f}</span></div>'
            f'<div class="t2">{_f(a["ltp"])}{_chg(chg)}</div>{sp}'
            f'<div class="t3"><b class="{col}">{state}</b> · {side["strike"]:g} {k} @ {_f(side["prem"])}</div>'
            + (f'<div class="t3 mut">T1 {_f(zs[0]["level"])} · {zs[0]["prob"] * 100:.0f}% touch</div>' if zs else "")
            + '</div>')


def tiles_grid(ts):
    return f'<div class="tk"><div class="tiles">{"".join(ts)}</div></div>'


def preview(path, body_html):
    """Standalone page for design checks."""
    with open(path, "w", encoding="utf8") as f:
        f.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<style>body{background:#0b1220;margin:0;padding:24px 28px;color:#e6edf7}</style>'
                f'{CSS}</head><body>{body_html}</body></html>')


def kite_strip(items):
    """Broker-style index ticker: [(name, value, abs change or None, pct change or None)]."""
    cells = []
    for name, val, ch, pc in items:
        cls = "up" if (ch or 0) >= 0 else "dn"
        chg = (f'<span class="c {cls}">{ch:+,.2f} ({pc:+.2f}%)</span>' if ch is not None and pc is not None else "")
        cells.append(f'<div class="it"><span class="n">{escape(name)}</span><span class="v">{val}</span>{chg}</div>')
    return f'<div class="kt"><div class="strip">{"".join(cells)}</div></div>'


def quote_header(sym, exch, ltp, ch, pc, buy=None, sell=None):
    """Symbol header with price/change and two order-pad style action buttons (display only, no orders)."""
    cls = "up" if (ch or 0) >= 0 else "dn"
    chg = f'<span class="pc {cls}">{ch:+,.2f} ({pc:+.2f}%)</span>' if ch is not None and pc is not None else ""

    def btn(x, kind):
        if not x:
            return f'<div class="b off">{"BUY CALL" if kind == "buy" else "BUY PUT"}<small>no signal</small></div>'
        return f'<div class="b {kind}">{escape(x[0])}<small>{escape(x[1])}</small></div>'
    return (f'<div class="kt"><div class="qh"><div><span class="s">{escape(sym)}</span><span class="ex">{escape(exch)}</span></div>'
            f'<div><span class="p">{_f(ltp)}</span>{chg}</div></div>'
            f'<div class="btns">{btn(buy, "buy")}{btn(sell, "sell")}</div></div>')


def depth_html(q):
    """Kite-style 5-level market depth + quote stats from an Upstox full quote."""
    d = q.get("depth") or {}
    buy, sell = (d.get("buy") or [])[:5], (d.get("sell") or [])[:5]
    rows = []
    for i in range(5):
        b = buy[i] if i < len(buy) else {}
        s = sell[i] if i < len(sell) else {}
        rows.append(f'<tr><td class="bid">{_f(b.get("price") or 0)}</td><td class="bid">{b.get("orders") or 0}</td>'
                    f'<td class="bid">{(b.get("quantity") or 0):,}</td><td class="ask">{_f(s.get("price") or 0)}</td>'
                    f'<td class="ask">{s.get("orders") or 0}</td><td class="ask">{(s.get("quantity") or 0):,}</td></tr>')
    tb, ts = q.get("total_buy_quantity") or 0, q.get("total_sell_quantity") or 0
    rows.append(f'<tr class="tot"><td colspan="3">Total {int(tb):,}</td><td colspan="3">Total {int(ts):,}</td></tr>')
    share = tb / (tb + ts) * 100 if tb + ts else 50
    o = q.get("ohlc") or {}
    ltt = q.get("last_trade_time")
    try:
        from datetime import datetime
        ltt = datetime.fromtimestamp(int(ltt) / 1000).strftime("%H:%M:%S")
    except Exception:
        ltt = "-"
    stat = lambda l, v: f'<div><div class="l">{l}</div><div class="v">{v}</div></div>'
    prev = (q.get("last_price") or 0) - (q.get("net_change") or 0)
    stats = "".join([stat("Open", _f(o.get("open") or 0)), stat("High", _f(o.get("high") or 0)),
                     stat("Low", _f(o.get("low") or 0)), stat("Prev. close", _f(prev)),
                     stat("Volume", f'{int(q.get("volume") or 0):,}'), stat("Avg. price", _f(q.get("average_price") or 0)),
                     stat("Lower circuit", _f(q.get("lower_circuit_limit") or 0)),
                     stat("Upper circuit", _f(q.get("upper_circuit_limit") or 0)), stat("Last trade", ltt)])
    return (f'<div class="kt"><table class="depth"><tr><th>Bid</th><th>Orders</th><th>Qty.</th><th>Offer</th>'
            f'<th>Orders</th><th>Qty.</th></tr>{"".join(rows)}</table>'
            f'<div class="bar"><div style="width:{share:.0f}%;background:#64b5f6"></div>'
            f'<div style="width:{100 - share:.0f}%;background:#ef5350"></div></div>'
            f'<div class="stats">{stats}</div></div>')


def reco_card(r, chg=None, spark=None, gamma=None):
    """One-stock recommendation card: BUY CALL / BUY PUT (now or on trigger) or NO TRADE with both conditional plans."""
    kind = r.get("kind")
    k = "ce" if kind == "CE" else "pe" if kind == "PE" else ""
    tag_cls = k or "nt"
    conf = int(r.get("conf") or 0)
    ring_c = UP if conf >= 60 else WARN if conf >= 40 else DN
    head = (f'<div class="hdr"><div><div class="sym">{escape(r["sym"])}</div>'
            f'<div class="sub"><span class="px">{_f(r["ltp"])}</span>{_chg(chg)} · score {r["score"]:+.1f} · '
            f'{escape(r["regime"])}</div></div><div style="text-align:right">{spark or ""}<div style="margin-top:4px">'
            f'<span class="tag {tag_cls}">{escape(r["action"])}{" ▲" if kind == "CE" else " ▼" if kind == "PE" else ""}'
            f'</span></div></div></div>')

    def plan_block(x, title=None):
        dr = 1 if x["kind"] == "CE" else -1
        pc = lambda v: f'<div class="p {"up" if v >= x["prem"] else "dn"}">{(v / x["prem"] - 1) * 100:+.0f}%</div>'
        when = ("now" if r["mode"] == "now" else f'if {"above" if dr > 0 else "below"} {_f(x["u_entry"])}')
        return ((f'<div class="contract"><b>{escape(title)}</b></div>' if title else "")
                + f'<div class="contract"><b>{escape(r["sym"])} {x["strike"]:g} {x["kind"]}</b>'
                f'<span>{"exp " + escape(str(r.get("expiry"))) + " · " if r.get("expiry") else ""}Δ {x["delta"]:+.2f}'
                f'{" · Upstox POP " + format(x["pop"], ".0f") + "%" if x.get("pop") is not None else ""}'
                f'{" · model premium" if not x.get("live") else ""}</span></div>'
                f'<div class="nums"><div class="num entry"><div class="l">Entry {escape(when)}</div>'
                f'<div class="v">{_f(x["prem"])}</div><div class="p mut">premium</div></div>'
                f'<div class="num"><div class="l">Stop</div><div class="v">{_f(x["sl"])}</div>{pc(x["sl"])}</div>'
                f'<div class="num"><div class="l">Target 1</div><div class="v">{_f(x["t1"])}</div>{pc(x["t1"])}</div>'
                f'<div class="num"><div class="l">Target 2</div><div class="v">{_f(x["t2"])}</div>{pc(x["t2"])}</div></div>'
                + _ladder(x["u_sl"], x["u_entry"], x["u_t1"], x["u_t2"], r["ltp"]))

    if r["mode"] == "none":
        up, dn = r.get("alt") or (None, None)
        body = "".join(plan_block(x, t) for x, t in ((up, "Plan A · CALL only on a break up"), (dn, "Plan B · PUT only on a break down")) if x)
    else:
        body = plan_block(r)
    chips = [f'<span class="chip">{escape(s.strip())}</span>' for s in (r.get("setup") or "").split(",") if s.strip()]
    chips += [f'<span class="chip g">{escape(w)}</span>' for w in r["why"][:4]]
    if r.get("bt"):
        chips.append(f'<span class="chip {"g" if r["bt"]["t1"] >= .5 else "w"}">T1 hit {r["bt"]["t1"] * 100:.0f}% '
                     f'of {r["bt"]["trades"]} past signals</span>')
    if gamma:
        chips.append(f'<span class="chip">{escape(gamma)}</span>')
    ring = (f'<div class="ring" style="--p:{conf};--c:{ring_c}"><span>{conf}</span></div>' if r["mode"] != "none" else "")
    notes = r.get("notes") or []
    return (f'<div class="tk"><div class="card {k}" style="max-width:760px"><div class="glow"></div>{head}{body}'
            f'<div class="foot">{ring}<div class="chips">{"".join(chips)}</div></div>'
            + (f'<div class="warn">⚠ {" · ".join(escape(n) for n in notes[:3])}</div>' if notes else "")
            + '</div></div>')
