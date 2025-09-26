# /// script
# dependencies = [
#   "bbos",
#   "fastapi",
#   "uvicorn",
#   "wsproto",
#   "opencv-python",
#   "numpy",
# ]
# [tool.uv.sources]
# bbos = { path = "/home/bracketbot/BracketBotOS", editable = true }
# ///
import asyncio
import json
import signal
import numpy as np
import cv2
import threading
import queue
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

from bbos import Reader, Writer, Type

SPEED_LIN = 0.15  # m s⁻¹  forward/back
SPEED_ANG = 0.3 # rad s⁻¹ CCW+

_stop = False

def _sigint(*_):
    global _stop
    _stop = True

signal.signal(signal.SIGINT, _sigint)

# Global queues
jpeg_queue = queue.Queue(maxsize=3)
cmd_queue = queue.Queue(maxsize=3)

def reader_loop():
    with Reader('camera.rect') as r_rect, \
         Writer('drive.ctrl', Type("drive_ctrl")) as w_ctrl:
        while not _stop:
            # Handle camera data
            if r_rect.ready():
                img = r_rect.data['rect']
                # Encode as JPEG
                encode_param = [
                    int(cv2.IMWRITE_JPEG_QUALITY), 85,
                    int(cv2.IMWRITE_JPEG_PROGRESSIVE), 0,
                    int(cv2.IMWRITE_JPEG_OPTIMIZE), 0
                ]
                _, encoded = cv2.imencode('.jpg', img, encode_param)
                
                try:
                    jpeg_queue.put_nowait(encoded.tobytes())
                except:
                    try:
                        jpeg_queue.get_nowait()
                        jpeg_queue.put_nowait(encoded.tobytes())
                    except:
                        pass
            # Handle drive commands
            with w_ctrl.buf() as buf:
              try:
                  cmd = cmd_queue.get_nowait()
                  twist = np.array([cmd['y'] * SPEED_LIN, cmd['x'] * SPEED_ANG], dtype=np.float32)
                  buf["twist"] = twist
              except queue.Empty:
                  pass

