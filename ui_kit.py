"""
Dashboard visuals: dark trading-terminal HTML/CSS components (pure functions returning HTML strings).
Rendered with st.html in the app; ui_kit.preview() writes a standalone page for design checks.
"""
import re
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
.tk .cred{margin-top:10px;border-top:1px dashed var(--line);padding-top:9px}
.tk .cred .h{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--mut)}
.tk .cred .g{font-size:15px;font-weight:800;width:30px;height:30px;border-radius:9px;display:grid;place-items:center;border:1px solid}
.tk .cred ul{list-style:none;margin:7px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:3px 14px}
.tk .cred li{font-size:11.5px;color:#cbd5e1;line-height:1.4}
.tk .cred li b{color:var(--txt);font-weight:700}
.tk .cb{overflow-x:auto;border:1px solid var(--line);border-radius:14px;background:var(--card)}
.tk .cb table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:760px}
.tk .cb th{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);text-align:right;padding:8px 10px;
  border-bottom:1px solid var(--line);white-space:nowrap}
.tk .cb td{padding:8px 10px;border-bottom:1px solid rgba(30,45,74,.6);text-align:right;color:var(--txt);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.tk .cb th.l,.tk .cb td.l{text-align:left}
.tk .cb td.w{white-space:normal;min-width:200px;color:#cbd5e1;font-size:11.5px}
.tk .cb tr:last-child td{border-bottom:0}
.tk .cb .gb{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:8px;border:1px solid;font-weight:800}
.tk .cb .near td{color:#94a3b8}
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
.kt .chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:2px 0 10px}
.kt .chip{font-size:11.5px;padding:3px 9px;border-radius:999px;background:#131f36;color:#cbd5e1;border:1px solid #1e2d4a;
  white-space:nowrap;font-variant-numeric:tabular-nums}
.kt .chip b{color:#e6edf7;font-weight:700}
.kt .chip.up{color:#4caf50;border-color:rgba(76,175,80,.45)}.kt .chip.dn{color:#ef5350;border-color:rgba(239,83,80,.45)}
.kt .chip.w{color:#fbbf24;border-color:rgba(245,158,11,.4)}
.kt .chip.split{min-width:210px;flex:1;max-width:340px;border-radius:8px}.kt .chip.split .bar{margin-top:4px}
.kt .ocw{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid #1e2d4a;border-radius:6px}
.kt table.oc{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.kt .oc th{color:#8ea0bf;font-weight:600;text-align:right;padding:5px 7px;border-bottom:1px solid #1e2d4a;background:#0f1a2e;font-size:11px}
.kt .oc tr.side th{text-align:center;letter-spacing:.12em;font-size:10.5px}
.kt .oc td{text-align:right;padding:3px 7px;border-bottom:1px solid #131f36;color:#e6edf7;line-height:1.25}
.kt .oc td.itm{background:rgba(234,179,8,.08)}
.kt .oc td.oi{position:relative}.kt .oc td.oi span{position:relative;z-index:1}
.kt .oc .bar-ce,.kt .oc .bar-pe{position:absolute;top:3px;bottom:3px;z-index:0;border-radius:2px}
.kt .oc .bar-ce{right:0;background:rgba(239,68,68,.22)}.kt .oc .bar-pe{left:0;background:rgba(76,175,80,.22)}
.kt .oc td.k{text-align:center;font-weight:700;background:#131f36;cursor:help}
.kt .oc tr.atm td.k{outline:1px solid #4184f3;outline-offset:-1px}
.kt .oc .wc{font-size:9.5px;font-weight:800;padding:0 4px;border-radius:3px;margin-left:4px;vertical-align:1px}
.kt .oc .wc.r{background:rgba(239,83,80,.2);color:#ef5350}.kt .oc .wc.s{background:rgba(76,175,80,.2);color:#4caf50}
.kt .oc tr.spot td{height:18px;padding:0;text-align:center;color:#4184f3;font-size:11px;font-weight:700;
  border-top:1px solid #4184f3;border-bottom:1px solid #4184f3;background:rgba(65,132,243,.06)}
.kt .oc .s2{display:block;font-size:11px;color:#8ea0bf;font-weight:400}
.kt .oc .bu{font-size:9.5px;font-weight:800;margin-left:5px}
.kt .oc .bu.lb{color:#4caf50}.kt .oc .bu.sb{color:#ef5350}.kt .oc .bu.sc{color:#a5d6a7}.kt .oc .bu.lu{color:#ef9a9a}
.kt .dd{background:#0f1a2e;border:1px solid #1e2d4a;border-radius:6px;padding:10px 12px;margin-top:6px}
.kt .dd .h{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;font-size:14px;font-weight:700;color:#e6edf7}
.kt .dd .stats{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}
.kt .dd ul{margin:8px 0 0;padding-left:16px;font-size:12px;color:#cbd5e1;line-height:1.45}
.kt .pnl{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:6px 18px;align-items:end;color:#e6edf7;
  background:#0f1a2e;border:1px solid #1e2d4a;border-radius:6px;padding:9px 14px 8px;margin:2px 0 10px}
.kt .pnl .l{font-size:11px;color:#8ea0bf;letter-spacing:.03em}
.kt .pnl .v{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.kt .pnl .big .v{font-size:22px;font-weight:800}
.kt .pnl .tg{grid-column:1/-1;font-size:10px;color:#64748b;letter-spacing:.08em;text-transform:uppercase}
.kt .nt{border:1px solid #1e2d4a;border-radius:6px;background:#0f1a2e;max-height:440px;overflow-y:auto;min-width:0}
.kt .nt .it{display:flex;gap:8px;align-items:baseline;padding:6px 10px;border-bottom:1px solid #131f36;font-size:12.5px;
  color:#cbd5e1;line-height:1.35}
.kt .nt .it:last-child{border-bottom:0}
.kt .nt .it.new{background:rgba(65,132,243,.08);box-shadow:inset 3px 0 0 #4184f3}
.kt .nt .t{color:#8ea0bf;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:38px}
.kt .nt .kb{font-size:9.5px;font-weight:800;letter-spacing:.06em;padding:1px 6px;border-radius:3px;white-space:nowrap;
  background:#263247;color:#cbd5e1}
.kt .nt .kb.entry{background:rgba(65,132,243,.2);color:#64b5f6}.kt .nt .kb.t1{background:rgba(245,158,11,.2);color:#fbbf24}
.kt .nt .kb.exit{background:rgba(76,175,80,.2);color:#4caf50}.kt .nt .kb.blocked{background:rgba(239,83,80,.2);color:#ef5350}
.kt .nt .kb.signal{background:rgba(168,85,247,.2);color:#c084fc}
.kt .nt .x{flex:1;min-width:0;overflow-wrap:anywhere}
.kt .nt .e{padding:10px;color:#8ea0bf;font-size:12.5px}
.kt .strip .ms{margin-left:auto;font-size:11.5px;color:#8ea0bf;white-space:nowrap}.kt .strip .ms .on{color:#4caf50;font-weight:700}
.kt .qh .evb{font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:4px;margin-left:6px;vertical-align:middle;
  background:#1e293b;color:#cbd5e1;border:1px solid #2b3b5c;white-space:nowrap;cursor:help}
.kt .qh .evb.hi{background:rgba(245,158,11,.14);color:#fbbf24;border-color:rgba(245,158,11,.45)}
.kt .qh .fr{display:block;font-size:11px;color:#8ea0bf;margin-top:2px;text-align:right;font-variant-numeric:tabular-nums}
.kt .qh .fr i{font-style:normal;display:inline-block;width:7px;height:7px;border-radius:50%;background:#64748b;margin-right:5px}
.kt .qh .fr.ok i{background:#4caf50}.kt .qh .fr.w{color:#fbbf24}.kt .qh .fr.w i{background:#f59e0b}
.kt .lg{display:flex;flex-wrap:wrap;gap:3px 12px;align-items:baseline;font-size:12px;color:#8ea0bf;padding:2px 2px 4px;
  font-variant-numeric:tabular-nums}
.kt .lg b{font-weight:700;color:#e6edf7}.kt .lg .s{color:#e6edf7;font-weight:800}
.kt .lg b.up{color:#4caf50}.kt .lg b.dn{color:#ef5350}
.kt .lg .x{margin-left:auto;font-size:11px;color:#64748b;white-space:nowrap}
.kt .rng{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 22px;margin:10px 0 2px}
.kt .rng .r .h{display:flex;justify-content:space-between;font-size:11px;color:#8ea0bf;margin-bottom:5px}
.kt .rng .r .t{position:relative;height:4px;background:#1f2937;border-radius:3px;margin:0 5px}
.kt .rng .r .f{position:absolute;top:0;bottom:0;left:0;border-radius:3px;background:linear-gradient(90deg,#ef5350,#f59e0b,#4caf50);
  opacity:.55}
.kt .rng .r .m{position:absolute;top:-5px;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;
  border-top:7px solid #e6edf7;transform:translateX(-5px)}
.kt .rng .r .lh{display:flex;justify-content:space-between;font-size:12px;color:#e6edf7;margin-top:5px;
  font-variant-numeric:tabular-nums}.kt .rng .r .lh span{color:#8ea0bf}
.kt .rng .stats{grid-column:1/-1;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));margin-top:4px}
.kt .hi{font-size:12.5px;color:#8ea0bf;margin:0 0 6px}.kt .hi b{color:#e6edf7}
.kt .ov{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:0 0 12px}
.kt .ov .cd{background:#111a2e;border:1px solid #1e2d4a;border-radius:6px;padding:9px 12px;min-height:92px;min-width:0;
  display:flex;flex-direction:column;gap:3px}
.kt .ov .l{font-size:10.5px;color:#8ea0bf;letter-spacing:.06em;text-transform:uppercase;font-weight:600}
.kt .ov .v{font-size:20px;font-weight:800;color:#e6edf7;font-variant-numeric:tabular-nums;white-space:nowrap}
.kt .ov .v.up{color:#4caf50}.kt .ov .v.dn{color:#ef5350}
.kt .ov .s{font-size:11.5px;color:#8ea0bf;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.kt .ov .s b{color:#e6edf7}
.kt .ov .s b.up{color:#4caf50}.kt .ov .s b.dn{color:#ef5350}
.kt .ov .tg{font-size:9.5px;color:#64748b;letter-spacing:.04em;margin-top:auto}
.kt .ov .rg{font-size:9.5px;font-weight:800;padding:1px 6px;border-radius:3px;background:rgba(239,83,80,.2);color:#ef5350;
  margin-left:6px;letter-spacing:.04em;vertical-align:2px;cursor:help}
.kt .ov .mb{height:6px;background:#1f2937;border-radius:3px;overflow:hidden;display:flex;gap:1px;margin-top:4px}
.kt .ov .mb i{display:block;height:100%}.kt .ov .mb i.g{background:#4caf50}.kt .ov .mb i.r{background:#ef5350}
.kt .ov .mb i.n{background:#64748b}
.kt .sec{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:6px}
.kt .sec .tl{border-radius:6px;padding:7px 10px;min-width:0;border:1px solid rgba(255,255,255,.06)}
.kt .sec .n{font-size:11.5px;color:#e6edf7;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kt .sec .c{font-size:15px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums}
.kt .sec .ad{font-size:10.5px;color:rgba(255,255,255,.7);font-variant-numeric:tabular-nums}
.kt .sec-h{font-size:11px;color:#8ea0bf;margin:2px 0 6px}
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


def kite_strip(items, status=None):
    """Broker-style index ticker: [(name, value, abs change or None, pct change or None)]; status: optional trailing
    market-status pill [(market, is_open, tooltip or '')], e.g. '● NSE open · ○ MCX closed'."""
    cells = []
    for name, val, ch, pc in items:
        cls = "up" if (ch or 0) >= 0 else "dn"
        chg = (f'<span class="c {cls}">{ch:+,.2f} ({pc:+.2f}%)</span>' if ch is not None and pc is not None else "")
        cells.append(f'<div class="it"><span class="n">{escape(name)}</span><span class="v">{val}</span>{chg}</div>')
    if status:
        ms = " · ".join(f'<span class="{"on" if op else ""}"' + (f' title="{escape(tip, quote=True)}"' if tip else "")
                        + f'>{"●" if op else "○"} {escape(m)} {"open" if op else "closed"}</span>' for m, op, tip in status)
        cells.append(f'<div class="ms">{ms}</div>')
    return f'<div class="kt"><div class="strip">{"".join(cells)}</div></div>'


def event_short(text):
    """'Financial Results in 3 days (Mon 19 Oct): IV…' -> 'Results 3d' (watchlist / header badge text)."""
    s = str(text or "")
    lo = s.lower()
    kind = ("Results" if "result" in lo else "Expiry" if lo.startswith("expiry") else "Holiday" if "holiday" in lo
            else "Dividend" if "dividend" in lo else "Split" if "split" in lo else "Bonus" if "bonus" in lo
            else "AGM" if "general meeting" in lo or "agm" in lo.split() else "Board mtg" if "board meeting" in lo
            else "Buyback" if "buyback" in lo or "buy back" in lo else "Rights" if "rights" in lo
            else "EIA" if lo[:4] == "eia "
            else " ".join(re.split(r"\s+in \d+ days|\s+today|\s+tomorrow|:", s)[0].split()[:2])[:18])
    m = re.search(r"in (\d+) days", lo)
    when = ("today" if "today" in lo or kind == "Expiry" else "tmrw" if "tomorrow" in lo else f"{m.group(1)}d" if m else "")
    return f"{kind} {when}".strip()


def event_badges(events, n=3):
    """Header badges from analysis['events'] [(severity, text)]: high severity first and amber; full text on hover."""
    ev = sorted([e for e in (events or []) if e and len(e) == 2], key=lambda e: e[0] != "high")[:n]
    return "".join(f'<span class="evb{" hi" if sev == "high" else ""}" title="{escape(str(t), quote=True)}">'
                   f'📅 {escape(event_short(t))}</span>' for sev, t in ev)


def quote_header(sym, exch, ltp, ch, pc, buy=None, sell=None, events=None, fresh=None):
    """Symbol header with price/change and two order-pad style action buttons (display only, no orders).
    events: analysis['events'] -> badges; fresh: dict(text, cls 'ok'|'w'|'', tip) data-freshness line under the price."""
    cls = "up" if (ch or 0) >= 0 else "dn"
    chg = f'<span class="pc {cls}">{ch:+,.2f} ({pc:+.2f}%)</span>' if ch is not None and pc is not None else ""
    fr = (f'<span class="fr {escape(fresh.get("cls") or "", quote=True)}" title="{escape(fresh.get("tip") or "", quote=True)}">'
          f'<i></i>{escape(fresh.get("text") or "")}</span>') if fresh else ""

    def btn(x, kind):
        if not x:
            return f'<div class="b off">{"BUY CALL" if kind == "buy" else "BUY PUT"}<small>no signal</small></div>'
        return f'<div class="b {kind}">{escape(x[0])}<small>{escape(x[1])}</small></div>'
    return (f'<div class="kt"><div class="qh"><div><span class="s">{escape(sym)}</span><span class="ex">{escape(exch)}</span>'
            f'{event_badges(events)}</div>'
            f'<div><span class="p">{_f(ltp) if _num(ltp) is not None else "n/a"}</span>{chg}{fr}</div></div>'
            f'<div class="btns">{btn(buy, "buy")}{btn(sell, "sell")}</div></div>')


def _qty(x):
    """Indian compact quantity: 1.23 Cr / 4.56 L / 7,890 ('–' for None)."""
    x = _num(x)
    if x is None:
        return "–"
    return f"{x / 1e7:,.2f} Cr" if abs(x) >= 1e7 else f"{x / 1e5:,.2f} L" if abs(x) >= 1e5 else f"{x:,.0f}"


def _bar_row(title, lo, hi, x):
    lo, hi, x = _num(lo), _num(hi), _num(x)
    if lo is None or hi is None:
        return ""
    lo, hi = min(lo, hi), max(lo, hi)
    pos = None if x is None else 50.0 if hi == lo else min(max((x - lo) / (hi - lo) * 100, 0.0), 100.0)
    mk = (f'<div class="f" style="width:{pos:.1f}%"></div><div class="m" style="left:{pos:.1f}%" '
          f'title="LTP {_f(x)}"></div>') if pos is not None else ""
    return (f'<div class="r"><div class="h"><span>{escape(title)}</span>'
            f'<span>{"" if pos is None else f"{pos:.0f}% of range"}</span></div><div class="t">{mk}</div>'
            f'<div class="lh"><div><span>L </span>{_f(lo)}</div><div><span>H </span>{_f(hi)}</div></div></div>')


def range_bars(day_lo, day_hi, ltp, wk_lo=None, wk_hi=None, stats=None, day_title="Today's range"):
    """Kite-style quote ranges: today's and 52-week L-H bars with an LTP marker (clamped 0-100%, centred when H == L;
    a row is omitted when its L/H is missing) and a compact stats row [(label, text)] (None texts are skipped)."""
    rows = _bar_row(day_title, day_lo, day_hi, ltp) + _bar_row("52-week range", wk_lo, wk_hi, ltp)
    st_ = "".join(f'<div><div class="l">{escape(str(lb))}</div><div class="v">{escape(str(v))}</div></div>'
                  for lb, v in (stats or []) if v is not None)
    if not rows and not st_:
        return ""
    return f'<div class="kt"><div class="rng">{rows}' + (f'<div class="stats">{st_}</div>' if st_ else "") + '</div></div>'


def ohlc_legend(sym, interval, row, ltp=None, src=None, has_vol=True, prev=None):
    """TradingView-style one-line legend for the last closed bar (row): O/H/L/C coloured by the bar, change vs the
    previous bar's close (prev), volume, VWAP ('Avg' without volume: equal-weighted session mean), EMA21, RSI and
    Supertrend direction; bar time, LTP and source on the right."""
    g = (lambda k: _num(row.get(k))) if row is not None and hasattr(row, "get") else (lambda k: None)
    o, h, l, c, p = g("Open"), g("High"), g("Low"), g("Close"), _num(prev)
    nf = lambda x: "–" if x is None else _f(x)
    bc = "up" if c is not None and o is not None and c >= o else "dn" if c is not None and o is not None else ""
    cv = lambda x, ref: "" if x is None or ref is None else "up" if ref >= x else "dn"  # close above the line = green
    parts = [f'<span class="s">{escape(str(sym))} · {escape(str(interval))}</span>'] + \
            [f'{k} <b class="{bc}">{nf(v)}</b>' for k, v in (("O", o), ("H", h), ("L", l), ("C", c))]
    if c is not None and p:
        parts.append(f'<b class="{"up" if c >= p else "dn"}">{c - p:+,.2f} ({(c / p - 1) * 100:+.2f}%)</b>')
    if has_vol and g("Volume") is not None:
        parts.append(f'Vol <b>{_qty(g("Volume"))}</b>')
    vw, e21, rsi, sd = g("VWAP"), g("EMA21"), g("RSI"), g("STdir")
    parts.append(f'{"VWAP" if has_vol else "Avg"} <b class="{cv(vw, c)}">{nf(vw)}</b>')
    parts.append(f'EMA21 <b class="{cv(e21, c)}">{nf(e21)}</b>')
    if rsi is not None:
        parts.append(f'RSI <b class="{"up" if rsi >= 50 else "dn"}">{rsi:.0f}</b>')
    if sd:
        parts.append(f'ST <b class="{"up" if sd > 0 else "dn"}">{"▲" if sd > 0 else "▼"}</b>')
    t = getattr(row, "name", None)
    try:
        t = t.strftime("%d %b %H:%M")
    except Exception:
        t = None
    right = " · ".join(x for x in (f"bar {t}" if t else None, f"LTP {_f(_num(ltp))}" if _num(ltp) is not None else None,
                                     escape(str(src)) if src else None) if x)
    return f'<div class="kt"><div class="lg">{" ".join(parts)}' + (f'<span class="x">{right}</span>' if right else "") + \
        '</div></div>'


def depth_html(q, exch=None):
    """Kite-style 5-level market depth + quote stats from an Upstox full quote (OI, OI day high/low when the quote
    carries OI, e.g. MCX futures; no Volume for indices)."""
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
        from datetime import datetime, timedelta, timezone
        ltt = datetime.fromtimestamp(int(ltt) / 1000, timezone(timedelta(hours=5, minutes=30))).strftime("%H:%M:%S")
    except Exception:
        ltt = "-"
    stat = lambda l, v: f'<div><div class="l">{l}</div><div class="v">{v}</div></div>'
    prev = (q.get("last_price") or 0) - (q.get("net_change") or 0)
    idx = exch == "INDEX" or "_INDEX|" in str(q.get("instrument_token") or "")
    oi = [stat("OI", _qty(q.get("oi"))), stat("OI day high", _qty(q.get("oi_day_high"))),
          stat("OI day low", _qty(q.get("oi_day_low")))] if _num(q.get("oi")) else []
    stats = "".join([stat("Open", _f(o.get("open") or 0)), stat("High", _f(o.get("high") or 0)),
                     stat("Low", _f(o.get("low") or 0)), stat("Prev. close", _f(prev))]
                    + ([] if idx else [stat("Volume", f'{int(q.get("volume") or 0):,}')])
                    + [stat("Avg. price", _f(q.get("average_price") or 0)),
                       stat("Lower circuit", _f(q.get("lower_circuit_limit") or 0)),
                       stat("Upper circuit", _f(q.get("upper_circuit_limit") or 0)), stat("Last trade", ltt)] + oi)
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
                + _ladder(x["u_sl"], x["u_entry"], x["u_t1"], x["u_t2"], r["ltp"])
                + (f'<div class="size">⚖ Risk:reward 1:{x["rr"][0]:.1f} to T1 · 1:{x["rr"][1]:.1f} to T2 · '
                   f'⛔ {escape(x["invalid"])}</div>' if x.get("rr") and x.get("invalid") else "")
                + (f'<div class="size">' + (f'🎯 Limit buy ≤ ₹{x["limit"]:,.2f} · ' if x.get("limit") else "")
                   + f'spread {x["spread_pct"]:.1f}%' + ("" if x.get("spread_ok", True) else " ⚠ wide: skip or wait")
                   + '</div>' if x.get("spread_pct") is not None else "")
                + (f'<div class="size">📦 {escape(x["size_note"])}</div>' if x.get("size_note") else ""))

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
            + cred_block(r.get("cred"))
            + (f'<div class="warn">⚠ {" · ".join(escape(n) for n in notes[:3])}</div>' if notes else "")
            + '</div></div>')


def cred_block(c):
    """Credibility grade and the evidence checks behind it (engine.credibility)."""
    if not c:
        return ""
    col = {"A": UP, "B": ACC, "C": WARN, "D": DN}[c["grade"]]
    icon = {"pass": ("✓", UP), "warn": ("!", WARN), "fail": ("✗", DN), "n/a": ("–", "#64748b")}
    items = "".join(f'<li><span style="color:{icon[x["state"]][1]};font-weight:800">{icon[x["state"]][0]}</span> '
                    f'<b>{escape(x["name"])}</b>: {escape(x["detail"])}</li>' for x in c["checks"])
    return (f'<div class="cred"><div class="h"><span class="g" style="color:{col};border-color:{col}">{c["grade"]}</span>'
            f'<span><b style="color:{col}">{escape(c["label"])}</b> · credibility {c["score"]}/100 · '
            f'evidence-based T1 odds {c["p_t1"] * 100:.0f}%</span></div><ul>{items}</ul></div>')


def credible_board(ok, near, bar="B"):
    """High-credibility CALL / PUT plans (engine.credible_ideas) as a dense table; when none clear the bar, the
    closest candidates are shown greyed with the checks that keep them out."""
    gcol = {"A": UP, "B": ACC, "C": WARN, "D": DN}

    def row(r, faded):
        c, ce = r["cred"], r["kind"] == "CE"
        when = "now" if r["mode"] == "now" else f'{"above" if ce else "below"} {_f(r["u_entry"])}'
        passes = [x["name"] for x in c["checks"] if x["state"] == "pass"]
        weak = [x["name"] + ("" if x["state"] == "fail" else " (weak)") for x in c["checks"] if x["state"] in ("fail", "warn")]
        pc = lambda v: f' <small class="{"up" if v >= r["prem"] else "dn"}">{(v / r["prem"] - 1) * 100:+.0f}%</small>' \
            if r.get("prem") else ""
        note = (("✗ " + ", ".join(weak[:3])) if faded else ("✓ " + ", ".join(passes[:3])
                                                            + (f' · ! {", ".join(weak[:2])}' if weak else "")))
        return (f'<tr class="{"near" if faded else ""}"><td class="l"><span class="gb" style="color:{gcol[c["grade"]]};'
                f'border-color:{gcol[c["grade"]]}">{c["grade"]}</span></td>'
                f'<td class="l"><b>{escape(r["symbol"])}</b><br><small class="mut">{_f(r["ltp"])}</small></td>'
                f'<td class="l"><b class="{"up" if ce else "dn"}">{"BUY CALL ▲" if ce else "BUY PUT ▼"}</b><br>'
                f'<small class="mut">{escape(when)}</small></td>'
                f'<td class="l">{r["strike"]:g} {r["kind"]}<br><small class="mut">{escape(str(r.get("expiry") or ""))}'
                f'{"" if r.get("live") else " · model"}</small></td>'
                f'<td>{_f(r["prem"])}</td><td>{_f(r["sl"])}{pc(r["sl"])}</td><td>{_f(r["t1"])}{pc(r["t1"])}</td>'
                f'<td>{_f(r["t2"])}{pc(r["t2"])}</td><td><b>{c["p_t1"] * 100:.0f}%</b></td><td>{c["score"]}</td>'
                f'<td class="l w">{escape(note)}</td></tr>')

    head = ('<tr><th class="l">Grade</th><th class="l">Symbol</th><th class="l">Call / Put</th><th class="l">Contract</th>'
            '<th>Entry ₹</th><th>Stop ₹</th><th>T1 ₹</th><th>T2 ₹</th><th>T1 odds</th><th>Score</th>'
            '<th class="l">Evidence</th></tr>')
    if ok:
        body = "".join(row(r, False) for r in ok)
        msg = ""
    else:
        body = "".join(row(r, True) for r in near)
        msg = (f'<div class="empty" style="margin-bottom:8px">No CALL / PUT clears credibility grade {escape(bar)} right now. '
               + ('Closest candidates below, with what keeps them out.' if near else 'No active plans to grade.') + '</div>')
    table = f'<div class="cb"><table>{head}{body}</table></div>' if body else ""
    return f'<div class="tk">{msg}{table}</div>'


# ---------------- Option chain (Home) ----------------
def _num(x):
    """float, or None for None / NaN / non-numbers."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def chain_window(chain, spot, width=None, cap=60):
    """-> (strikes shown, ATM strike): ATM ± width (every strike when None), at most `cap` per side around ATM."""
    c = chain or {}
    strikes = sorted(c.get("quotes") or {})
    s = _num(spot) or _num(c.get("und"))
    if not strikes:
        return [], None
    i = min(range(len(strikes)), key=lambda j: abs(strikes[j] - s)) if s is not None else len(strikes) // 2
    w = min(int(width) if width else len(strikes), cap)
    return strikes[max(0, i - w): i + w + 1], strikes[i]


def _oi_lakh(chain):
    qs = (chain or {}).get("quotes") or {}
    return max((_num(q.get("oi")) or 0 for v in qs.values() for q in v.values() if q), default=0) >= 1e5


def _side(qs, K, s):
    return (qs.get(K) or {}).get(s) or {}


def chain_chips(chain, spot, vix=None, dte=None, o=None):
    """Option-chain summary chips: spot, expiry/DTE, PCR, PCR ΔOI, max pain, OI walls, ATM IV, VIX, ATM straddle,
    expected range and gamma regime (o = pro.options_analytics) and the total CE-vs-PE OI split bar."""
    c = chain or {}
    n = lambda x, fmt: "n/a" if _num(x) is None else format(_num(x), fmt)
    chip = lambda lbl, v, cls="", tip="": (f'<span class="chip {cls}"' + (f' title="{escape(tip)}"' if tip else "")
                                           + f'>{escape(lbl)} <b>{v}</b></span>')
    pcr, atm_iv, d = _num(c.get("pcr")), _num(c.get("atm_iv")), _num(dte)
    out = [chip("Spot", n(spot, ",.2f")),
           chip("Expiry", escape(str(c.get("expiry") or "n/a")) + (f" · {d:.1f}d" if d is not None else "")),
           chip("PCR", n(pcr, ".2f"), "up" if pcr and pcr > 1.2 else "dn" if pcr and pcr < 0.8 else "",
                "Put/call OI ratio: >1.2 put writing (supportive), <0.8 call writing (capping)"),
           chip("PCR ΔOI", n(c.get("pcr_chg"), ".2f"), tip="Put/call ratio of today's OI change"),
           chip("Max pain", n(c.get("max_pain"), "g")),
           chip("Resistance", n(c.get("call_wall"), "g"), "dn", "Highest call OI (call wall)"),
           chip("Support", n(c.get("put_wall"), "g"), "up", "Highest put OI (put wall)"),
           chip("ATM IV", f"{atm_iv * 100:.1f}%" if atm_iv else "n/a"),
           chip("India VIX", n(vix, ".2f"))]
    if o:
        sd, lo, hi = _num(o.get("straddle")), _num(o.get("em_lo")), _num(o.get("em_hi"))
        if sd:
            out.append(chip("ATM straddle", f"±{sd:,.1f}", tip="ATM call + put premium: the market's expected move to expiry"))
        if lo is not None and hi is not None:
            out.append(chip("Expected range", f"{lo:,.0f}–{hi:,.0f}"))
        if o.get("regime"):
            fl = _num(o.get("flip"))
            out.append(chip("Gamma", escape(str(o["regime"])) + (f" · flip {fl:,.0f}" if fl else ""),
                            "w" if o["regime"] == "Negative" else "",
                            "Positive = moves get damped (range/pin); negative = moves get amplified"))
    qs = c.get("quotes") or {}
    ce = sum(_num(_side(qs, K, "CE").get("oi")) or 0 for K in qs)
    pe = sum(_num(_side(qs, K, "PE").get("oi")) or 0 for K in qs)
    if ce + pe > 0:
        sh = ce / (ce + pe) * 100
        out.append(f'<span class="chip split">CE OI <b>{ce / 1e5:,.1f}L</b> · PE OI <b>{pe / 1e5:,.1f}L</b>'
                   f'<div class="bar"><div style="width:{sh:.0f}%;background:#ef5350"></div>'
                   f'<div style="width:{100 - sh:.0f}%;background:#4caf50"></div></div></span>')
    return f'<div class="kt"><div class="chips">{"".join(out)}</div></div>'


def option_chain_html(chain, spot, width=10, view="Calls & Puts", greeks=False, reads=None, mdelta=None):
    """Upstox-style chain: Calls | Strike | Puts with ITM shading, OI bars, ATM outline, spot row and R/S wall chips.
    reads {strike: OI read} -> strike tooltip; mdelta {(strike, side): model delta} fills Δ where the feed has none."""
    c = chain or {}
    qs = c.get("quotes") or {}
    ks, atm = chain_window(c, spot, width)
    if not ks:
        return '<div class="kt"><div class="mut">No strikes in this chain.</div></div>'
    spot = _num(spot) or _num(c.get("und")) or atm
    reads, mdelta = reads or {}, mdelta or {}
    sides = [s for s, on in (("CE", view != "Puts"), ("PE", view != "Calls")) if on] or ["CE", "PE"]
    has = lambda f: any(_side(qs, K, s).get(f) is not None for K in ks for s in ("CE", "PE"))
    extras = has("vol") or has("cp")
    bu_on = c.get("source") == "Upstox" and has("cp")
    g = []
    if greeks:
        g = ["delta"] + (["theta"] if has("theta") else []) + (["pop"] if has("pop") else [])
    lak = _oi_lakh(c)
    mx = max([_num(_side(qs, K, s).get("oi")) or 0 for K in ks for s in sides] + [0]) or 1
    lk = lambda x: f"{x / 1e5:,.1f}L" if x >= 1e5 else f"{x:,.0f}"
    names = dict(delta="Δ" if has("delta") else "model Δ", theta="θ/day", pop="POP", vol="Vol",
                 oi="OI (lakh)" if lak else "OI", doi="ΔOI", iv="IV", ltp="LTP")
    ce_cols = g + (["vol"] if extras else []) + ["oi", "doi", "iv", "ltp"]
    order = {"CE": ce_cols, "PE": ce_cols[::-1]}

    def cells(K, s):
        o = _side(qs, K, s)
        itm = K < spot if s == "CE" else K > spot
        cl = ' class="itm"' if itm else ""
        out = {}
        for f in g:
            v = _num(o.get(f))
            if f == "delta" and v is None:
                v = _num(mdelta.get((K, s)))
            out[f] = f"<td{cl}>{'–' if v is None else f'{v:.0f}%' if f == 'pop' else f'{v:.2f}'}</td>"
        vol = _num(o.get("vol"))
        out["vol"] = f"<td{cl}>{lk(vol) if vol is not None else '–'}</td>"
        oi = _num(o.get("oi"))
        bar = f'<div class="bar-{s.lower()}" style="width:{oi / mx * 100:.0f}%"></div>' if oi else ""
        out["oi"] = (f'<td class="oi{" itm" if itm else ""}">{bar}<span>'
                     f'{"–" if oi is None else f"{oi / 1e5:,.1f}" if lak else f"{oi:,.0f}"}</span></td>')
        d = _num(o.get("doi"))
        tag = ""
        lt, cp = _num(o.get("ltp")), _num(o.get("cp"))
        if bu_on and d and lt and cp and lt != cp:
            tag = {(1, 1): "LB", (-1, 1): "SB", (1, -1): "SC", (-1, -1): "LU"}[(1 if lt > cp else -1, 1 if d > 0 else -1)]
        tips = dict(LB="Long build-up: price up, OI up", SB="Short build-up: price down, OI up",
                    SC="Short covering: price up, OI down", LU="Long unwinding: price down, OI down")
        dcls = "up" if (d or 0) > 0 else "dn" if (d or 0) < 0 else "mut"
        out["doi"] = (f'<td{cl}><span class="{dcls}">'
                      f'{"–" if d is None else f"{d / 1e5:+,.2f}" if lak else f"{d:+,.0f}"}</span>'
                      + (f'<span class="bu {tag.lower()}" title="{tips[tag]}">{tag}</span>' if tag else "") + "</td>")
        iv = _num(o.get("iv"))
        out["iv"] = f"<td{cl}>{f'{iv:.1f}' if iv else '–'}</td>"
        b, a = _num(o.get("bid")), _num(o.get("ask"))
        pc = (f' · <span class="{"up" if lt >= cp else "dn"}">{(lt / cp - 1) * 100:+.1f}%</span>'
              if extras and lt and cp else "")
        out["ltp"] = (f'<td{cl}><b>{_f(lt) if lt else "–"}</b><span class="s2">{_f(b) if b else "–"} / '
                      f'{_f(a) if a else "–"}{pc}</span></td>')
        return "".join(out[f] for f in order[s])

    ncol = sum(len(order[s]) for s in sides) + 1
    top = ((f'<th colspan="{len(order["CE"])}">CALLS</th>' if "CE" in sides else "") + '<th></th>'
           + (f'<th colspan="{len(order["PE"])}">PUTS</th>' if "PE" in sides else ""))
    hdr = ("".join(f"<th>{names[f]}</th>" for f in order["CE"]) if "CE" in sides else "") + \
        '<th style="text-align:center">Strike</th>' + \
        ("".join(f"<th>{names[f]}</th>" for f in order["PE"]) if "PE" in sides else "")
    rows = []
    cw, pw = _num(c.get("call_wall")), _num(c.get("put_wall"))
    for j, K in enumerate(ks):
        if j and ks[j - 1] < spot <= K:
            rows.append(f'<tr class="spot"><td colspan="{ncol}">── Spot {_f(spot)} ──</td></tr>')
        rd = reads.get(K) or ""
        tip = escape(f"{K:g}: {rd}" if rd and rd != "-" else f"{K:g}: no clear OI build-up", quote=True)
        wc = ('<span class="wc r" title="Call wall (max CE OI): resistance">R</span>' if K == cw else "") + \
             ('<span class="wc s" title="Put wall (max PE OI): support">S</span>' if K == pw else "")
        rows.append(f'<tr{" class=atm" if K == atm else ""}>' + (cells(K, "CE") if "CE" in sides else "")
                    + f'<td class="k" title="{tip}">{K:g}{wc}</td>' + (cells(K, "PE") if "PE" in sides else "") + "</tr>")
    return (f'<div class="kt"><div class="ocw"><table class="oc"><tr class="side">{top}</tr><tr>{hdr}</tr>'
            f'{"".join(rows)}</table></div></div>')


def chain_caption(chain, n):
    """One-line source / unit / legend caption for option_chain_html."""
    c = chain or {}
    src = c.get("source") or "chain"
    unit = ("lakh " if _oi_lakh(c) else "") + ("contracts" if src == "NSE" else "qty")
    return (f"{src} · expiry {c.get('expiry') or 'n/a'} · {n} of {len(c.get('quotes') or {})} strikes · ITM shaded · "
            f"bars = share of max OI · OI in {unit} · "
            + ("ΔOI = change since first seen this session (Kite has no previous-day OI)" if src == "Kite"
               else "ΔOI vs previous day")
            + (" · LB/SB/SC/LU = long build-up / short build-up / short covering / long unwinding" if src == "Upstox"
               else "") + " · hover a strike for the OI read")


# ---------------- Positions / notifications (Home) ----------------
def pnl_summary(overall, realised, unreal, n_open, n_closed):
    """Upstox-style positions P&L strip (paper, shared journal): overall, realised today, unrealised MTM, counts."""
    def cell(lbl, x, big=""):
        x = _num(x) or 0.0
        cls = "up" if x > 0 else "dn" if x < 0 else "mut"
        return (f'<div class="c{big}"><div class="l">{lbl}</div><div class="v {cls}">'
                f'{"-" if x < 0 else ""}₹{abs(x):,.0f}</div></div>')
    no, nc = int(_num(n_open) or 0), int(_num(n_closed) or 0)
    return (f'<div class="kt"><div class="pnl">{cell("Overall P&amp;L", overall, " big")}'
            f'{cell("Realised (closed today)", realised)}{cell("Unrealised (MTM)", unreal)}'
            f'<div class="c"><div class="l">Open / Closed today</div><div class="v">{no} / {nc}</div></div>'
            f'<div class="tg">paper · shared journal · no broker positions</div></div></div>')


def notif_list(rows, seen=None, today=None):
    """Dense alert list [{id, ts, symbol, kind, text}] newest first: HH:MM (date when not today), kind badge, text;
    ids above `seen` are marked unread."""
    if not rows:
        return '<div class="kt"><div class="nt"><div class="e">No alerts yet.</div></div></div>'
    out = []
    for r in rows:
        ts = str(r.get("ts") or "")
        hm = ts[11:16] if len(ts) >= 16 else (ts or "–")
        if today and len(ts) >= 10 and ts[:10] != today:
            hm = f"{ts[8:10]}/{ts[5:7]} {hm}"
        k = str(r.get("kind") or "").upper()
        new = seen is not None and (_num(r.get("id")) or 0) > (_num(seen) or 0)
        out.append(f'<div class="it{" new" if new else ""}"><span class="t" title="{escape(ts, quote=True)}">'
                   f'{escape(hm)}</span><span class="kb {escape(k.lower(), quote=True)}">{escape(k or "–")}</span>'
                   f'<span class="x">{escape(str(r.get("text") or "")).replace(chr(10), " · ")}</span></div>')
    return f'<div class="kt"><div class="nt">{"".join(out)}</div></div>'


def strike_drill(s, name):
    """One-strike drill-down from engine.option_price_signal: live vs fair value, delta, premium stop, targets."""
    if not s:
        return ""
    stat = lambda lbl, v: f'<div><div class="l">{lbl}</div><div class="v">{v}</div></div>'
    nf = lambda x, fmt="{:,.2f}": "n/a" if _num(x) is None else fmt.format(_num(x))
    price, rich, piv, dte = _num(s.get("price")), _num(s.get("rich")), _num(s.get("piv")), _num(s.get("dte"))
    act = str(s.get("action") or "-")
    cls = {"BUY": "up", "HOLD": "up", "AVOID": "dn", "EXIT": "dn", "WAIT": "w"}.get(act, "")
    rc = "dn" if (rich or 0) > 0.15 else "up" if (rich or 0) < -0.10 else ""
    stats = [stat("Price", nf(price) + (" <small class='mut'>live</small>" if s.get("live") else
                                        " <small class='mut'>model</small>")),
             stat("Fair value", nf(s.get("fair"))),
             stat("Rich / cheap", f'<span class="{rc}">{rich * 100:+.0f}%</span>' if rich is not None else "n/a"),
             stat("Delta", nf(s.get("delta"), "{:+.2f}")),
             stat("IV at price", f"{piv * 100:.1f}%" if piv else "n/a"),
             stat("Premium stop", nf(s.get("p_sl")) + f" <small class='mut'>und {nf(s.get('stop_lvl'))}</small>"),
             stat("Days to expiry", f"{dte:.1f}" if dte is not None else "n/a")]
    tg = s.get("tgts") or []
    trs = "".join(f'<tr><td style="text-align:left">T{i + 1}</td><td>{nf(t.get("level"))}</td><td>{nf(t.get("prem"))}</td>'
                  f'<td>{(t["prem"] / price - 1) * 100:+.0f}%</td><td>{(t.get("prob") or 0) * 100:.0f}%</td></tr>'
                  if price and _num(t.get("prem")) is not None else
                  f'<tr><td style="text-align:left">T{i + 1}</td><td>{nf(t.get("level"))}</td><td>n/a</td><td>–</td>'
                  f'<td>{(t.get("prob") or 0) * 100:.0f}%</td></tr>' for i, t in enumerate(tg[:4]))
    tbl = (f'<div class="ocw" style="margin-top:8px"><table class="depth"><tr><th style="text-align:left">Target</th>'
           f'<th>Underlying</th><th>Premium ≈</th><th>Gain</th><th>Touch prob</th></tr>{trs}</table></div>') if trs else ""
    why = [f'<li><span class="up">●</span> {escape(w)}</li>' for w in (s.get("why_for") or [])[:3]] + \
          [f'<li><span class="dn">●</span> {escape(w)}</li>' for w in (s.get("why_against") or [])[:3]]
    return (f'<div class="kt"><div class="dd"><div class="h"><span>{escape(str(name))} {nf(s.get("strike"), "{:g}")} '
            f'{escape(str(s.get("kind") or ""))}</span><span class="chip {cls}"><b>{escape(act)}</b></span></div>'
            f'<div class="stats">{"".join(stats)}</div>{tbl}' + (f'<ul>{"".join(why)}</ul>' if why else "") + '</div></div>')


def _rs(x, sign=False):
    x = _num(x)
    if x is None:
        return "n/a"
    return f'{"-" if x < 0 else "+" if sign and x > 0 else ""}₹{abs(x):,.0f}'


def _seg(parts):
    """6px stacked bar: [(weight, cls 'g'|'r'|'n', title)] -> <div class=mb> (grey when every weight is 0)."""
    parts = [(w, c, t) for w, c, t in parts if (_num(w) or 0) > 0]
    tot = sum(_num(w) for w, _, _ in parts)
    if not tot:
        return '<div class="mb"></div>'
    return ('<div class="mb">' + "".join(f'<i class="{c}" style="width:{_num(w) / tot * 100:.2f}%" title="{escape(t, quote=True)}">'
                                         f'</i>' for w, c, t in parts) + '</div>')


def overview_cards(name, funds=None, pos=None, sig=None, breadth=None):
    """Kite-dashboard summary row (auto-fit grid, wraps on phones): paper funds, paper positions, watchlist signals and
    market breadth. funds: dict(avail, used, capital, realised, risk_ok, reasons); pos: dict(n, pnl, items [(label,
    unrealised)]); sig: dict(call, put, wait, none); breadth: dict(adv, dec, unch, src) or None. None-safe throughout."""
    f, p, g, b = funds or {}, pos or {}, sig or {}, breadth or {}
    cls = lambda x: "up" if (_num(x) or 0) > 0 else "dn" if (_num(x) or 0) < 0 else ""
    rg = ("" if f.get("risk_ok", True) else
          f'<span class="rg" title="{escape(" · ".join(f.get("reasons") or []) or "daily limits hit", quote=True)}">RISK GUARD</span>')
    c1 = (f'<div class="cd"><div class="l">Margin available{rg}</div><div class="v">{_rs(f.get("avail"))}</div>'
          f'<div class="s">Margin used <b>{_rs(f.get("used"))}</b> · Opening balance <b>{_rs(f.get("capital"))}</b></div>'
          f'<div class="tg">Paper funds (premium paid, not SPAN margin) · shared journal</div></div>')
    items = p.get("items") or []
    bar = _seg([(abs(_num(u) or 0), "g" if (_num(u) or 0) >= 0 else "r", f"{lbl} {_rs(u, True)}") for lbl, u in items])
    n = int(_num(p.get("n")) or 0)
    c2 = (f'<div class="cd"><div class="l">Positions · paper</div><div class="v {cls(p.get("pnl"))}">{_rs(p.get("pnl"), True)}'
          f'</div><div class="s"><b>{n}</b> open · P&amp;L today (realised + unrealised)</div>'
          + (bar if items else "") + '<div class="tg">shared journal · no broker positions</div></div>')
    k = lambda x: int(_num(g.get(x)) or 0)
    c3 = (f'<div class="cd"><div class="l">Signals · watchlist</div><div class="v"><span class="up">▲ {k("call")}</span> '
          f'<span class="dn">▼ {k("put")}</span></div><div class="s"><b class="up">▲ {k("call")} CALL</b> · '
          f'<b class="dn">▼ {k("put")} PUT</b> · <b>⏳ {k("wait")}</b> waiting</div>'
          f'<div class="s">{k("none")} no trade</div></div>')
    if b and any(_num(b.get(x)) is not None for x in ("adv", "dec", "unch")):
        ad, dc, un = (int(_num(b.get(x)) or 0) for x in ("adv", "dec", "unch"))
        c4 = (f'<div class="cd"><div class="l">Market breadth</div><div class="v"><span class="up">{ad}</span> '
              f'<span class="mut">/</span> <span class="dn">{dc}</span></div>'
              + _seg([(ad, "g", f"Advances {ad}"), (dc, "r", f"Declines {dc}"), (un, "n", f"Unchanged {un}")])
              + f'<div class="s">▲ {ad} adv · ▼ {dc} dec · {un} unch</div>'
                f'<div class="tg">{escape(str(b.get("src") or ""))}</div></div>')
    else:
        c4 = '<div class="cd"><div class="l">Market breadth</div><div class="v mut">n/a</div><div class="tg">n/a</div></div>'
    hi = f'<div class="hi">Hi, <b>{escape(str(name))}</b></div>' if name else ""
    return f'<div class="kt">{hi}<div class="ov">{c1}{c2}{c3}{c4}</div></div>'


def _mix(a, b, t):
    """Hex colour a -> b at t (0-1)."""
    a, b = ([int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in (a, b))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def heat(pc):
    """Diverging tile colour for a % change: #b91c1c at ≤-2% → #1f2937 at 0 → #15803d at ≥+2%."""
    pc = _num(pc)
    if pc is None:
        return "#1f2937"
    t = max(-1.0, min(1.0, pc / 2))
    return _mix("#1f2937", "#15803d" if t > 0 else "#b91c1c", abs(t))


def sector_tiles(rows, title=None):
    """Sector heat tiles from [dict(name, chg (%), adv, dec)], in the given order; title: small source line above."""
    out = []
    for r in rows or []:
        c, ad, dc = _num(r.get("chg")), _num(r.get("adv")), _num(r.get("dec"))
        nm = escape(str(r.get("name") or "?"))
        out.append(f'<div class="tl" style="background:{heat(c)}" title="{nm}"><div class="n">{nm}</div>'
                   f'<div class="c">{"n/a" if c is None else f"{c:+.2f}%"}</div>'
                   + (f'<div class="ad">▲ {ad:.0f} · ▼ {dc:.0f}</div>' if ad is not None and dc is not None else "")
                   + '</div>')
    if not out:
        return '<div class="kt"><div class="sec-h">Sector data unavailable</div></div>'
    return (f'<div class="kt">' + (f'<div class="sec-h">{escape(str(title))}</div>' if title else "")
            + f'<div class="sec">{"".join(out)}</div></div>')


# ---------------- Simple (mobile) view ----------------
MINI_CSS = """<style>
.tk .mini{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--acc);border-radius:12px;
  padding:10px 12px;margin:0 0 8px}
.tk .mini.ce{border-left-color:var(--up)} .tk .mini.pe{border-left-color:var(--dn)}
.tk .mini .t{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:800;color:var(--txt)}
.tk .mini .t small{font-weight:600;color:var(--mut)}
.tk .mini .g{display:inline-grid;place-items:center;min-width:24px;height:24px;border-radius:7px;border:1px solid;
  font-size:12px;font-weight:800}
.tk .mini .r{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}
.tk .mini .r div{background:rgba(255,255,255,.04);border-radius:8px;padding:5px 6px;text-align:center}
.tk .mini .r b{display:block;font-size:14px;color:var(--txt);font-variant-numeric:tabular-nums}
.tk .mini .r span{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
.tk .mini .n{font-size:11.5px;color:var(--mut);margin-top:6px}
</style>"""


def mini_call(sym, r):
    """One compact CALL/PUT card for the phone: action, contract, entry/SL/T1/T2 premiums, grade and trigger."""
    ce = r.get("kind") == "CE"
    c = r.get("cred") or {}
    g = c.get("grade", "–")
    gc = {"A": UP, "B": ACC, "C": WARN, "D": DN}.get(g, "#64748b")
    when = "enter now" if r.get("mode") == "now" else f"only {'above' if ce else 'below'} {_f(r.get('u_entry'))}"
    cell = lambda lbl, v: f'<div><span>{lbl}</span><b>{_f(v)}</b></div>'
    rr = r.get("rr")
    return (f'<div class="mini {"ce" if ce else "pe"}"><div class="t"><span>{"▲ BUY CALL" if ce else "▼ BUY PUT"} · '
            f'{escape(sym)} {r.get("strike", 0):g} {escape(str(r.get("kind", "")))}</span>'
            f'<span class="g" style="color:{gc};border-color:{gc}">{g}</span></div>'
            f'<div class="r">{cell("Entry", r.get("prem"))}{cell("Stop", r.get("sl"))}{cell("T1", r.get("t1"))}'
            f'{cell("T2", r.get("t2"))}</div>'
            f'<div class="n">{escape(when)} · T1 odds {c.get("p_t1", 0) * 100:.0f}%'
            + (f' · R:R 1:{rr[1]:.1f}' if rr else "") + (" · model premium" if not r.get("live") else "") + '</div>'
            + (f'<div class="n">' + (f'🎯 Limit buy ≤ ₹{r["limit"]:,.2f} · ' if r.get("limit") else "")
               + f'spread {r["spread_pct"]:.1f}%' + ("" if r.get("spread_ok", True) else " ⚠ wide: skip or wait")
               + '</div>' if r.get("spread_pct") is not None else "")
            + (f'<div class="n">📦 {escape(r["size_note"])} · ₹{r.get("cost_lot", 0):,.0f} premium per lot</div>'
               if r.get("size_note") else "") + '</div>')


# ---------------- Phone "Calls" screen (app-style cards) ----------------
APP_CSS = """<style>
.tk .hero{border-radius:22px;padding:20px 18px 34px;margin:0 0 -22px;color:#fff;
  background:linear-gradient(135deg,#3a1460 0%,#542087 38%,#7132b0 70%,#9b6bd6 100%)}
.tk .hero h1{margin:0;font-size:26px;font-weight:800;color:#fff;line-height:1.15}
.tk .hero p{margin:4px 0 12px;opacity:.85;font-size:13px}
.tk .hero .px{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px}
.tk .hero .px b{font-variant-numeric:tabular-nums}
.tk .hero .px .u{color:#7ff0c4} .tk .hero .px .d{color:#ffb3ab}
.tk .sheet{background:var(--bg,#140b1d);border-radius:22px 22px 0 0;padding:16px 2px 2px;position:relative}
.tk .sheet h2{font-size:19px;font-weight:800;margin:0 4px 12px;color:var(--txt)}
.tk .cc{border-radius:16px;padding:1.5px;margin:0 0 14px;
  background:linear-gradient(135deg,rgba(155,107,214,.9),rgba(49,192,144,.6))}
.tk .cc.pe{background:linear-gradient(135deg,rgba(155,107,214,.9),rgba(231,95,83,.65))}
.tk .cc.x{background:rgba(255,255,255,.10)}
.tk .cc .in{background:#1b1027;border-radius:15px;overflow:hidden}
.tk .cc .strip{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 12px;font-size:12px;
  background:rgba(255,255,255,.06);color:#d9cfe6}
.tk .cc .top{display:flex;align-items:center;gap:10px;padding:12px 12px 4px}
.tk .cc .av{width:40px;height:40px;border-radius:11px;display:grid;place-items:center;font-weight:800;font-size:13px;
  color:#fff;flex:none}
.tk .cc .nm{font-weight:800;font-size:16px;color:var(--txt);line-height:1.2}
.tk .cc .tg{font-size:11px;font-weight:700;letter-spacing:.05em}
.tk .cc .st{margin-left:auto;font-size:12px;font-weight:800;padding:4px 11px;border-radius:999px;white-space:nowrap}
.tk .cc .legs{padding:2px 12px 4px}
.tk .cc .lg{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:6px 0;font-size:13.5px;
  color:var(--txt)}
.tk .cc .lg .k{font-size:10.5px;color:var(--mut);text-align:right}
.tk .cc .lg b{font-variant-numeric:tabular-nums;font-size:15px}
.tk .cc .row{display:flex;justify-content:space-between;gap:10px;padding:10px 12px;
  border-top:1px solid rgba(255,255,255,.08);font-size:13.5px;color:#d9cfe6}
.tk .cc .row b{color:var(--txt);font-variant-numeric:tabular-nums;text-align:right}
.tk .cc .ft{padding:8px 12px 11px;font-size:11.5px;color:var(--mut);border-top:1px solid rgba(255,255,255,.08)}
.tk .empty2{padding:26px 10px;text-align:center;color:var(--mut);font-size:13.5px}
</style>"""

_AV = ["#542087", "#148564", "#c63224", "#7132b0", "#0f6b50", "#b8860b", "#2f5aa8", "#8a5cc7"]
_G = "#31c090"
_R = "#e75f53"


def _avatar(sym):
    col = _AV[sum(map(ord, str(sym))) % len(_AV)]
    return f'<div class="av" style="background:{col}">{escape(str(sym)[:2])}</div>'


def _badge(text, col):
    return f'<span class="st" style="background:{col}22;color:{col}">{escape(text)}</span>'


def _contract(r):
    """'28 OCT 2450 CE' from the chain expiry '28-Oct-2025'."""
    e = str(r.get("expiry") or "")
    tag = " ".join(e.split("-")[:2]).upper() if e.count("-") == 2 else ""
    return f'{tag + " " if tag else ""}{(r.get("strike") or 0):g} {r.get("kind") or ""}'


def hero(title, subtitle, prices):
    """Gradient header. prices: [(name, value text, change % or None)]."""
    px = "".join(f'<span>{escape(n)} <b>{escape(v)}</b>'
                 + (f' <span class="{"u" if c >= 0 else "d"}">{"▲" if c >= 0 else "▼"}{abs(c):.2f}%</span>'
                    if c is not None else "") + '</span>' for n, v, c in prices)
    return (f'<div class="tk"><div class="hero"><h1>{escape(title)}</h1><p>{escape(subtitle)}</p>'
            f'<div class="px">{px}</div></div></div>')


def sheet_title(text):
    return f'<div class="tk"><div class="sheet"><h2>{escape(text)}</h2></div></div>'


def call_card(sym, r, view="Options", duration="Intraday"):
    """App-style card for one live CALL/PUT: status, legs, expected profit and stop loss in rupees for the suggested lots."""
    ce = r.get("kind") == "CE"
    c = r.get("cred") or {}
    g = c.get("grade", "–")
    gc = {"A": UP, "B": ACC, "C": WARN, "D": DN}.get(g, "#64748b")
    live = r.get("mode") == "now"
    strip = "Enter now" if live else f"Enter only if {sym} goes {'above' if ce else 'below'} {_f(r.get('u_entry'))}"
    status = _badge("Active", _G) if live else _badge("Waiting", "#ffb400")
    lot = int(r.get("lot_size") or 1)
    lots = int(r.get("lots") or 0)
    qty = lot * max(lots, 1)
    if view == "Futures":
        legs = (f'<div class="lg"><span><span style="color:{_G if ce else _R}">●</span> {"BUY" if ce else "SELL"} '
                f'{escape(sym)} FUT</span><span><div class="k">Entry</div><b>{_f(r.get("u_entry"))}</b></span></div>')
        pts = lambda x: abs((x or 0) - (r.get("u_entry") or 0))
        rows = (f'<div class="row"><span>Target 1 / Target 2</span><b style="color:{_G}">{_f(r.get("u_t1"))} / '
                f'{_f(r.get("u_t2"))}</b></div><div class="row"><span>Stop loss</span><b style="color:{_R}">'
                f'{_f(r.get("u_sl"))} <small>({pts(r.get("u_sl")):,.1f} pts)</small></b></div>')
    else:
        legs = (f'<div class="lg"><span><span style="color:{_G}">●</span> BUY {escape(_contract(r))}</span>'
                f'<span><div class="k">Entry</div><b>₹{_f(r.get("prem") or 0)}</b></span></div>')
        prem = r.get("prem") or 0
        rows = (f'<div class="row"><span>Expected profit (T2)</span><b style="color:{_G}">'
                f'₹{((r.get("t2") or 0) - prem) * qty:,.0f}</b></div>'
                f'<div class="row"><span>Stop loss</span><b style="color:{_R}">₹{(prem - (r.get("sl") or 0)) * qty:,.0f}</b></div>'
                f'<div class="row"><span>Targets (premium)</span><b>₹{_f(r.get("t1") or 0)} / ₹{_f(r.get("t2") or 0)}</b></div>')
    rows += (f'<div class="row"><span>Duration</span><b>{escape(duration)}</b></div>'
             f'<div class="row"><span>Grade · T1 odds</span><b><span style="color:{gc}">{g}</span> · '
             f'{c.get("p_t1", 0) * 100:.0f}%</b></div>')
    ft = []
    if view != "Futures":
        ft.append(f'{lots} lot{"s" if lots != 1 else ""} × {lot}' if lots else f'1 lot × {lot} shown (size says skip)')
        if r.get("limit"):
            ft.append(f'limit ≤ ₹{r["limit"]:,.2f}')
        if r.get("spread_pct") is not None:
            ft.append(f'bid-ask {r["spread_pct"]:.1f}%' + ("" if r.get("spread_ok", True) else " ⚠ wide"))
        if not r.get("live"):
            ft.append("model premium")
    if r.get("invalid"):
        ft.append(r["invalid"])
    return (f'<div class="cc {"ce" if ce else "pe"}"><div class="in"><div class="strip"><span>{escape(strip)}</span>'
            f'<span>{"⚡" if live else "⏳"}</span></div><div class="top">{_avatar(sym)}<div>'
            f'<div class="nm">{escape(sym)}</div><div class="tg" style="color:{_G if ce else _R}">'
            f'{"↗ BULLISH" if ce else "↘ BEARISH"}</div></div>{status}</div><div class="legs">{legs}</div>{rows}'
            + (f'<div class="ft">{escape(" · ".join(ft))}</div>' if ft else "") + '</div></div>')


def exited_card(row):
    """Card for a resolved recommendation from the track record (recs table row as a dict)."""
    ce = row.get("kind") == "CE"
    st_ = str(row.get("status") or "")
    rm = row.get("r_mult")
    rm = None if rm is None or rm != rm else float(rm)
    win = rm is not None and rm > 0
    if st_ in ("EXPIRED", "NOT TRIGGERED"):
        badge, col = _badge("Not triggered", "#94a3b8"), "#94a3b8"
    else:
        badge, col = (_badge("Profit", _G), _G) if win else (_badge("Loss" if rm else "Flat", _R if rm else "#94a3b8"),
                                                            _R if rm else "#94a3b8")
    res = escape(str(row.get("outcome") or st_ or "–")) + (f' ({rm:+.2f}R)' if rm is not None else "")
    when = escape(str(row.get("day") or ""))
    src = str(row.get("mode") or "")
    src = f' · {escape(src.split(":", 1)[1])}' if src.startswith("broker:") else ""
    return (f'<div class="cc x"><div class="in"><div class="top">{_avatar(row.get("symbol", ""))}<div>'
            f'<div class="nm">{escape(str(row.get("symbol", "")))}</div><div class="tg" style="color:{_G if ce else _R}">'
            f'{"↗ BULLISH" if ce else "↘ BEARISH"} · {when}{src}</div></div>{badge}</div>'
            f'<div class="legs"><div class="lg"><span>BUY {(row.get("strike") or 0):g} {escape(str(row.get("kind") or ""))}'
            f'</span><span><div class="k">Entry</div><b>₹{_f(row.get("prem") or 0)}</b></span></div></div>'
            f'<div class="row"><span>Result</span><b style="color:{col}">{res}</b></div></div></div>')
