"""
Video Detector Service — FastAPI
Connects to IPeye cameras via WebSocket, decodes fMP4 with FFmpeg,
optionally runs YOLO detection, streams JPEG frames to web clients.
"""

import asyncio
import json
import logging
import os
import struct
import subprocess
import threading
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

VALID_BOXES = {b"ftyp", b"styp", b"moov", b"moof", b"mdat", b"sidx", b"free", b"skip", b"mfra"}

COLORS = [
    (46, 232, 183), (255, 107, 107), (78, 205, 196), (255, 230, 109),
    (162, 155, 254), (253, 150, 68), (0, 210, 211), (232, 67, 147),
]


# ── IPeye Client ──

class IpeyeClient:
    BASE_URL = "https://www.ipeye.ru/ipeye_service/index.php"
    SITE_URL = "https://www.ipeye.ru"
    API_URL = "https://api.ipeye.ru"

    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.UA})
        self.cameras: list[dict] = []

    def login(self, login: str, password: str) -> bool:
        try:
            self.session.get(self.SITE_URL, timeout=10)

            resp = self.session.post(
                f"{self.BASE_URL}?route=proc_login",
                data={
                    "login": login,
                    "pass": password,
                    "captcha": "false",
                    "service_url_relative": "ipeye_service/",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.SITE_URL}/ipeye_service/?route=page_login",
                    "Origin": self.SITE_URL,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return False

            resp = self.session.get(f"{self.BASE_URL}?route=page_index", timeout=10)
            return resp.status_code == 200
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
                    "server": item.get("storage_server", ""),
                })
            self.cameras = cameras
            return cameras
        except Exception as e:
            logger.error(f"IPeye get_cameras error: {e}")
            return []

    def get_stream_info(self, device_ids: list[str]) -> dict:
        try:
            resp = self.session.post(
                f"{self.API_URL}/v1/stream/status_array_full",
                data={"streams": json.dumps(device_ids)},
                headers={
                    "Accept": "*/*",
                    "Referer": f"{self.BASE_URL}?route=page_index",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": self.SITE_URL,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"IPeye stream_info error: {e}")
        return {}

    def authorize_stream(self, device_id: str) -> dict:
        try:
            resp = self.session.get(
                f"{self.BASE_URL}?route=page_play_ajax&new_websocket&devid={device_id}",
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"IPeye authorize error: {e}")
        return {}


# ── YOLO Detector ──

class YoloDetector:
    def __init__(self, model_path: str):
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.model.fuse()

        import torch
        if torch.backends.mps.is_available():
            self.device = "mps"
        elif torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"
        logger.info(f"YOLO device: {self.device}")

        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self.model.predict(dummy, imgsz=YOLO_IMGSZ, conf=YOLO_CONF, device=self.device, verbose=False)

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
            self._event.wait(timeout=1.0)
            self._event.clear()
            if not self._queue:
                continue
            frame = self._queue.pop()
            try:
                results = self.model.predict(
                    frame, imgsz=YOLO_IMGSZ, conf=YOLO_CONF,
                    device=self.device, verbose=False,
                )
                dets = []
                for r in results:
                    for box in r.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        dets.append({
                            "bbox": [float(x1), float(y1), float(x2), float(y2)],
                            "conf": float(box.conf[0]),
                            "cls": int(box.cls[0]),
                            "label": r.names[int(box.cls[0])],
                        })
                with self._lock:
                    self._results = dets
            except Exception as e:
                logger.error(f"YOLO inference error: {e}")


def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    for det in detections:
        x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
        color = COLORS[det["cls"] % len(COLORS)]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f'{det["label"]} {det["conf"]:.0%}'
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(frame, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return frame


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
        status = await asyncio.to_thread(client.get_stream_info, device_ids)
        if isinstance(status, dict):
            for cam in cameras:
                info = status.get(cam["id"], {})
                cam["online"] = info.get("status") == "online" if info else False
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


# ── Stream Pipeline ──

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
        if self.detector:
            self.detector.stop()
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.stdin.close()
            except Exception:
                pass
            try:
                self._ffmpeg_proc.kill()
            except Exception:
                pass

    def _find_model(self, name: str) -> str:
        p = os.path.join(MODELS_DIR, name)
        if os.path.isfile(p):
            return p
        return name

    def _run_ws(self):
        import websocket as ws_lib

        try:
            auth = self.client.authorize_stream(self.device_id)
            ws_server = auth.get("server", "")
            stream_name = auth.get("stream_name", self.device_id)

            if not ws_server:
                self.error = "Failed to authorize stream"
                self.running = False
                return

            status = self.client.get_stream_info([self.device_id])
            if isinstance(status, dict) and self.device_id in status:
                info = status[self.device_id]
                self.width = int(info.get("Width", 1280)) or 1280
                self.height = int(info.get("Height", 720)) or 720

            self._start_ffmpeg()

            ws_url = f"wss://{ws_server}/ws/mp4/live?name={stream_name}"
            logger.info(f"Connecting to camera WS: {ws_url} ({self.width}x{self.height})")

            init_buf = bytearray()
            has_ftyp = False
            has_moov = False
            init_done = False

            def on_message(ws_conn, message):
                nonlocal init_buf, has_ftyp, has_moov, init_done

                if not self.running:
                    ws_conn.close()
                    return
                if isinstance(message, str):
                    return

                data = bytes(message)

                if not init_done:
                    init_buf.extend(data)
                    pos = 0
                    while pos + 8 <= len(init_buf):
                        size = struct.unpack(">I", init_buf[pos:pos + 4])[0]
                        box_type = init_buf[pos + 4:pos + 8]
                        if size < 8 or pos + size > len(init_buf):
                            break
                        if box_type == b"ftyp":
                            has_ftyp = True
                        elif box_type == b"moov":
                            has_moov = True
                        pos += size

                    if has_ftyp and has_moov:
                        init_done = True
                        self._write_ffmpeg(bytes(init_buf))
                        init_buf = bytearray()
                else:
                    self._write_ffmpeg(data)

            def on_error(ws_conn, error):
                logger.error(f"Camera WS error: {error}")
                self.error = str(error)

            def on_close(ws_conn, code, msg):
                logger.info(f"Camera WS closed: {code}")
                self.running = False

            ws_conn = ws_lib.WebSocketApp(
                ws_url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                header={
                    "Origin": "https://www.ipeye.ru",
                    "User-Agent": IpeyeClient.UA,
                },
            )
            ws_conn.run_forever()
        except Exception as e:
            logger.error(f"Stream pipeline error: {e}")
            self.error = str(e)
        finally:
            self.running = False

    def _start_ffmpeg(self):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "mp4", "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-vsync", "drop", "pipe:1",
        ]
        self._ffmpeg_proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._reader_thread.start()
        threading.Thread(target=self._log_stderr, daemon=True).start()

    def _write_ffmpeg(self, data: bytes):
        try:
            if self._ffmpeg_proc and self._ffmpeg_proc.stdin and not self._ffmpeg_proc.stdin.closed:
                self._ffmpeg_proc.stdin.write(data)
                self._ffmpeg_proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_frames(self):
        frame_size = self.width * self.height * 3
        buf = bytearray()

        while self.running and self._ffmpeg_proc:
            try:
                chunk = self._ffmpeg_proc.stdout.read(min(frame_size - len(buf), 65536))
                if not chunk:
                    break
                buf.extend(chunk)

                while len(buf) >= frame_size:
                    frame_data = bytes(buf[:frame_size])
                    buf = buf[frame_size:]

                    frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(
                        (self.height, self.width, 3)
                    )

                    if self.detector:
                        self.detector.submit(frame.copy())
                        dets = self.detector.get_results()
                        if dets:
                            frame = draw_detections(frame.copy(), dets)

                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self.frame_queue.append(jpeg.tobytes())
            except Exception as e:
                logger.error(f"Frame read error: {e}")
                break

        logger.info("Frame reader stopped")

    def _log_stderr(self):
        while self.running and self._ffmpeg_proc:
            try:
                line = self._ffmpeg_proc.stderr.readline()
                if not line:
                    break
                logger.debug(f"ffmpeg: {line.decode(errors='replace').strip()}")
            except Exception:
                break


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
