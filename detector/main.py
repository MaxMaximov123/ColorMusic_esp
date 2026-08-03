"""
Video Detector Service — FastAPI
Connects to IPeye cameras via WebSocket, decodes fMP4 with FFmpeg,
optionally runs YOLO detection, streams JPEG frames to web clients.
"""

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from typing import Optional

import cv2
import numpy as np
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("detector")

app = FastAPI(title="Video Detector Service")

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
YOLO_IMGSZ = int(os.environ.get("YOLO_IMGSZ", "640"))
YOLO_CONF = float(os.environ.get("YOLO_CONF", "0.25"))

ORIGIN = "https://www.ipeye.ru"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 YaBrowser/26.4.0.0 Safari/537.36"
)

VALID_BOXES = {b"ftyp", b"styp", b"moov", b"moof", b"mdat",
               b"sidx", b"free", b"skip", b"mfra"}

COLORS = [
    (76, 175, 80), (33, 150, 243), (255, 152, 0), (156, 39, 176),
    (244, 67, 54), (0, 188, 212), (255, 235, 59), (121, 85, 72),
]


# ── IPeye Client (порт из оригинала 1:1) ──

class IpeyeClient:
    BASE_URL = "https://www.ipeye.ru/ipeye_service/index.php"
    SITE_URL = "https://www.ipeye.ru"
    API_URL = "https://api.ipeye.ru"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        self.logged_in = False

    def login(self, login: str, password: str) -> bool:
        try:
            self.session.get(
                self.SITE_URL + "/",
                headers={"Accept": "text/html"},
                timeout=15,
            )
            logger.info(f"PHPSESSID: {self.session.cookies.get('PHPSESSID', '?')[:12]}...")

            resp = self.session.post(
                f"{self.BASE_URL}?route=proc_login",
                data={
                    "service_url_relative": "ipeye_service/",
                    "login": login,
                    "pass": password,
                    "captcha": "false",
                },
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": self.SITE_URL + "/",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": ORIGIN,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=15,
            )
            logger.info(f"Login response: {resp.status_code}, body={resp.text[:200]}")
            if resp.status_code != 200:
                return False

            idx_resp = self.session.get(
                f"{self.BASE_URL}?route=page_index",
                headers={"Accept": "text/html", "Referer": self.SITE_URL + "/"},
                timeout=15,
            )
            logger.info(f"page_index: {idx_resp.status_code}, cookies: {list(self.session.cookies.keys())}")

            self.logged_in = True
            return True
        except Exception as e:
            logger.error(f"IPeye login error: {e}")
            return False

    def get_cameras(self) -> list[dict]:
        try:
            data = {
                "draw": "1",
                "start": "0",
                "length": "100",
                "search[value]": "",
                "search[regex]": "true",
                "order[0][column]": "2",
                "order[0][dir]": "asc",
            }
            col_names = [
                "devices.devcode", "devices.devcode", "devices.name",
                "devices_groups.name", "tariffs.name", "devices.dvr_limit",
                "", "", "", "devices_groups.id", "devices.permissions",
                "devices.model_id", "devices.storage_server",
            ]
            for i, col_data in enumerate(col_names):
                data[f"columns[{i}][data]"] = col_data
                data[f"columns[{i}][name]"] = ""
                data[f"columns[{i}][searchable]"] = "true" if col_data else "false"
                data[f"columns[{i}][orderable]"] = "true" if col_data else "false"
                data[f"columns[{i}][search][value]"] = ""
                data[f"columns[{i}][search][regex]"] = "false"

            resp = self.session.post(
                f"{self.BASE_URL}?route=proc_device",
                data=data,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": f"{self.BASE_URL}?route=page_index",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            result = resp.json()
            cameras = []
            for item in result.get("data", []):
                cameras.append({
                    "id": item.get("devcode", ""),
                    "name": item.get("device_name", ""),
                    "storage_server": item.get("storage_server", ""),
                })
            logger.info(f"Found {len(cameras)} cameras")
            self.cameras = cameras
            return cameras
        except Exception as e:
            logger.error(f"IPeye get_cameras error: {e}")
            return []

    def get_stream_status(self, device_ids: list[str]) -> dict:
        try:
            resp = self.session.post(
                f"{self.API_URL}/v1/stream/status_array_full",
                data={"streams": json.dumps(device_ids)},
                headers={
                    "Accept": "*/*",
                    "Referer": f"{self.BASE_URL}?route=page_index",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": ORIGIN,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                for did, info in data.items():
                    logger.info(f"Stream {did[:12]}... status={info.get('status','?')}, "
                                f"{info.get('Width','?')}x{info.get('Height','?')}")
                return data
        except Exception as e:
            logger.error(f"IPeye stream_status error: {e}")
        return {}

    def authorize_stream(self, device_id: str) -> Optional[str]:
        try:
            resp = self.session.get(
                f"{self.BASE_URL}?route=page_play_ajax&new_websocket&devid={device_id}",
                headers={
                    "Accept": "*/*",
                    "Referer": f"{self.BASE_URL}?route=page_play&devcode={device_id}",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=10,
            )
            logger.info(f"authorize_stream {device_id[:12]}... -> {resp.status_code}")
            logger.info(f"authorize_stream body: {resp.text[:300]}")
            if resp.status_code == 200:
                data = resp.json()
                server = data.get("server", "")
                return server if server else None
        except Exception as e:
            logger.error(f"IPeye authorize error: {e}")
        return None


# ── YOLO Detector ──

class YoloDetector:
    def __init__(self, model_path: str):
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.model.fuse()

        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self.device = "mps"
        elif torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        logger.info(f"YOLO device: {self.device}")

        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self.model.predict(dummy, imgsz=YOLO_IMGSZ, conf=YOLO_CONF,
                           device=self.device, half=(self.device == "mps"),
                           verbose=False)

        self._queue: deque = deque(maxlen=1)
        self._event = threading.Event()
        self._results: list[dict] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, frame_bgr: np.ndarray):
        self._queue.append(frame_bgr)
        self._event.set()

    def get_results(self) -> list[dict]:
        with self._lock:
            return list(self._results)

    def _loop(self):
        while self._running:
            self._event.wait(timeout=0.5)
            self._event.clear()
            if not self._queue:
                continue
            try:
                frame = self._queue.pop()
            except IndexError:
                continue
            try:
                results = self.model.predict(
                    frame, verbose=False, stream=True, imgsz=YOLO_IMGSZ,
                    conf=YOLO_CONF, device=self.device,
                    half=(self.device == "mps"),
                )
                result = next(results)
                dets = []
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    dets.append({
                        "bbox": [x1, y1, x2, y2],
                        "conf": float(box.conf[0]),
                        "cls": int(box.cls[0]),
                        "label": self.model.names[int(box.cls[0])],
                    })
                with self._lock:
                    self._results = dets
            except StopIteration:
                pass
            except Exception as e:
                logger.error(f"YOLO inference error: {e}")
                time.sleep(0.1)


def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    out = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        color = COLORS[det["cls"] % len(COLORS)]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f'{det["label"]} {det["conf"]:.0%}'
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, y1 - th - baseline - 6), (x1 + tw + 6, y1), color, -1)
        cv2.putText(out, label, (x1 + 3, y1 - baseline - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out


# ── Session Storage ──

ipeye_sessions: dict[str, IpeyeClient] = {}


@app.post("/api/login")
async def ipeye_login(body: dict):
    login_str = body.get("login", "")
    password = body.get("password", "")

    if not login_str or not password:
        return JSONResponse({"error": "Login and password required"}, 400)

    client = IpeyeClient()
    success = await asyncio.to_thread(client.login, login_str, password)
    if not success:
        return JSONResponse({"error": "Authentication failed"}, 401)

    cameras = await asyncio.to_thread(client.get_cameras)

    device_ids = [c["id"] for c in cameras]
    if device_ids:
        statuses = await asyncio.to_thread(client.get_stream_status, device_ids)
        if isinstance(statuses, dict):
            for cam in cameras:
                info = statuses.get(cam["id"], {})
                cam["online"] = int(info.get("status", 0)) == 3
                cam["width"] = int(info.get("Width", 0) or 0)
                cam["height"] = int(info.get("Height", 0) or 0)

    session_id = str(uuid.uuid4())
    ipeye_sessions[session_id] = client

    return {"session": session_id, "cameras": cameras}


@app.get("/api/models")
async def list_models():
    models = []
    for d in [MODELS_DIR]:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".pt"):
                    models.append(f)
    if not models:
        models = ["yolo11n.pt"]
    return {"models": models}


# ── Stream Pipeline (порт из оригинала StreamPlayer) ──

class StreamPipeline:
    def __init__(self, ipeye_client: IpeyeClient, device_id: str,
                 model_name: Optional[str] = None, detect: bool = False):
        self.client = ipeye_client
        self.device_id = device_id
        self.model_name = model_name
        self.detect = detect
        self.detector: Optional[YoloDetector] = None
        self.running = False
        self.frame_queue: deque = deque(maxlen=2)
        self._ws_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self.width = 1280
        self.height = 720
        self.error: Optional[str] = None

        self._init_buf = b""
        self._got_moov = False
        self._ffmpeg_started = False
        self._pending_chunks: list[bytes] = []

    def start(self):
        self.running = True

        if self.detect and self.model_name:
            try:
                model_path = self._find_model(self.model_name)
                self.detector = YoloDetector(model_path)
                self.detector.start()
                logger.info(f"YOLO started: {self.model_name}")
            except Exception as e:
                logger.error(f"YOLO load failed: {e}")
                self.detector = None

        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()

    def stop(self):
        self.running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self.detector:
            self.detector.stop()
        if self._ffmpeg_stdin:
            try:
                self._ffmpeg_stdin.close()
            except Exception:
                pass
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.terminate()
            except Exception:
                pass

    def _find_model(self, name: str) -> str:
        p = os.path.join(MODELS_DIR, name)
        if os.path.isfile(p):
            return p
        return name

    _ws = None
    _ffmpeg_stdin = None

    # ── FFmpeg (точная копия оригинала) ──

    def _start_ffmpeg(self, init_data: bytes):
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+discardcorrupt+nobuffer",
            "-flags", "low_delay",
            "-analyzeduration", "3000000",
            "-probesize", "5000000",
            "-f", "mp4",
            "-i", "pipe:0",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-an", "-sn",
            "pipe:1",
        ]
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10 ** 6,
        )
        self._ffmpeg_stdin = self._ffmpeg_proc.stdin

        self._ffmpeg_stdin.write(init_data)
        self._ffmpeg_stdin.flush()
        logger.info(f"FFmpeg init segment: {len(init_data)} bytes")

        for chunk in self._pending_chunks:
            try:
                self._ffmpeg_stdin.write(chunk)
            except Exception:
                break
        self._ffmpeg_stdin.flush()
        self._pending_chunks.clear()
        self._ffmpeg_started = True

        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._reader_thread.start()
        threading.Thread(target=self._log_stderr, daemon=True).start()

    def _feed(self, data: bytes):
        if self._ffmpeg_started and self._ffmpeg_stdin:
            try:
                self._ffmpeg_stdin.write(data)
                self._ffmpeg_stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        else:
            self._pending_chunks.append(data)

    def _read_frames(self):
        frame_size = self.width * self.height * 3
        buf = b""
        stdout = self._ffmpeg_proc.stdout

        while self.running:
            chunk = stdout.read(frame_size - len(buf))
            if not chunk:
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is not None:
                    logger.info("FFmpeg process exited")
                    break
                time.sleep(0.005)
                continue
            buf += chunk
            while len(buf) >= frame_size:
                raw = buf[:frame_size]
                buf = buf[frame_size:]
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (self.height, self.width, 3)
                )

                if self.detector:
                    self.detector.submit(frame.copy())
                    dets = self.detector.get_results()
                    if dets:
                        frame = draw_detections(frame, dets)

                _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.frame_queue.append(jpeg.tobytes())

        logger.info("Frame reader stopped")

    def _log_stderr(self):
        if not self._ffmpeg_proc or not self._ffmpeg_proc.stderr:
            return
        for line in self._ffmpeg_proc.stderr:
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.debug(f"ffmpeg: {text}")

    # ── WebSocket к камере (точная копия оригинала StreamPlayer) ──

    def _run_ws(self):
        import websocket as ws_lib

        try:
            ws_server = self.client.authorize_stream(self.device_id)

            if not ws_server:
                self.error = "Failed to authorize stream"
                self.running = False
                return

            statuses = self.client.get_stream_status([self.device_id])
            if isinstance(statuses, dict) and self.device_id in statuses:
                info = statuses[self.device_id]
                self.width = int(info.get("Width", 1280)) or 1280
                self.height = int(info.get("Height", 720)) or 720

            # URL использует device_id как name (как в оригинале)
            ws_url = f"wss://{ws_server}/ws/mp4/live?name={self.device_id}"
            logger.info(f"Connecting to camera WS: {ws_url} ({self.width}x{self.height})")

            msg_count = 0

            def on_open(ws_conn):
                logger.info(f"Camera WS connected to {ws_server}")

            def on_message(ws_conn, message):
                nonlocal msg_count

                if not self.running:
                    ws_conn.close()
                    return
                if not isinstance(message, bytes):
                    logger.debug(f"WS text: {str(message)[:200]}")
                    return

                msg_count += 1
                if len(message) < 8:
                    return

                box_type = message[4:8]

                if msg_count <= 5:
                    logger.info(f"WS msg#{msg_count}: {len(message)} bytes, box={box_type}")

                if box_type not in VALID_BOXES:
                    return

                # Собираем init-сегмент: ftyp + moov
                if not self._got_moov:
                    if box_type == b"ftyp":
                        self._init_buf = message
                        if b"moov" in message:
                            self._got_moov = True
                            logger.info(f"ftyp+moov in one message: {len(message)} bytes")
                            threading.Thread(
                                target=self._start_ffmpeg,
                                args=(self._init_buf,),
                                daemon=True,
                            ).start()
                        else:
                            logger.info(f"ftyp: {len(message)} bytes, waiting for moov...")
                        return
                    elif box_type == b"moov":
                        self._init_buf += message
                        self._got_moov = True
                        logger.info(f"moov: {len(message)} bytes, total init: {len(self._init_buf)} bytes")
                        threading.Thread(
                            target=self._start_ffmpeg,
                            args=(self._init_buf,),
                            daemon=True,
                        ).start()
                        return
                    elif box_type == b"styp":
                        self._init_buf += message
                        return
                    else:
                        return

                self._feed(message)

            def on_error(ws_conn, error):
                logger.error(f"Camera WS error: {error}")
                self.error = str(error)

            def on_close(ws_conn, code, msg):
                logger.info(f"Camera WS closed: code={code}, msg={msg}")
                self.running = False

            self._ws = ws_lib.WebSocketApp(
                ws_url,
                header={
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "ru",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "User-Agent": USER_AGENT,
                },
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            self._ws.run_forever(
                origin=ORIGIN,
                skip_utf8_validation=True,
            )
        except Exception as e:
            logger.error(f"Stream pipeline error: {e}")
            self.error = str(e)
        finally:
            self.running = False


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    pipeline: Optional[StreamPipeline] = None

    try:
        config_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        config = json.loads(config_raw)

        session_id = config.get("session", "")
        camera_id = config.get("camera", "")
        model_name = config.get("model", "")
        detect = bool(config.get("detect", False))

        if not session_id or session_id not in ipeye_sessions:
            await websocket.send_text(json.dumps({"error": "Invalid session"}))
            await websocket.close()
            return

        if not camera_id:
            await websocket.send_text(json.dumps({"error": "No camera specified"}))
            await websocket.close()
            return

        client = ipeye_sessions[session_id]
        pipeline = StreamPipeline(client, camera_id, model_name, detect)
        pipeline.start()

        await websocket.send_text(json.dumps({"status": "connecting"}))

        while pipeline.running:
            if pipeline.error:
                await websocket.send_text(json.dumps({"error": pipeline.error}))
                break

            if pipeline.frame_queue:
                frame_data = pipeline.frame_queue.popleft()
                await websocket.send_bytes(frame_data)
            else:
                await asyncio.sleep(0.033)

    except WebSocketDisconnect:
        logger.info("Stream client disconnected")
    except asyncio.TimeoutError:
        logger.warning("Stream client config timeout")
    except Exception as e:
        logger.error(f"Stream WS error: {e}")
        try:
            await websocket.send_text(json.dumps({"error": str(e)}))
        except Exception:
            pass
    finally:
        if pipeline:
            pipeline.stop()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
