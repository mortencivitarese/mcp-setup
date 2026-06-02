#!/usr/bin/env python3
"""
Local auth proxy for RcMcpAtea Azure MCP server.
Auto-refreshes Entra ID tokens and forwards to Azure.
Claude Code connects to http://localhost:8090/sse (no auth needed).
"""
import os
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response

load_dotenv(Path(__file__).parent / ".env")

TENANT_ID    = "b3f0b16b-81f9-4c36-a9ba-2b7fc139f0cb"
CLIENT_ID    = "90926159-18cc-4b41-80f6-9cf01a61af38"
CLIENT_SECRET = os.environ["RCMCPATEA_CLIENT_SECRET"]
TARGET_BASE  = "https://rcmcpatea.wonderfulsmoke-7219c7b7.westeurope.azurecontainerapps.io"
SCOPE        = f"api://{CLIENT_ID}/.default"

_token: str | None = None
_token_expires: datetime = datetime.min


async def get_token() -> str:
    global _token, _token_expires
    if _token and datetime.now() < _token_expires - timedelta(minutes=5):
        return _token
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": CLIENT_ID,
                  "client_secret": CLIENT_SECRET, "scope": SCOPE}
        )
        data = r.json()
    _token = data["access_token"]
    _token_expires = datetime.now() + timedelta(seconds=data["expires_in"])
    print(f"Token refreshed — udløber {_token_expires.strftime('%H:%M:%S')}")
    return _token


app = FastAPI()


@app.get("/sse")
async def sse_proxy(request: Request):
    token = await get_token()

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{TARGET_BASE}/sse",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "text/event-stream"}
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/{path:path}")
async def post_proxy(path: str, request: Request):
    token = await get_token()
    body = await request.body()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{TARGET_BASE}/{path}", content=body,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": request.headers.get("content-type", "application/json")}
        )
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"))


if __name__ == "__main__":
    print(f"RcMcpAtea auth proxy -> {TARGET_BASE}")
    print("Claude Code MCP URL: http://localhost:8090/sse")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")
