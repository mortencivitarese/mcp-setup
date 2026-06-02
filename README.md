# MCP Setup

Portabel opsætning af MCP-servere til brug med Claude Code og Dive.

## Indhold

- `mcp/litellm_server.py` — MCP-server der kalder 100+ LLM'er via LiteLLM (OpenAI, Groq, Gemini, Ollama m.fl.)
- `mcp/.env.template` — Skabelon til API-nøgler (kopiér til `.env` og udfyld)
- `dive-config/mcp_config.json` — MCP-server konfiguration til Dive
- `claude-config/settings.json` — Skabelon til Claude Code settings (stier skal opdateres per maskine)

## Opsætning på ny PC

### 1. Installer forudsætninger
```
winget install OpenJS.NodeJS.LTS --scope user
winget install Python.Python.3.12 --scope user
pip install litellm "mcp[cli]"
npm install -g @modelcontextprotocol/server-filesystem
```

### 2. Klon dette repo
```
git clone https://github.com/mortencivitarese/mcp-setup
```

### 3. Sæt API-nøgler op
```
cp mcp/.env.template mcp/.env
# Rediger mcp/.env med dine nøgler
```

### 4. Kopiér filer til rette steder
- `mcp/litellm_server.py` → `~/.claude/mcp/litellm_server.py`
- `mcp/.env` → `~/.claude/mcp/.env`
- `dive-config/mcp_config.json` → `~/.dive/config/mcp_config.json`
- Opdatér stier i `claude-config/settings.json` og kopiér til `~/.claude/settings.json`
