# /// script
# dependencies = [
#   "bbos",
#   "fastapi",
#   "uvicorn",
#   "opencv-python",
#   "numpy",
# ]
# [tool.uv.sources]
# bbos = { path = "/home/bracketbot/BracketBotOS", editable = true }
# ///
import asyncio
import threading
from queue import Queue
import cv2
import numpy as np
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
import uvicorn
from bbos import Reader, Config

jpeg_queue = Queue(maxsize=2)

CFG_D = Config('depth')

def camera_reader():
    with Reader('camera.rect') as r_rect:
        while True:
            if r_rect.ready():
                img = r_rect.data['rect']
                if img is None:
                    continue
                encode_param = [
                    int(cv2.IMWRITE_JPEG_QUALITY), 75,  # Lower quality for faster encoding
                    int(cv2.IMWRITE_JPEG_PROGRESSIVE), 0,  # Disable progressive for lower latency
                    int(cv2.IMWRITE_JPEG_OPTIMIZE), 0  # Disable optimization for speed
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

app = FastAPI()

async def generate_frames():
    while True:
        try:
            frame = jpeg_queue.get(timeout=0.05)  # Lower timeout for better responsiveness
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except:
            await asyncio.sleep(0.005)  # Shorter sleep for lower latency

@app.get("/stream")
async def video_feed():
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if present
        }
    )

@app.get("/")
async def index():
    html = '''
    <html>
    <head>
        <title>Camera Stream</title>
        <style>
            body { margin: 0; padding: 0; background: #000; overflow: hidden; }
            img { 
                width: 100%; 
                height: 100vh; 
                object-fit: contain; 
                display: block;
                image-rendering: -webkit-optimize-contrast;
                image-rendering: crisp-edges;
            }
        </style>
    </head>
    <body>
        <img src="/stream" />
    </body>
    </html>
    '''
    return Response(content=html, media_type="text/html")

@app.get("/frame")
async def get_single_frame():
    """Get a single frame as JPEG for testing or snapshots"""
    try:
        frame = jpeg_queue.get(timeout=0.5)
        return Response(content=frame, media_type="image/jpeg")
    except:
        return Response(status_code=503)

def main():
    reader_thread = threading.Thread(target=camera_reader, daemon=True)
    reader_thread.start()
    
    print("[+] Starting camera stream server on http://0.0.0.0:8003")
    print("[+] View stream at http://<robot-ip>:8003/")
    print("[+] Direct MJPEG stream at http://<robot-ip>:8003/stream")
    print("[+] Single frame at http://<robot-ip>:8003/frame")
    
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="error", 
                access_log=False)  # Disable access logs for performance

if __name__ == "__main__":
    main()