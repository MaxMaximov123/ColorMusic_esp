import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()

CAMERAS = {}
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "5"))
MAX_FPS = int(os.environ.get("MAX_FPS", "15"))

for pair in os.environ.get("CAMERAS", "").split(","):
    pair = pair.strip()
    if "=" in pair:
        name, url = pair.split("=", 1)
        CAMERAS[name.strip()] = url.strip()


@app.get("/", response_class=HTMLResponse)
async def index():
    cam_buttons = "".join(
        f'<button onclick="sel(\'{n}\')" class="btn">{n}</button>' for n in CAMERAS
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RTSP Viewer</title>
<style>
  body {{ margin:0; background:#111; color:#fff; font-family:system-ui; display:flex; flex-direction:column; align-items:center; }}
  .bar {{ padding:12px; display:flex; gap:8px; flex-wrap:wrap; justify-content:center; }}
  .btn {{ padding:8px 20px; background:#333; color:#fff; border:1px solid #555; border-radius:6px; cursor:pointer; font-size:14px; }}
  .btn:hover {{ background:#444; }}
  #stream {{ max-width:100%; max-height:calc(100vh - 60px); background:#000; display:none; }}
  .msg {{ color:#888; margin-top:40px; }}
</style></head><body>
  <div class="bar">{cam_buttons}</div>
  <img id="stream">
  <div id="msg" class="msg">Выберите камеру</div>
  <script>
    function sel(name) {{
      const img = document.getElementById('stream');
      const msg = document.getElementById('msg');
      img.src = '/stream/' + name + '?t=' + Date.now();
      img.style.display = 'block';
      msg.style.display = 'none';
      img.onerror = () => {{ msg.textContent = 'Ошибка потока'; msg.style.display = 'block'; img.style.display = 'none'; }};
    }}
  </script>
</body></html>"""


@app.get("/stream/{camera_id}")
async def stream(camera_id: str):
    if camera_id not in CAMERAS:
        return HTMLResponse("Camera not found", status_code=404)
    rtsp_url = CAMERAS[camera_id]

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-fflags", "+genpts+discardcorrupt+nobuffer",
            "-flags", "low_delay",
            "-i", rtsp_url,
            "-f", "image2pipe",
            "-c:v", "mjpeg",
            "-q:v", str(JPEG_QUALITY),
            "-r", str(MAX_FPS),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        buf = b""
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi == -1:
                        buf = b""
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi == -1:
                        buf = buf[soi:]
                        break
                    frame = buf[soi : eoi + 2]
                    buf = buf[eoi + 2 :]
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
        finally:
            proc.kill()
            await proc.wait()

    return StreamingResponse(
        generate(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/cameras")
async def list_cameras():
    return {"cameras": list(CAMERAS.keys())}