def server(port=8008):
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse("""
<!doctype html><meta charset=utf-8>
<title>BracketBot Teleop</title>
<style>
body {
  margin: 0;
  background: #111;
  color: white;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  overflow: hidden;
}

.container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  gap: 20px;
}

.title {
  color: #4CAF50;
  font-size: 24px;
  margin-bottom: 10px;
}

.main-content {
  display: flex;
  align-items: center;
  gap: 30px;
}

#feed {
  max-height: 540px;
  max-width: 720px;
  border: 2px solid #333;
  border-radius: 12px;
  background: #222;
}

.joystick-container {
  position: relative;
  width: 240px;
  height: 240px;
}

#joystick {
  background: #222;
  border: 2px solid #444;
  border-radius: 50%;
  touch-action: none;
  cursor: grab;
}

#joystick:active {
  cursor: grabbing;
}

.info {
  background: #222;
  border-radius: 8px;
  padding: 15px 25px;
  text-align: center;
  font-size: 14px;
  color: #aaa;
}

.speed-value {
  color: #4CAF50;
  font-weight: bold;
}

.controls-info {
  margin-top: 10px;
  font-size: 12px;
  color: #666;
}
</style>

<div class="container">
  <h1 class="title">🤖 BracketBot Teleop Control</h1>
  
  <div class="main-content">
    <img id="feed" src="/feed" alt="Camera Feed">
    
    <div class="joystick-container">
      <canvas id="joystick" width="240" height="240"></canvas>
    </div>
  </div>
  
  <div class="info">
    <div>Linear: <span id="linear-speed" class="speed-value">0.00</span> m/s</div>
    <div>Angular: <span id="angular-speed" class="speed-value">0.00</span> rad/s</div>
    <div class="controls-info">Click and drag the joystick to control the robot</div>
  </div>
</div>

<script>
const canvas = document.getElementById("joystick");
const ctx = canvas.getContext("2d");
const centerX = canvas.width / 2;
const centerY = canvas.height / 2;
const maxRadius = 80;
let knobX = centerX;
let knobY = centerY;
let isDragging = false;

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  
  // Draw outer circle
  ctx.beginPath();
  ctx.arc(centerX, centerY, 100, 0, 2 * Math.PI);
  ctx.strokeStyle = "#444";
  ctx.lineWidth = 3;
  ctx.stroke();
  
  // Draw crosshairs
  ctx.beginPath();
  ctx.moveTo(centerX - 100, centerY);
  ctx.lineTo(centerX + 100, centerY);
  ctx.moveTo(centerX, centerY - 100);
  ctx.lineTo(centerX, centerY + 100);
  ctx.strokeStyle = "#333";
  ctx.lineWidth = 1;
  ctx.stroke();
  
  // Draw knob
  ctx.beginPath();
  ctx.arc(knobX, knobY, 25, 0, 2 * Math.PI);
  ctx.fillStyle = "#4CAF50";
  ctx.fill();
  ctx.strokeStyle = "#66BB6A";
  ctx.lineWidth = 2;
  ctx.stroke();
}

function updatePosition(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left - centerX;
  const y = clientY - rect.top - centerY;
  
  const distance = Math.sqrt(x * x + y * y);
  if (distance <= maxRadius) {
    knobX = centerX + x;
    knobY = centerY + y;
  } else {
    const angle = Math.atan2(y, x);
    knobX = centerX + Math.cos(angle) * maxRadius;
    knobY = centerY + Math.sin(angle) * maxRadius;
  }
  
  draw();
  sendCommand();
}

function resetPosition() {
  knobX = centerX;
  knobY = centerY;
  draw();
  sendCommand();
}

function sendCommand() {
  const x = (knobX - centerX) / maxRadius;
  const y = -(knobY - centerY) / maxRadius;  // Invert Y for intuitive control
  
  document.getElementById("linear-speed").textContent = (y * 1.2).toFixed(2);
  document.getElementById("angular-speed").textContent = (-x * 1.0).toFixed(2);
  
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ x: -x, y: y }));
  }
}

canvas.addEventListener("mousedown", (e) => {
  isDragging = true;
  updatePosition(e.clientX, e.clientY);
});

canvas.addEventListener("mousemove", (e) => {
  if (isDragging) {
    updatePosition(e.clientX, e.clientY);
  }
});

canvas.addEventListener("mouseup", () => {
  isDragging = false;
  resetPosition();
});

canvas.addEventListener("mouseleave", () => {
  if (isDragging) {
    isDragging = false;
    resetPosition();
  }
});

// Touch events
canvas.addEventListener("touchstart", (e) => {
  e.preventDefault();
  isDragging = true;
  const touch = e.touches[0];
  updatePosition(touch.clientX, touch.clientY);
});

canvas.addEventListener("touchmove", (e) => {
  e.preventDefault();
  if (isDragging) {
    const touch = e.touches[0];
    updatePosition(touch.clientX, touch.clientY);
  }
});

canvas.addEventListener("touchend", (e) => {
  e.preventDefault();
  isDragging = false;
  resetPosition();
});

const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");

ws.onopen = () => {
  console.log("[teleop] WebSocket connected");
};

ws.onclose = () => {
  console.log("[teleop] WebSocket disconnected");
};

draw();
</script>
""")

    @app.get("/feed")
    async def feed():
        return StreamingResponse(
            generate_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "close",
                "X-Accel-Buffering": "no",
            }
        )
    
    async def generate_frames():
        while not _stop:
            try:
                frame = jpeg_queue.get_nowait()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            except:
                await asyncio.sleep(0.005)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        print("[teleop] WebSocket client connected")
        
        try:
            while not _stop:
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                    data = json.loads(message)
                    
                    if "x" in data and "y" in data:
                        # Put command in queue for main thread
                        cmd = {'x': data['x'], 'y': data['y']}
                        try:
                            cmd_queue.put_nowait(cmd)
                        except queue.Full:
                            # Remove old command and add new one
                            try:
                                cmd_queue.get_nowait()
                            except queue.Empty:
                                pass
                            cmd_queue.put_nowait(cmd)
                            
                except asyncio.TimeoutError:
                    continue
                    
        except WebSocketDisconnect:
            print("[teleop] WebSocket client disconnected")
            # Send stop command
            try:
                cmd_queue.put_nowait({'x': 0, 'y': 0})
            except queue.Full:
                pass
        except Exception as e:
            print(f"[teleop] WebSocket error: {e}")

    # Run the server
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="error", 
                access_log=False, ws="wsproto")


def main():
    # Start reader loop thread
    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()
    
    print("[teleop] Starting teleop control server on http://0.0.0.0:8008")
    print("[teleop] View interface at http://<robot-ip>:8008/")
    print("[teleop] Camera feed at http://<robot-ip>:8008/feed")
    
    # Run server in main thread
    server(8008)

    _stop = True
    reader_thread.join()


if __name__ == "__main__":
    main()