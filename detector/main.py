"""
Video Proxy Service — FastAPI
Connects to IPeye cameras via WebSocket, decodes fMP4 with FFmpeg,
streams JPEG frames to web clients.
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

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("proxy")

app = FastAPI(title="Video Proxy Service")

JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "5"))
MAX_FPS = int(os.environ.get("MAX_FPS", "15"))

ORIGIN = "https://www.ipeye.ru"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 YaBrowser/26.4.0.0 Safari/537.36"
)

VALID_BOXES = {b"ftyp", b"styp", b"moov", b"moof", b"mdat",
               b"sidx", b"free", b"skip", b"mfra"}


# ── IPeye Client ──

class IpeyeClient:
    BASE_URL = "https://www.ipeye.ru/ipeye_service/index.php"
    SITE_URL = "https://www.ipeye.ru"

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
            if resp.status_code != 200:
                return False

            self.session.get(
                f"{self.BASE_URL}?route=page_index",
                headers={"Accept": "text/html", "Referer": self.SITE_URL + "/"},
                timeout=15,
            )

            self.logged_in = True
            return True
        except Exception as e:
            logger.error(f"Login error: {e}")
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
                })
            logger.info(f"Found {len(cameras)} cameras")
            return cameras
        except Exception as e:
            logger.error(f"get_cameras error: {e}")
            return []

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
            if resp.status_code == 200:
                data = resp.json()
                server = data.get("server", "")
                return server if server else None
        except Exception as e:
            logger.error(f"authorize error: {e}")
        return None


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

    session_id = str(uuid.uuid4())
    ipeye_sessions[session_id] = client

    return {"session": session_id, "cameras": cameras}


# ── Stream Pipeline ──

class StreamPipeline:
    def __init__(self, ipeye_client: IpeyeClient, device_id: str):
        self.client = ipeye_client
        self.device_id = device_id
        self.running = False
        self.frame_queue: deque = deque(maxlen=2)
        self.error: Optional[str] = None

        self._ws_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._ws = None
        self._ffmpeg_stdin = None

        self._init_buf = b""
        self._got_moov = False
        self._ffmpeg_started = False
        self._pending_chunks: list[bytes] = []

    def start(self):
        self.running = True
        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()

    def stop(self):
        self.running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
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

    def _start_ffmpeg(self, init_data: bytes):
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+discardcorrupt+nobuffer",
            "-flags", "low_delay",
            "-analyzeduration", "3000000",
            "-probesize", "5000000",
            "-f", "mp4", "-i", "pipe:0",
            "-f", "image2pipe", "-c:v", "mjpeg",
            "-q:v", str(JPEG_QUALITY),
            "-r", str(MAX_FPS),
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
        buf = b""
        stdout = self._ffmpeg_proc.stdout

        while self.running:
            chunk = stdout.read(8192)
            if not chunk:
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is not None:
                    logger.info("FFmpeg exited")
                    break
                time.sleep(0.005)
                continue

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
                self.frame_queue.append(buf[soi : eoi + 2])
                buf = buf[eoi + 2 :]

    def _log_stderr(self):
        if not self._ffmpeg_proc or not self._ffmpeg_proc.stderr:
            return
        for line in self._ffmpeg_proc.stderr:
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.debug(f"ffmpeg: {text}")

    def _run_ws(self):
        import websocket as ws_lib

        try:
            ws_server = self.client.authorize_stream(self.device_id)
            if not ws_server:
                self.error = "Failed to authorize stream"
                self.running = False
                return

            ws_url = f"wss://{ws_server}/ws/mp4/live?name={self.device_id}"
            logger.info(f"Connecting to camera WS: {ws_url}")

            msg_count = 0

            def on_open(ws_conn):
                logger.info(f"Camera WS connected to {ws_server}")

            def on_message(ws_conn, message):
                nonlocal msg_count

                if not self.running:
                    ws_conn.close()
                    return
                if not isinstance(message, bytes):
                    return

                msg_count += 1
                if len(message) < 8:
                    return

                box_type = message[4:8]

                if msg_count <= 3:
                    logger.info(f"WS msg#{msg_count}: {len(message)} bytes, box={box_type}")

                if box_type not in VALID_BOXES:
                    return

                if not self._got_moov:
                    if box_type == b"ftyp":
                        self._init_buf = message
                        if b"moov" in message:
                            self._got_moov = True
                            threading.Thread(
                                target=self._start_ffmpeg,
                                args=(self._init_buf,),
                                daemon=True,
                            ).start()
                        return
                    elif box_type == b"moov":
                        self._init_buf += message
                        self._got_moov = True
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
                logger.info(f"Camera WS closed: code={code}")
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
            logger.error(f"Pipeline error: {e}")
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

        if not session_id or session_id not in ipeye_sessions:
            await websocket.send_text(json.dumps({"error": "Invalid session"}))
            await websocket.close()
            return

        if not camera_id:
            await websocket.send_text(json.dumps({"error": "No camera specified"}))
            await websocket.close()
            return

        client = ipeye_sessions[session_id]
        pipeline = StreamPipeline(client, camera_id)
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
        pass
    except asyncio.TimeoutError:
        logger.warning("Client config timeout")
    except Exception as e:
        logger.error(f"Stream error: {e}")
    finally:
        if pipeline:
            pipeline.stop()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
