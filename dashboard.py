# /// script
# dependencies = [
#   "bbos",
#   "fastapi",
#   "uvicorn",
#   "wsproto",
#   "httpx",
# ]
# [tool.uv.sources]
# bbos = { path = "/home/bracketbot/BracketBotOS", editable = true }
# ///
import asyncio
import json
import signal
import socket
import re
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn
import httpx

from bbos.app_manager import get_status, start_app, stop_app

REFRESH_TIME: float = 2.0  # seconds
PORT_SCAN_RANGE = range(8000, 8010)  # Scan ports 8000-8009

_stop = False

def _sigint(*_):
    global _stop
    _stop = True

signal.signal(signal.SIGINT, _sigint)

async def check_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a port is open and accepting connections."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False

async def get_page_title(port: int, timeout: float = 2.0) -> str:
    """Attempt to get the page title from a service running on the given port."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"http://localhost:{port}/")
            if response.status_code == 200:
                # Try to extract title from HTML
                content = response.text
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
                if title_match:
                    return title_match.group(1).strip()
                # If no title, return a generic name
                return f"Service on port {port}"
    except Exception:
        pass
    return f"Port {port}"

async def scan_ports() -> dict:
    """Scan for active services in the port range."""
    active_services = {}
    
    # Check each port in parallel
    tasks = []
    for port in PORT_SCAN_RANGE:
        tasks.append(check_port_open('localhost', port))
    
    results = await asyncio.gather(*tasks)
    
    # For open ports, get their titles
    title_tasks = []
    open_ports = []
    for port, is_open in zip(PORT_SCAN_RANGE, results):
        if is_open:
            open_ports.append(port)
            title_tasks.append(get_page_title(port))
    
    if title_tasks:
        titles = await asyncio.gather(*title_tasks)
        for port, title in zip(open_ports, titles):
            active_services[port] = title
    
    return active_services

def main():
    app = FastAPI()
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse("""
<!doctype html><meta charset=utf-8>
<title>BracketBot Dashboard</title>
<style>
body {
  margin: 20px;
  background: #111;
  color: white;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
}

h1, h2 {
  color: #4CAF50;
  text-align: center;
}

.section {
  background: #222;
  border-radius: 12px;
  padding: 20px;
  margin: 20px 0;
}

.app-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 15px;
  margin-top: 15px;
}

.app-card {
  background: #333;
  border-radius: 8px;
  padding: 15px;
  border: 2px solid #444;
}

.app-card.running {
  border-color: #4CAF50;
}

.app-card.stopped {
  border-color: #f44336;
}

.app-name {
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 10px;
}

.app-status {
  margin: 5px 0;
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 14px;
}

.status-running {
  background: #4CAF50;
  color: white;
}

.status-stopped {
  background: #f44336;
  color: white;
}

.toggle-btn {
  background: #2196F3;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  margin-top: 10px;
  width: 100%;
}

.toggle-btn:hover {
  background: #1976D2;
}

.toggle-btn:disabled {
  background: #666;
  cursor: not-allowed;
}

.loading {
  text-align: center;
  color: #888;
  font-style: italic;
}

.services-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 15px;
  margin-top: 15px;
}

.service-btn {
  background: #2196F3;
  color: white;
  border: none;
  padding: 20px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
  text-align: center;
  text-decoration: none;
  display: block;
  transition: all 0.3s ease;
  box-shadow: 0 2px 5px rgba(0,0,0,0.3);
}

.service-btn:hover {
  background: #1976D2;
  transform: translateY(-2px);
  box-shadow: 0 4px 10px rgba(0,0,0,0.4);
}

.service-port {
  font-size: 12px;
  color: #BBB;
  margin-top: 5px;
}
</style>

<div class="container">
  <h1>🤖 BracketBot Dashboard</h1>
  
  <div class="section">
    <h2>Active Services</h2>
    <div id="services-container" class="loading">Scanning for services...</div>
  </div>
  
  <div class="section">
    <h2>Applications</h2>
    <div id="apps-container" class="loading">Loading apps...</div>
  </div>
