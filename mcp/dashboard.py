#!/usr/bin/env python3
"""MCP Dashboard — MCP-centric view with dynamic tool discovery, health, datasources and diff"""
import os, asyncio, json, time
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import httpx, uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv(Path(__file__).parent / ".env")

TENANT_ID = "b3f0b16b-81f9-4c36-a9ba-2b7fc139f0cb"
REGISTRY  = json.loads((Path(__file__).parent / "mcp_registry.json").read_text())

_tokens: dict = {}

async def get_token(client_id: str, secret: str) -> str:
    if client_id in _tokens and datetime.now() < _tokens[client_id]["exp"]:
        return _tokens[client_id]["tok"]
    async with httpx.AsyncClient() as c:
        r = await c.post(f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={"grant_type":"client_credentials","client_id":client_id,
                  "client_secret":secret,"scope":f"api://{client_id}/.default"})
        d = r.json()
    _tokens[client_id] = {"tok": d["access_token"], "exp": datetime.now() + timedelta(seconds=d["expires_in"]-60)}
    return _tokens[client_id]["tok"]

async def mcp_call(url: str, client_id: str, secret: str, method: str, params: dict = {}) -> dict:
    token = await get_token(client_id, secret)
    body  = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params})
    headers = {"Authorization":f"Bearer {token}","Content-Type":"application/json","Accept":"application/json, text/event-stream"}
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{url}/", content=body, headers=headers)
        ms = int((time.time()-start)*1000)
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if "result" in data:
                    return {"ok":True,"data":data["result"],"ms":ms}
                return {"ok":False,"error":data.get("error",{}).get("message","unknown"),"ms":ms}
        return {"ok":False,"error":"no data","ms":ms}
    except Exception as e:
        return {"ok":False,"error":str(e)[:100],"ms":int((time.time()-start)*1000)}

def env_cfg(mcp: dict, env: str) -> dict | None:
    envs = mcp.get("environments",{})
    if env not in envs: return None
    cfg = envs[env]
    return {**cfg, "secret": os.environ.get(cfg["secret_env"], "")}

IGNORE_KEYS = {"generationtime_ms","timestamp","request_id","elapsed","duration_ms","server_time","utc_offset_seconds"}

def deep_diff(a, b, path=""):
    diffs = []
    if type(a) != type(b):
        diffs.append(f"{path}: type {type(a).__name__} vs {type(b).__name__}")
    elif isinstance(a, dict):
        for k in set(list(a)+list(b)):
            if k in IGNORE_KEYS: continue
            if k not in a: diffs.append(f"{path}.{k}: mangler i A")
            elif k not in b: diffs.append(f"{path}.{k}: mangler i B")
            else: diffs.extend(deep_diff(a[k], b[k], f"{path}.{k}"))
    elif isinstance(a, list):
        for i,(x,y) in enumerate(zip(a,b)): diffs.extend(deep_diff(x,y,f"{path}[{i}]"))
        if len(a)!=len(b): diffs.append(f"{path}: len {len(a)} vs {len(b)}")
    elif a != b:
        diffs.append(f"{path}: {str(a)[:40]!r} vs {str(b)[:40]!r}")
    return diffs

def compare(results: dict) -> dict:
    parsed = {}
    for env,r in results.items():
        if r and r.get("ok"):
            try: parsed[env] = json.loads(r["data"]["content"][0]["text"])
            except: parsed[env] = r["data"]
        else: parsed[env] = None
    if any(v is None for v in parsed.values()):
        return {"status":"error","message":"Et eller flere miljøer fejlede","diffs":{}}
    envs = list(parsed)
    diffs = {}
    for i,a in enumerate(envs):
        for b in envs[i+1:]:
            d = deep_diff(parsed[a], parsed[b])
            if d: diffs[f"{a}_vs_{b}"] = d[:15]
    if not diffs: return {"status":"identical","message":"Alle miljoer returnerer identiske data","diffs":{}}
    return {"status":"differs","message":f"Forskelle fundet","diffs":diffs}

app = FastAPI()

@app.get("/api/registry")
async def registry(): return JSONResponse(REGISTRY)

