#!/usr/bin/env python3
"""
Visual MCP Environment Dashboard
Runs at http://localhost:9090 - shows Local, Dev, Prod side by side
"""
import os, asyncio, json, time
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv(Path(__file__).parent / ".env")

TENANT_ID     = "b3f0b16b-81f9-4c36-a9ba-2b7fc139f0cb"
DEV_CLIENT_ID = "90926159-18cc-4b41-80f6-9cf01a61af38"
PRD_CLIENT_ID = "6c38778d-37bd-45f4-910b-fe9600c3c250"
DEV_SECRET    = os.environ["RCMCPATEA_CLIENT_SECRET"]
PRD_SECRET    = os.environ.get("RCMCPATEA_PROD_CLIENT_SECRET", DEV_SECRET)

ENVS = {
    "local": {"url": "http://localhost:4547", "client_id": DEV_CLIENT_ID, "secret": DEV_SECRET, "color": "#4f8ef7"},
    "dev":   {"url": "https://rcmcpatea.wonderfulsmoke-7219c7b7.westeurope.azurecontainerapps.io", "client_id": DEV_CLIENT_ID, "secret": DEV_SECRET, "color": "#f7a94f"},
    "prod":  {"url": "https://rcmcpatea.kindhill-77d03965.westeurope.azurecontainerapps.io", "client_id": PRD_CLIENT_ID, "secret": PRD_SECRET, "color": "#4fc87a"},
}

_tokens: dict = {}

async def get_token(client_id: str, secret: str) -> str:
    key = client_id
    if key in _tokens and datetime.now() < _tokens[key]["expires"]:
        return _tokens[key]["token"]
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": secret, "scope": f"api://{client_id}/.default"}
        )
        d = r.json()
    _tokens[key] = {"token": d["access_token"], "expires": datetime.now() + timedelta(seconds=d["expires_in"] - 60)}
    return _tokens[key]["token"]

async def call_tool(env_name: str, tool: str, args: dict) -> dict:
    env = ENVS[env_name]
    token = await get_token(env["client_id"], env["secret"])
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{env['url']}/", content=body, headers=headers)
        ms = int((time.time() - start) * 1000)
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if "result" in data:
                    return {"ok": True, "text": data["result"]["content"][0]["text"], "ms": ms}
                return {"ok": False, "error": data.get("error", {}).get("message", "unknown"), "ms": ms}
        return {"ok": False, "error": "no data", "ms": ms}
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        return {"ok": False, "error": str(e)[:80], "ms": ms}

app = FastAPI()

IGNORE_KEYS = {
    "generationtime_ms", "timestamp", "request_id", "x-request-id",
    "elapsed", "duration_ms", "server_time", "utc_offset_seconds"
}

def deep_diff(a, b, path=""):
    """Return list of diff strings between two objects."""
    diffs = []
    if type(a) != type(b):
        diffs.append(f"{path}: {type(a).__name__} → {type(b).__name__}")
    elif isinstance(a, dict):
        for k in set(list(a.keys()) + list(b.keys())):
            if k in IGNORE_KEYS:
                continue
            if k not in a:
                diffs.append(f"{path}.{k}: mangler i A")
            elif k not in b:
                diffs.append(f"{path}.{k}: mangler i B")
            else:
                diffs.extend(deep_diff(a[k], b[k], f"{path}.{k}"))
    elif isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(deep_diff(x, y, f"{path}[{i}]"))
        if len(a) != len(b):
            diffs.append(f"{path}: længde {len(a)} → {len(b)}")
    elif a != b:
        av = str(a)[:40]
        bv = str(b)[:40]
        diffs.append(f"{path}: {av!r} → {bv!r}")
    return diffs

def compare_results(results: dict) -> dict:
    parsed = {}
    for env, r in results.items():
        if r["ok"]:
            try:
                parsed[env] = json.loads(r["text"])
            except:
                parsed[env] = r["text"]
        else:
            parsed[env] = None

    envs = list(parsed.keys())
    all_ok = all(v is not None for v in parsed.values())
    if not all_ok:
        return {"status": "error", "message": "Et eller flere miljøer fejlede", "diffs": {}}

    diffs = {}
    pairs = [("local","dev"), ("local","prod"), ("dev","prod")]
    for a, b in pairs:
        d = deep_diff(parsed[a], parsed[b])
        if d:
            diffs[f"{a}_vs_{b}"] = d[:20]

    if not diffs:
        return {"status": "identical", "message": "✅ Alle tre miljøer returnerer identiske data", "diffs": {}}
    return {"status": "differs", "message": f"⚠️ Forskelle fundet", "diffs": diffs}

@app.get("/api/query")
async def query(tool: str, args: str = "{}"):
    arg_dict = json.loads(args)
    results = await asyncio.gather(
        call_tool("local", tool, arg_dict),
        call_tool("dev",   tool, arg_dict),
        call_tool("prod",  tool, arg_dict),
    )
    data = {"local": results[0], "dev": results[1], "prod": results[2]}
    data["compare"] = compare_results(data)
    return JSONResponse(data)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(HTML)