</div>

<script>
let ws = null;

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(protocol + location.host + "/ws");
  
  ws.onopen = function() {
    console.log("WebSocket connected");
    requestStatus();
  };
  
  ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    if (data.apps) {
      updateApps(data.apps);
    }
    if (data.services) {
      updateServices(data.services);
    }
  };
  
  ws.onclose = function() {
    console.log("WebSocket disconnected, reconnecting...");
    setTimeout(connectWebSocket, 2000);
  };
  
  ws.onerror = function(error) {
    console.error("WebSocket error:", error);
  };
}

function requestStatus() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: "get_status" }));
  }
}

function toggleApp(appName, isRunning) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const action = isRunning ? "stop_app" : "start_app";
    ws.send(JSON.stringify({ action: action, app_name: appName }));
  }
}

function updateServices(services) {
  const container = document.getElementById("services-container");
  container.innerHTML = "";
  container.className = "services-grid";
  
  if (Object.keys(services).length === 0) {
    container.innerHTML = "<div class='loading'>No active services found</div>";
    return;
  }
  
  for (const [port, title] of Object.entries(services)) {
    const link = document.createElement("a");
    link.href = `http://${window.location.hostname}:${port}/`;
    link.target = "_blank";
    link.className = "service-btn";
    
    link.innerHTML = `
      <div>${title}</div>
      <div class="service-port">Port ${port}</div>
    `;
    
    container.appendChild(link);
  }
}

function updateApps(apps) {
  const container = document.getElementById("apps-container");
  container.innerHTML = "";
  container.className = "app-grid";
  
  if (Object.keys(apps).length === 0) {
    container.innerHTML = "<div class='loading'>No apps found</div>";
    return;
  }
  
  for (const [appName, isRunning] of Object.entries(apps)) {
    const card = document.createElement("div");
    card.className = `app-card ${isRunning ? "running" : "stopped"}`;
    
    card.innerHTML = `
      <div class="app-name">${appName}</div>
      <div class="app-status ${isRunning ? "status-running" : "status-stopped"}">
        ${isRunning ? "RUNNING" : "STOPPED"}
      </div>
      <button class="toggle-btn" onclick="toggleApp('${appName}', ${isRunning})">
        ${isRunning ? "Stop" : "Start"} App
      </button>
    `;
    
    container.appendChild(card);
  }
}

// Connect WebSocket and refresh status periodically
connectWebSocket();
setInterval(requestStatus, """ + str(int(REFRESH_TIME * 1000)) + """);
</script>
""")
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        print("[dashboard] WebSocket client connected")
        status = lambda: get_status(exclude=['dashboard'])
        
        async def send_full_status():
            apps = status()
            services = await scan_ports()
            await websocket.send_text(json.dumps({
                "apps": apps,
                "services": services
            }))
        
        try:
            while not _stop:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                    data = json.loads(message)
                    
                    if data.get("action") == "get_status":
                        await send_full_status()
                    
                    elif data.get("action") == "start_app":
                        app_name = data.get("app_name")
                        if app_name:
                            success = start_app(app_name)
                            if success:
                                await send_full_status()
                    
                    elif data.get("action") == "stop_app":
                        app_name = data.get("app_name")
                        if app_name:
                            success = stop_app(app_name)
                            print(f"[dashboard] Stopping app: {app_name} - {success}")
                            if success:
                                await send_full_status()
                
                except asyncio.TimeoutError:
                    # Send periodic status updates
                    await send_full_status()
                    
        except WebSocketDisconnect:
            print("[dashboard] WebSocket client disconnected")
        except Exception as e:
            print(f"[dashboard] WebSocket error: {e}")

    # Run the server
    uvicorn.run(
      app,
      host="0.0.0.0",
      port=8001,
      ws="wsproto",
      log_level="info",
    )


if __name__ == "__main__":
    main() 