@app.get("/api/health/{mcp_id}")
async def health(mcp_id: str):
    mcp = next((m for m in REGISTRY["mcps"] if m["id"]==mcp_id), None)
    if not mcp: return JSONResponse({"error":"not found"},404)
    results = {}
    async def ping(env):
        cfg = env_cfg(mcp, env)
        if not cfg: results[env] = None; return
        r = await mcp_call(cfg["url"], cfg["client_id"], cfg["secret"], "initialize",
            {"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"dashboard","version":"1.0"}})
        results[env] = {"ok":r["ok"],"ms":r["ms"]}
    await asyncio.gather(*[ping(e) for e in ["local","dev","prod"]])
    return JSONResponse(results)

@app.get("/api/tools/{mcp_id}")
async def tools(mcp_id: str):
    mcp = next((m for m in REGISTRY["mcps"] if m["id"]==mcp_id), None)
    if not mcp: return JSONResponse({"error":"not found"},404)
    env = next((e for e in ["local","dev","prod"] if e in mcp.get("environments",{})), None)
    if not env: return JSONResponse([])
    cfg = env_cfg(mcp, env)
    r = await mcp_call(cfg["url"], cfg["client_id"], cfg["secret"], "tools/list")
    if r["ok"]: return JSONResponse(r["data"].get("tools",[]))
    return JSONResponse([])

@app.get("/api/run/{mcp_id}")
async def run(mcp_id: str, tool: str, args: str = "{}"):
    mcp = next((m for m in REGISTRY["mcps"] if m["id"]==mcp_id), None)
    if not mcp: return JSONResponse({"error":"not found"},404)
    arg_dict = json.loads(args)
    results = {}
    async def call(env):
        cfg = env_cfg(mcp, env)
        if not cfg: results[env] = None; return
        r = await mcp_call(cfg["url"], cfg["client_id"], cfg["secret"],
                           "tools/call", {"name":tool,"arguments":arg_dict})
        if r["ok"]:
            try: text = r["data"]["content"][0]["text"]
            except: text = str(r["data"])
            results[env] = {"ok":True,"text":text,"ms":r["ms"]}
        else:
            results[env] = {"ok":False,"error":r["error"],"ms":r["ms"]}
    envs = [e for e in ["local","dev","prod"] if e in mcp.get("environments",{})]
    await asyncio.gather(*[call(e) for e in envs])
    results["compare"] = compare(results)
    return JSONResponse(results)

@app.get("/", response_class=HTMLResponse)
async def dashboard(): return HTMLResponse(HTML)

HTML = r"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<title>MCP Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;display:flex;flex-direction:column;height:100vh}
header{padding:14px 24px;border-bottom:1px solid #2a2a3a;display:flex;align-items:center;gap:12px;flex-shrink:0}
header h1{font-size:1.1rem;font-weight:700}header span{font-size:0.8rem;color:#666}
.main{display:flex;flex:1;overflow:hidden}
/* Sidebar */
.sidebar{width:240px;border-right:1px solid #2a2a3a;overflow-y:auto;flex-shrink:0;padding:12px 0}
.sidebar h3{font-size:0.7rem;color:#666;letter-spacing:1px;text-transform:uppercase;padding:8px 16px 4px}
.mcp-item{padding:10px 16px;cursor:pointer;border-left:3px solid transparent;transition:.15s}
.mcp-item:hover{background:#1a1a2e}
.mcp-item.active{background:#1a1a2e;border-left-color:#4f8ef7}
.mcp-item .mcp-name{font-size:0.9rem;font-weight:600;margin-bottom:4px}
.mcp-item .mcp-desc{font-size:0.75rem;color:#888;line-height:1.4}
.envdots{display:flex;gap:4px;margin-top:6px}
.envdot{width:8px;height:8px;border-radius:50%;background:#333;title:attr(data-env)}
.envdot.ok{background:#4fc87a;box-shadow:0 0 6px #4fc87a88}
.envdot.err{background:#f74f4f}
.envdot.loading{background:#f7a94f;animation:pulse 1s infinite}
.envdot.na{background:#333}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
/* Content */
.content{flex:1;overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:20px}
.section{background:#1a1a2e;border-radius:12px;border:1px solid #2a2a3a;overflow:hidden}
.section-header{padding:12px 16px;border-bottom:1px solid #2a2a3a;display:flex;align-items:center;gap:10px}
.section-header h2{font-size:0.9rem;font-weight:700;letter-spacing:.5px}
.section-body{padding:16px}
/* Health */
.health-grid{display:flex;gap:12px}
.health-card{flex:1;background:#0f1117;border-radius:8px;padding:12px;border:1px solid #2a2a3a;text-align:center}
.health-card .env-label{font-size:0.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;font-weight:700}
.health-card .status{font-size:1.2rem;margin-bottom:4px}
.health-card .ms{font-size:0.75rem;color:#888}
/* Datasources */
.ds-list{display:flex;flex-wrap:wrap;gap:8px}
.ds-chip{background:#0f1117;border:1px solid #3a3a4a;border-radius:20px;padding:4px 12px;font-size:0.8rem;display:flex;align-items:center;gap:6px}
.ds-chip a{color:#4f8ef7;text-decoration:none;font-size:0.75rem}
/* Tools */
.tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
.tool-card{background:#0f1117;border:1px solid #2a2a3a;border-radius:8px;padding:10px;cursor:pointer;transition:.15s}
.tool-card:hover{border-color:#4f8ef7;background:#0d1525}
.tool-card.selected{border-color:#4f8ef7;background:#0d1525}
.tool-card .tool-name{font-size:0.85rem;font-weight:600;color:#4f8ef7;margin-bottom:4px;font-family:monospace}
.tool-card .tool-desc{font-size:0.75rem;color:#888;line-height:1.4}
/* Runner */
.runner-row{display:flex;gap:10px;margin-bottom:16px;align-items:center}
.runner-row input{flex:1;background:#0f1117;border:1px solid #3a3a4a;border-radius:8px;color:#e0e0e0;padding:8px 12px;font-size:0.9rem}
.runner-row button{background:#4f8ef7;border:none;border-radius:8px;color:white;padding:8px 20px;font-size:0.9rem;cursor:pointer;font-weight:600;white-space:nowrap}
.runner-row button:hover{background:#3a7ae0}
.results-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.res-card{background:#0f1117;border-radius:8px;border:1px solid #2a2a3a;overflow:hidden}
.res-card-header{padding:8px 12px;font-size:0.75rem;font-weight:700;letter-spacing:1px;display:flex;justify-content:space-between;align-items:center}
.res-card-body{padding:10px 12px;font-size:0.78rem;font-family:monospace;white-space:pre-wrap;word-break:break-word;max-height:220px;overflow-y:auto;line-height:1.5;color:#c8d3f5}
.res-card-body.err{color:#f74f4f}
.ms-badge{font-size:0.7rem;color:#888;font-weight:400}
/* Compare */
.compare-bar{border-radius:8px;padding:12px 16px;border:1px solid #2a2a3a;margin-top:12px}
.compare-bar.identical{border-color:#4fc87a44;background:#0d2a1a}
.compare-bar.differs{border-color:#f7a94f44;background:#2a1a0a}
.compare-bar.error{border-color:#f74f4f44;background:#2a0a0a}
.compare-title{font-size:0.9rem;font-weight:700;margin-bottom:8px}
.diff-group{margin-top:8px}
.diff-group h4{font-size:0.72rem;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:1px}
.diff-item{font-size:0.78rem;font-family:monospace;color:#f7a94f;background:#1e1000;border-radius:4px;padding:2px 8px;margin:2px 0}
.placeholder{color:#555;font-size:0.9rem;text-align:center;padding:40px}
</style>
</head>
<body>
<header>
  <div style="font-size:1.5rem">🔌</div>
  <div><h1>MCP Dashboard</h1><span>Model Context Protocol — miljø-overblik og sammenligning</span></div>
</header>
<div class="main">
  <div class="sidebar">
    <h3>MCP Servere</h3>
    <div id="mcpList"></div>
  </div>
  <div class="content" id="content">
    <div class="placeholder">Vælg en MCP server i venstre panel</div>
  </div>
</div>

<script>
let registry = null;
let selectedMcp = null;
let selectedTool = null;

async function init() {
  const r = await fetch("/api/registry");
  registry = await r.json();
  renderSidebar();
}

function renderSidebar() {
  const list = document.getElementById("mcpList");
  list.innerHTML = "";
  registry.mcps.forEach(mcp => {
    const envs = Object.keys(mcp.environments || {});
    const div = document.createElement("div");
    div.className = "mcp-item";
    div.onclick = () => selectMcp(mcp.id);
    div.id = "sidebar-"+mcp.id;
    const dots = envs.map(e => `<div class="envdot loading" id="dot-${mcp.id}-${e}" title="${e}"></div>`).join("");
    div.innerHTML = `
      <div class="mcp-name">${mcp.name}</div>
      <div class="mcp-desc">${mcp.description}</div>
      <div class="envdots">${dots}</div>`;
    list.appendChild(div);
    checkHealth(mcp);
  });
}

async function checkHealth(mcp) {
  const r = await fetch(`/api/health/${mcp.id}`);
  const data = await r.json();
  Object.entries(data).forEach(([env, status]) => {
    const dot = document.getElementById(`dot-${mcp.id}-${env}`);
    if (!dot) return;
    if (status === null) { dot.className="envdot na"; return; }
    dot.className = "envdot " + (status.ok ? "ok" : "err");
    dot.title = `${env}: ${status.ok ? status.ms+"ms" : "fejl"}`;
  });
}

async function selectMcp(id) {
  selectedMcp = registry.mcps.find(m => m.id === id);
  selectedTool = null;
  document.querySelectorAll(".mcp-item").forEach(el => el.classList.remove("active"));
  document.getElementById("sidebar-"+id).classList.add("active");
  renderContent();
  const toolsData = await fetch(`/api/tools/${id}`).then(r=>r.json());
  renderTools(toolsData);
}

function renderContent() {
  const mcp = selectedMcp;
  const envs = Object.keys(mcp.environments || {});
  const envColors = {local:"#4f8ef7",dev:"#f7a94f",prod:"#4fc87a"};

  const dsHtml = mcp.datasources.length
    ? mcp.datasources.map(ds=>`<div class="ds-chip">📡 ${ds.name} <a href="${ds.url}" target="_blank">↗</a></div>`).join("")
    : '<span style="color:#666;font-size:0.85rem">Ingen datakilder konfigureret endnu</span>';

  const healthCards = envs.map(e => `
    <div class="health-card">
      <div class="env-label" style="color:${envColors[e]||"#aaa"}">${e.toUpperCase()}</div>
      <div class="status" id="hstatus-${mcp.id}-${e}">⏳</div>
      <div class="ms" id="hms-${mcp.id}-${e}"></div>
    </div>`).join("");

  document.getElementById("content").innerHTML = `
    <div class="section">
      <div class="section-header"><h2>🏥 Health</h2><span style="font-size:0.8rem;color:#888">${mcp.name} — auth: ${mcp.auth_type}</span></div>
      <div class="section-body"><div class="health-grid">${healthCards}</div></div>
    </div>
    <div class="section">
      <div class="section-header"><h2>📡 Datakilder</h2></div>
      <div class="section-body"><div class="ds-list">${dsHtml}</div></div>
    </div>
    <div class="section">
      <div class="section-header"><h2>🔧 Tools</h2><span style="font-size:0.8rem;color:#888">hentet live fra MCP serveren</span></div>
      <div class="section-body"><div class="tools-grid" id="toolsGrid"><span style="color:#888">Henter tools...</span></div></div>
    </div>
    <div class="section" id="runnerSection" style="display:none">
      <div class="section-header"><h2>▶ Kør tool</h2><span style="font-size:0.8rem;color:#888" id="runnerToolName"></span></div>
      <div class="section-body">
        <div class="runner-row">
          <input id="runnerArg" type="text" placeholder="Argument..." onkeydown="if(event.key==='Enter')runTool()"/>
          <button onclick="runTool()">▶ Kør på alle miljøer</button>
        </div>
        <div class="results-grid" id="runnerResults"></div>
        <div id="compareArea"></div>
      </div>
    </div>`;

  refreshHealth(mcp);
}

async function refreshHealth(mcp) {
  const envColors = {local:"#4f8ef7",dev:"#f7a94f",prod:"#4fc87a"};
  const r = await fetch(`/api/health/${mcp.id}`);
  const data = await r.json();
  Object.entries(data).forEach(([env, status]) => {
    const s = document.getElementById(`hstatus-${mcp.id}-${env}`);
    const m = document.getElementById(`hms-${mcp.id}-${env}`);
    if (!s) return;
    if (!status) { s.textContent="—"; return; }
    const col = envColors[env]||"#aaa";
    s.innerHTML = status.ok ? `<span style="color:${col}">✅</span>` : `<span style="color:#f74f4f">❌</span>`;
    m.textContent = status.ok ? status.ms+"ms" : "fejl";
    const dot = document.getElementById(`dot-${mcp.id}-${env}`);
    if (dot) { dot.className="envdot "+(status.ok?"ok":"err"); dot.title=`${env}: ${status.ok?status.ms+"ms":"fejl"}`; }
  });
}

function renderTools(tools) {
  const grid = document.getElementById("toolsGrid");
  if (!tools.length) { grid.innerHTML='<span style="color:#888">Ingen tools fundet</span>'; return; }
  grid.innerHTML = tools.map(t => `
    <div class="tool-card" id="tc-${t.name}" onclick="selectTool('${t.name}', this)">
      <div class="tool-name">${t.name}</div>
      <div class="tool-desc">${t.description||""}</div>
    </div>`).join("");
}

function selectTool(name, el) {
  selectedTool = name;
  document.querySelectorAll(".tool-card").forEach(c=>c.classList.remove("selected"));
  el.classList.add("selected");
  const section = document.getElementById("runnerSection");
  section.style.display = "block";
  document.getElementById("runnerToolName").textContent = name;
  document.getElementById("runnerResults").innerHTML = "";
  document.getElementById("compareArea").innerHTML = "";
  section.scrollIntoView({behavior:"smooth"});
}

async function runTool() {
  if (!selectedTool || !selectedMcp) return;
  const arg = document.getElementById("runnerArg").value.trim();
  const envColors = {local:"#4f8ef7",dev:"#f7a94f",prod:"#4fc87a"};
  const envs = Object.keys(selectedMcp.environments||{});

  const grid = document.getElementById("runnerResults");
  grid.innerHTML = envs.map(e=>`
    <div class="res-card">
      <div class="res-card-header" style="background:${envColors[e]||"#333"}22">
        <span style="color:${envColors[e]||"#aaa"}">${e.toUpperCase()}</span>
        <span class="ms-badge" id="rms-${e}"></span>
      </div>
      <div class="res-card-body" id="rres-${e}">⏳ Henter...</div>
    </div>`).join("");
  grid.style.gridTemplateColumns = `repeat(${envs.length},1fr)`;

  const schema = await fetch(`/api/tools/${selectedMcp.id}`).then(r=>r.json());
  const toolDef = schema.find(t=>t.name===selectedTool);
  let argKey = arg ? Object.keys(toolDef?.inputSchema?.properties||{})[0] : null;
  const argObj = argKey && arg ? JSON.stringify({[argKey]:arg}) : "{}";

  const r = await fetch(`/api/run/${selectedMcp.id}?tool=${encodeURIComponent(selectedTool)}&args=${encodeURIComponent(argObj)}`);
  const data = await r.json();

  envs.forEach(env => {
    const res = data[env];
    const el = document.getElementById(`rres-${env}`);
    const ms = document.getElementById(`rms-${env}`);
    if (!res) { el.textContent="—"; return; }
    ms.textContent = res.ms+"ms";
    if (res.ok) {
      try { el.textContent = JSON.stringify(JSON.parse(res.text),null,2).substring(0,1500); }
      catch { el.textContent = res.text.substring(0,1500); }
      el.className="res-card-body";
    } else {
      el.textContent = "❌ "+res.error;
      el.className="res-card-body err";
    }
  });

  const cmp = data.compare;
  const ca = document.getElementById("compareArea");
  ca.innerHTML = `<div class="compare-bar ${cmp.status}">
    <div class="compare-title">${cmp.status==="identical"?"✅ Alle miljoer returnerer identiske data":cmp.status==="differs"?"⚠️ Forskelle fundet":"❌ "+cmp.message}</div>
    ${Object.entries(cmp.diffs||{}).map(([pair,items])=>`
      <div class="diff-group">
        <h4>${pair.replace("_vs_"," vs ").toUpperCase()}</h4>
        ${items.map(d=>`<div class="diff-item">${d}</div>`).join("")}
      </div>`).join("")}
  </div>`;
}

init();
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("MCP Dashboard -> http://localhost:9090")
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")