HTML = """<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<title>MCP Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; }
  header { padding: 20px 30px; border-bottom: 1px solid #2a2a3a; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 1.3rem; font-weight: 600; }
  header span { font-size: 0.85rem; color: #888; }
  .controls { padding: 16px 30px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .controls select, .controls input { background: #1e1e2e; border: 1px solid #3a3a4a; border-radius: 8px; color: #e0e0e0; padding: 8px 12px; font-size: 0.9rem; }
  .controls input { width: 220px; }
  button { background: #4f8ef7; border: none; border-radius: 8px; color: white; padding: 8px 20px; font-size: 0.9rem; cursor: pointer; font-weight: 600; }
  button:hover { background: #3a7ae0; }
  button.auto { background: #2a2a3a; }
  button.auto.on { background: #2d5a27; border: 1px solid #4fc87a; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; padding: 20px 30px; }
  .card { background: #1a1a2e; border-radius: 12px; overflow: hidden; border: 1px solid #2a2a3a; }
  .card-header { padding: 14px 18px; display: flex; align-items: center; gap: 10px; }
  .card-header h2 { font-size: 1rem; font-weight: 700; letter-spacing: 1px; }
  .card-header .url { font-size: 0.72rem; color: #888; margin-top: 2px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: #444; flex-shrink: 0; }
  .dot.ok { background: #4fc87a; box-shadow: 0 0 8px #4fc87a88; }
  .dot.err { background: #f74f4f; box-shadow: 0 0 8px #f74f4f88; }
  .dot.loading { background: #f7a94f; animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.3 } }
  .card-body { padding: 16px 18px; }
  .ms { font-size: 0.8rem; color: #888; margin-bottom: 12px; }
  .result { background: #0f1117; border-radius: 8px; padding: 12px; font-size: 0.82rem; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; color: #c8d3f5; border: 1px solid #2a2a3a; }
  .result.err { color: #f74f4f; }
  .timestamp { text-align: right; font-size: 0.75rem; color: #555; padding: 8px 18px 12px; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #4f8ef7; border-top-color: transparent; border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .compare-bar { margin: 0 30px 20px; border-radius: 12px; padding: 16px 20px; border: 1px solid #2a2a3a; background: #1a1a2e; }
  .compare-bar.identical { border-color: #4fc87a44; background: #0d2a1a; }
  .compare-bar.differs   { border-color: #f7a94f44; background: #2a1a0a; }
  .compare-bar.error     { border-color: #f74f4f44; background: #2a0a0a; }
  .compare-title { font-size: 0.95rem; font-weight: 700; margin-bottom: 8px; }
  .diff-group { margin-top: 10px; }
  .diff-group h4 { font-size: 0.8rem; color: #888; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }
  .diff-item { font-size: 0.82rem; font-family: monospace; color: #f7a94f; background: #1e1000; border-radius: 4px; padding: 3px 8px; margin: 2px 0; }
  .diff-item.old { color: #f74f4f; background: #1e0000; }
  .diff-item.new { color: #4fc87a; background: #001e0a; }
</style>
</head>
<body>
<header>
  <div>🔌</div>
  <div>
    <h1>MCP Environment Dashboard</h1>
    <span>RcMcpAtea — REST Countries, Vejr, Valuta, Pokemon, Bøger, IP Geo</span>
  </div>
</header>

<div class="controls">
  <select id="tool">
    <option value="get_weather">🌤 get_weather</option>
    <option value="get_country_info">🌍 get_country_info</option>
    <option value="get_countries_by_region">🗺 get_countries_by_region</option>
    <option value="get_countries_by_currency">💶 get_countries_by_currency</option>
    <option value="get_exchange_rates">💱 get_exchange_rates</option>
    <option value="get_pokemon">⚡ get_pokemon</option>
    <option value="get_book">📚 get_book</option>
    <option value="get_ip_info">🌐 get_ip_info</option>
  </select>
  <input id="arg" type="text" value="Copenhagen" placeholder="Argument..." />
  <button onclick="query()">▶ Kør</button>
  <button class="auto" id="autoBtn" onclick="toggleAuto()">⏱ Auto-refresh</button>
  <span id="status" style="font-size:0.85rem;color:#888;"></span>
</div>

<div class="compare-bar" id="compareBar" style="display:none">
  <div class="compare-title" id="compareTitle"></div>
  <div id="compareDiffs"></div>
</div>

<div class="grid">
  <div class="card" id="card-local">
    <div class="card-header" style="background: #4f8ef722;">
      <div class="dot" id="dot-local"></div>
      <div><h2 style="color:#4f8ef7">LOCAL</h2><div class="url">http://localhost:4547</div></div>
    </div>
    <div class="card-body">
      <div class="ms" id="ms-local"></div>
      <div class="result" id="res-local">Klik ▶ Kør for at hente data...</div>
    </div>
    <div class="timestamp" id="ts-local"></div>
  </div>
  <div class="card" id="card-dev">
    <div class="card-header" style="background: #f7a94f22;">
      <div class="dot" id="dot-dev"></div>
      <div><h2 style="color:#f7a94f">DEV</h2><div class="url">rcmcpatea.wonderfulsmoke...azurecontainerapps.io</div></div>
    </div>
    <div class="card-body">
      <div class="ms" id="ms-dev"></div>
      <div class="result" id="res-dev">Klik ▶ Kør for at hente data...</div>
    </div>
    <div class="timestamp" id="ts-dev"></div>
  </div>
  <div class="card" id="card-prod">
    <div class="card-header" style="background: #4fc87a22;">
      <div class="dot" id="dot-prod"></div>
      <div><h2 style="color:#4fc87a">PROD</h2><div class="url">rcmcpatea.kindhill...azurecontainerapps.io</div></div>
    </div>
    <div class="card-body">
      <div class="ms" id="ms-prod"></div>
      <div class="result" id="res-prod">Klik ▶ Kør for at hente data...</div>
    </div>
    <div class="timestamp" id="ts-prod"></div>
  </div>
</div>

<script>
const ARGS = {
  get_weather: {city: "Copenhagen"}, get_country_info: {countryName: "Denmark"},
  get_countries_by_region: {region: "Europe"}, get_countries_by_currency: {currency: "DKK"},
  get_exchange_rates: {baseCurrency: "DKK"}, get_pokemon: {name: "pikachu"},
  get_book: {title: "Harry Potter"}, get_ip_info: {ip: "8.8.8.8"}
};
let autoInterval = null;

document.getElementById("tool").addEventListener("change", function() {
  const def = ARGS[this.value];
  if (def) document.getElementById("arg").value = Object.values(def)[0];
});

function setLoading(env) {
  document.getElementById("dot-"+env).className = "dot loading";
  document.getElementById("res-"+env).className = "result";
  document.getElementById("res-"+env).innerHTML = '<span class="spinner"></span>Henter...';
  document.getElementById("ms-"+env).textContent = "";
}

function setResult(env, data) {
  const dot = document.getElementById("dot-"+env);
  const res = document.getElementById("res-"+env);
  const ms  = document.getElementById("ms-"+env);
  const ts  = document.getElementById("ts-"+env);
  if (data.ok) {
    dot.className = "dot ok";
    try {
      const parsed = JSON.parse(data.text);
      res.className = "result";
      res.textContent = JSON.stringify(parsed, null, 2).substring(0, 2000);
    } catch {
      res.className = "result";
      res.textContent = data.text.substring(0, 2000);
    }
    ms.textContent = data.ms + " ms";
  } else {
    dot.className = "dot err";
    res.className = "result err";
    res.textContent = "❌ " + data.error;
    ms.textContent = data.ms + " ms";
  }
  ts.textContent = new Date().toLocaleTimeString("da-DK");
}

async function query() {
  const tool = document.getElementById("tool").value;
  const argVal = document.getElementById("arg").value.trim();
  const argKey = Object.keys(ARGS[tool])[0];
  const args = JSON.stringify({[argKey]: argVal});
  document.getElementById("status").textContent = "Kører " + tool + "...";
  ["local","dev","prod"].forEach(setLoading);
  try {
    const r = await fetch(`/api/query?tool=${encodeURIComponent(tool)}&args=${encodeURIComponent(args)}`);
    const data = await r.json();
    setResult("local", data.local);
    setResult("dev", data.dev);
    setResult("prod", data.prod);
    showCompare(data.compare);
    document.getElementById("status").textContent = "Opdateret " + new Date().toLocaleTimeString("da-DK");
  } catch(e) {
    document.getElementById("status").textContent = "Fejl: " + e.message;
  }
}

function showCompare(cmp) {
  const bar = document.getElementById("compareBar");
  const title = document.getElementById("compareTitle");
  const diffs = document.getElementById("compareDiffs");
  bar.style.display = "block";
  bar.className = "compare-bar " + cmp.status;
  title.textContent = cmp.message;
  diffs.innerHTML = "";
  if (cmp.status === "differs") {
    for (const [pair, items] of Object.entries(cmp.diffs)) {
      const g = document.createElement("div");
      g.className = "diff-group";
      const labels = {"local_vs_dev":"LOCAL vs DEV","local_vs_prod":"LOCAL vs PROD","dev_vs_prod":"DEV vs PROD"};
      g.innerHTML = `<h4>${labels[pair] || pair}</h4>`;
      items.forEach(d => {
        const el = document.createElement("div");
        el.className = "diff-item";
        el.textContent = d;
        g.appendChild(el);
      });
      diffs.appendChild(g);
    }
  }
}

function toggleAuto() {
  const btn = document.getElementById("autoBtn");
  if (autoInterval) {
    clearInterval(autoInterval);
    autoInterval = null;
    btn.className = "auto";
    btn.textContent = "⏱ Auto-refresh";
  } else {
    query();
    autoInterval = setInterval(query, 10000);
    btn.className = "auto on";
    btn.textContent = "⏹ Stop (10s)";
  }
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    print("MCP Dashboard -> http://localhost:9090")
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")
