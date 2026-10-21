"""Halaman untuk melihat & mengendalikan komputer lain dan menelusuri berkasnya.
Disajikan server node di http://localhost:7171/ (hanya dari komputer ini)."""

PAGE = r"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>It's Me — Komputer</title>
<style>
:root { color-scheme: dark; --bg:#0f1115; --panel:#1a1d24; --line:#2a2f3a; --fg:#e6e9ef;
        --mut:#9aa3b2; --acc:#4f8cff; --warn:#ffb020; --bad:#ff5b5b; }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }
header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:10px 14px;
         background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }
header b { font-size:15px; } .mut { color:var(--mut); }
.pcs { display:flex; gap:6px; flex-wrap:wrap; }
.pc { padding:5px 10px; border:1px solid var(--line); border-radius:8px; background:#232833; cursor:pointer; }
.pc.sel { border-color:var(--acc); background:#223; }
.pc .dot { color:var(--warn); } .pc.lead .dot { color:#36d07a; }
.tabs { display:flex; gap:6px; padding:8px 14px; }
.tab { padding:6px 12px; border-radius:8px; cursor:pointer; border:1px solid transparent; color:var(--mut); }
.tab.sel { background:var(--panel); border-color:var(--line); color:var(--fg); }
main { padding:0 14px 20px; }
.row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:8px 0; }
button, select, input { font:inherit; color:var(--fg); background:#232833; border:1px solid var(--line);
         border-radius:8px; padding:6px 10px; }
button { cursor:pointer; } button:hover { border-color:var(--acc); }
#screenWrap { position:relative; max-width:100%; background:#000; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
#screen { display:block; max-width:100%; height:auto; cursor:crosshair; }
#screenWrap.off #screen { cursor:default; }
.files { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.fitem { display:flex; gap:10px; align-items:center; padding:7px 12px; border-top:1px solid var(--line); }
.fitem:first-child { border-top:none; } .fitem:hover { background:#20242e; }
.fitem .nm { flex:1; cursor:pointer; word-break:break-all; } .fitem .sz { color:var(--mut); font-size:12px; white-space:nowrap; }
.ico { width:1.3em; text-align:center; }
.badge { font-size:11px; color:var(--warn); border:1px solid var(--warn); border-radius:6px; padding:0 5px; }
.small { font-size:12px; } a { color:var(--acc); }
#prev { width:100%; height:50vh; white-space:pre; overflow:auto; background:#0b0d11; border:1px solid var(--line);
        border-radius:10px; padding:10px; font-family:ui-monospace,Consolas,monospace; font-size:12px; }
#path { flex:1; min-width:200px; } .hide { display:none; }
.note { color:var(--mut); font-size:12px; }
</style></head>
<body>
<header>
  <b>It's Me — Komputer</b>
  <div class="pcs" id="pcs"></div>
  <button id="refresh" title="Segarkan daftar komputer">⟳</button>
</header>
<div class="tabs">
  <div class="tab sel" data-t="screen">🖥️ Layar</div>
  <div class="tab" data-t="files">📁 Berkas</div>
</div>
<main>
  <section id="tab-screen">
    <div class="row">
      <label><input type="checkbox" id="ctrl"> Kendalikan (mouse &amp; keyboard)</label>
      <label>Lebar <select id="qw">
        <option value="800">800</option><option value="1280" selected>1280</option>
        <option value="1600">1600</option><option value="1920">1920</option></select></label>
      <label>Jeda <select id="fps">
        <option value="250">cepat</option><option value="500" selected>sedang</option>
        <option value="1000">hemat</option></select></label>
      <button id="snap">Ambil sekali</button>
      <span class="note" id="sinfo"></span>
    </div>
    <div id="screenWrap" class="off" tabindex="0"><img id="screen" alt="layar"></div>
    <p class="note">Klik kotak layar lalu centang “Kendalikan” untuk memakai komputer itu seperti remote
    desktop. Ini gambar yang diperbarui berkala, jadi sedikit ada jeda. Hanya layar utama.</p>
  </section>
  <section id="tab-files" class="hide">
    <div class="row">
      <button id="up">⬆ Naik</button>
      <input id="path" placeholder="C:\ atau /home/..." >
      <button id="go">Buka</button>
    </div>
    <div class="files" id="list"></div>
    <div class="row hide" id="prevRow">
      <strong id="prevName"></strong><span class="note" id="prevInfo"></span>
      <button id="prevClose">Tutup</button>
    </div>
    <div id="prev" class="hide"></div>
  </section>
</main>
<script>
const $ = s => document.querySelector(s);
let nodes = [], sel = null, tab = "screen";
let timer = null, loading = false, lastMove = 0;

async function jget(u){ const r = await fetch(u); if(!r.ok) throw new Error((await r.json().catch(()=>({}))).error||r.status); return r.json(); }
function human(n){ if(!n) return ""; const u=["B","KB","MB","GB"]; let i=0; while(n>=1024&&i<3){n/=1024;i++;} return n.toFixed(i?1:0)+u[i]; }

async function loadNodes(){
  try { const s = await jget("/status");
    nodes = s.nodes||[];
    if(!sel || !nodes.find(n=>n.id===sel)) sel = (nodes.find(n=>n.id===s.me)||nodes[0]||{}).id;
    const me = s.me;
    $("#pcs").innerHTML = "";
    nodes.forEach(n=>{ const d=document.createElement("div");
      d.className="pc"+(n.id===sel?" sel":"")+(n.leader?" lead":"");
      d.innerHTML=`<span class="dot">●</span> ${n.name}${n.id===me?' <span class="mut small">(ini)</span>':''}${n.leader?' <span class="mut small">pemimpin</span>':''}`;
      d.onclick=()=>{ sel=n.id; loadNodes(); if(tab==="screen") snap(); else browse($("#path").value); };
      $("#pcs").appendChild(d); });
  } catch(e){ $("#pcs").innerHTML = `<span class="mut">tidak bisa baca daftar: ${e.message}</span>`; }
}

/* ---- layar ---- */
function nextShot(){ if(tab!=="screen") return; clearTimeout(timer); timer=setTimeout(snap, +$("#fps").value); }
function snap(){
  if(tab!=="screen"||!sel||loading) return;
  loading=true; const t0=performance.now();
  const img=$("#screen"), u=`/screen?node=${encodeURIComponent(sel)}&w=${$("#qw").value}&t=${Date.now()}`;
  const probe=new Image();
  probe.onload=()=>{ img.src=probe.src; loading=false;
    $("#sinfo").textContent=`${probe.naturalWidth}×${probe.naturalHeight} · ${Math.round(performance.now()-t0)} ms`;
    nextShot(); };
  probe.onerror=()=>{ loading=false; $("#sinfo").textContent="layar tidak bisa diambil (komputer mati / bukan Windows / tidak ada sesi layar)"; };
  probe.src=u;
}
function sendInput(events){
  if(!sel) return;
  fetch("/input",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({node:sel,events})}).catch(()=>{});
}
function relXY(ev){ const r=$("#screen").getBoundingClientRect();
  return {x:Math.min(1,Math.max(0,(ev.clientX-r.left)/r.width)), y:Math.min(1,Math.max(0,(ev.clientY-r.top)/r.height))}; }
const controlling=()=>$("#ctrl").checked;
$("#ctrl").onchange=()=>{ $("#screenWrap").classList.toggle("off",!controlling()); if(controlling()) $("#screenWrap").focus(); };
const img=$("#screen");
img.addEventListener("mousemove",ev=>{ if(!controlling())return; const now=Date.now(); if(now-lastMove<60)return; lastMove=now;
  const p=relXY(ev); sendInput([{t:"move",...p}]); });
img.addEventListener("mousedown",ev=>{ if(!controlling())return; ev.preventDefault();
  const b=["left","middle","right"][ev.button]||"left"; sendInput([{t:"down",button:b,...relXY(ev)}]); });
img.addEventListener("mouseup",ev=>{ if(!controlling())return; ev.preventDefault();
  const b=["left","middle","right"][ev.button]||"left"; sendInput([{t:"up",button:b,...relXY(ev)}]); });
img.addEventListener("dblclick",ev=>{ if(!controlling())return; ev.preventDefault(); sendInput([{t:"dblclick",...relXY(ev)}]); });
img.addEventListener("contextmenu",ev=>{ if(controlling()) ev.preventDefault(); });
$("#screenWrap").addEventListener("wheel",ev=>{ if(!controlling())return; ev.preventDefault();
  sendInput([{t:"scroll",dy:ev.deltaY<0?120:-120}]); },{passive:false});
$("#screenWrap").addEventListener("keydown",ev=>{ if(!controlling())return; ev.preventDefault();
  const k=ev.key;
  if(k.length===1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey){ sendInput([{t:"text",text:k}]); return; }
  const map={Enter:"enter",Backspace:"backspace",Tab:"tab",Escape:"escape",Delete:"delete"," ":"space",
    ArrowUp:"up",ArrowDown:"down",ArrowLeft:"left",ArrowRight:"right",Home:"home",End:"end",
    PageUp:"pageup",PageDown:"pagedown"};
  let key=map[k]|| (k.length===1?k:k.toLowerCase());
  if(/^f\d{1,2}$/i.test(k)) key=k.toLowerCase();
  sendInput([{t:"key",key,ctrl:ev.ctrlKey,alt:ev.altKey,shift:ev.shiftKey,win:ev.metaKey}]); });
$("#snap").onclick=snap; $("#qw").onchange=snap; $("#fps").onchange=()=>{};

/* ---- berkas ---- */
async function browse(path){
  try { const d=await jget(`/fs/list?node=${encodeURIComponent(sel)}&path=${encodeURIComponent(path||"")}`);
    $("#path").value=d.path||""; $("#up").dataset.parent=d.parent==null?"":d.parent;
    const L=$("#list"); L.innerHTML="";
    if(!d.entries.length) L.innerHTML='<div class="fitem mut">(kosong)</div>';
    d.entries.forEach(e=>{ const row=document.createElement("div"); row.className="fitem";
      const ico=e.dir?"📁":"📄";
      row.innerHTML=`<span class="ico">${ico}</span><span class="nm">${e.name}</span>`+
        (e.secret?'<span class="badge">rahasia</span>':'')+
        `<span class="sz">${e.dir?'':human(e.size)}</span>`;
      row.querySelector(".nm").onclick=()=>{ if(e.dir) browse(e.path); else if(!e.secret) preview(e); };
      if(!e.dir && !e.secret){ const b=document.createElement("button"); b.textContent="⬇"; b.title="Unduh"; b.className="small";
        b.onclick=()=>window.open(`/fs/download?node=${encodeURIComponent(sel)}&path=${encodeURIComponent(e.path)}`,"_blank");
        row.appendChild(b); }
      L.appendChild(row); });
  } catch(e){ $("#list").innerHTML=`<div class="fitem" style="color:var(--bad)">${e.message}</div>`; }
}
async function preview(e){
  try { const d=await jget(`/fs/read?node=${encodeURIComponent(sel)}&path=${encodeURIComponent(e.path)}`);
    $("#prevRow").classList.remove("hide"); $("#prev").classList.remove("hide");
    $("#prevName").textContent=e.name; $("#prev").textContent=d.text||"";
    $("#prevInfo").textContent=human(d.size)+(d.truncated?" · dipotong":"")+(d.binary?" · biner":""); }
  catch(err){ $("#prevRow").classList.remove("hide"); $("#prev").classList.remove("hide");
    $("#prevName").textContent=e.name; $("#prev").textContent="("+err.message+")"; $("#prevInfo").textContent=""; }
}
$("#up").onclick=()=>browse($("#up").dataset.parent||"");
$("#go").onclick=()=>browse($("#path").value);
$("#path").addEventListener("keydown",e=>{ if(e.key==="Enter") browse($("#path").value); });
$("#prevClose").onclick=()=>{ $("#prevRow").classList.add("hide"); $("#prev").classList.add("hide"); };

/* ---- tab ---- */
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("sel")); t.classList.add("sel");
  tab=t.dataset.t;
  $("#tab-screen").classList.toggle("hide",tab!=="screen");
  $("#tab-files").classList.toggle("hide",tab!=="files");
  if(tab==="screen") snap(); else { clearTimeout(timer); if(!$("#list").children.length) browse(""); }
});
$("#refresh").onclick=loadNodes;
loadNodes().then(()=>snap());
</script>
</body></html>"""
