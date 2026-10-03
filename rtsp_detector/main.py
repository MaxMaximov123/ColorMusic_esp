"""
RTSP Video Player + YOLO Object Detection

Прямое подключение к камерам по RTSP через FFmpeg,
детекция объектов YOLO26 с ускорением Apple MPS.

Камеры настраиваются в .env файле проекта:
    RTSP_CAM1_URL=rtsp://user:pass@host:554/path
    RTSP_CAM1_NAME=Камера 1
    RTSP_CAM2_URL=rtsp://...
    RTSP_CAM2_NAME=Камера 2

Зависимости:
    pip install opencv-python numpy Pillow python-dotenv
    pip install ultralytics torch torchvision

Также нужен FFmpeg в PATH.
"""

import threading
import subprocess
import time
import os
import sys
import signal
import tkinter as tk
from tkinter import ttk, messagebox
from collections import deque

import numpy as np
import cv2
from PIL import Image, ImageTk

# ── Пути ──────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── YOLO ──────────────────────────────────────────────────────────
YOLO_MODEL_PRIORITY = ["yolo26x.pt", "yolo26l.pt", "yolo26m.pt",
                        "yolo26s.pt", "yolo26n.pt",
                        "yolo11x.pt", "yolo11l.pt", "yolo11m.pt",
                        "yolo11s.pt", "yolo11n.pt"]
YOLO_AUTO_DOWNLOAD = "yolo26x.pt"
YOLO_IMGSZ = 640
YOLO_CONF = 0.1


def _find_yolo_model() -> str | None:
    search_dirs = [
        SCRIPT_DIR,
        PROJECT_ROOT,
        os.path.join(os.path.dirname(PROJECT_ROOT), "RTSP_YOLO_detector"),
    ]
    for search_dir in search_dirs:
        for name in YOLO_MODEL_PRIORITY:
            path = os.path.join(search_dir, name)
            if os.path.isfile(path):
                return path
    # No local model found — return name for auto-download
    return YOLO_AUTO_DOWNLOAD


def _load_cameras_from_env() -> list[dict]:
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(PROJECT_ROOT, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass

    cameras = []
    for i in range(1, 20):
        url = os.environ.get(f"RTSP_CAM{i}_URL")
        if not url:
            continue
        name = os.environ.get(f"RTSP_CAM{i}_NAME", f"Камера {i}")
        cameras.append({"id": i, "name": name, "url": url})
    return cameras


# ═══════════════════════════════════════════════════════════════════
#  YOLO Detector — асинхронный inference в отдельном потоке
# ═══════════════════════════════════════════════════════════════════

class YoloDetector:
    def __init__(self):
        self.model = None
        self.device = "cpu"
        self.model_name = ""
        self.running = False
        self.enabled = False
        self._frame_slot = deque(maxlen=1)
        self._new_frame = threading.Event()
        self._det_lock = threading.Lock()
        self._detections: list[dict] = []
        self._det_count = 0
        self._det_fps = 0.0
        self._thread = None

    def load_model(self, model_path: str) -> bool:
        try:
            from ultralytics import YOLO
            import torch

            self.model_name = os.path.basename(model_path)
            # If path doesn't exist, YOLO() auto-downloads by name
            if not os.path.isfile(model_path):
                print(f"[YOLO] Скачивание {self.model_name}...")
                model_path = self.model_name

            print(f"[YOLO] Загрузка модели {self.model_name}...")

            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self.device = "mps"
                print("[YOLO] Apple MPS (Metal Performance Shaders)")
            else:
                self.device = "cpu"
                print("[YOLO] CPU mode")

            self.model = YOLO(model_path)
            self.model.fuse()

            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            self.model.predict(
                dummy, verbose=False, imgsz=YOLO_IMGSZ,
                device=self.device, half=(self.device == "mps"),
            )
            print(f"[YOLO] Модель готова, device={self.device}")
            return True
        except ImportError as e:
            print(f"[YOLO] Библиотека не установлена: {e}")
            return False
        except Exception as e:
            print(f"[YOLO] Ошибка загрузки: {e}")
            return False

    def start(self):
        if self.model is None:
            return
        self.running = True
        self.enabled = True
        self._thread = threading.Thread(target=self._inference_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._new_frame.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._frame_slot.clear()
        with self._det_lock:
            self._detections.clear()
            self._det_count = 0
            self._det_fps = 0.0

    def submit_frame(self, frame_bgr: np.ndarray):
        if not self.enabled or not self.running:
            return
        self._frame_slot.append(frame_bgr)
        self._new_frame.set()

    def get_detections(self) -> tuple[list[dict], int, float]:
        with self._det_lock:
            return list(self._detections), self._det_count, self._det_fps

    def _inference_loop(self):
        fps_window = deque(maxlen=30)
        while self.running:
            self._new_frame.wait(timeout=0.5)
            self._new_frame.clear()
            if not self.running or not self.enabled:
                continue
            try:
                frame = self._frame_slot.pop()
            except IndexError:
                continue

            t0 = time.perf_counter()
            try:
                results = self.model.predict(
                    frame, verbose=False, stream=True,
                    imgsz=YOLO_IMGSZ, conf=YOLO_CONF,
                    device=self.device, half=(self.device == "mps"),
                )
                result = next(results)
                detections = []
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    label = self.model.names[cls]
                    detections.append({
                        "bbox": (x1, y1, x2, y2),
                        "conf": conf, "cls": cls, "label": label,
                    })
                dt = time.perf_counter() - t0
                fps_window.append(dt)
                avg_fps = len(fps_window) / sum(fps_window) if fps_window else 0
                with self._det_lock:
                    self._detections = detections
                    self._det_count = len(detections)
                    self._det_fps = avg_fps
            except StopIteration:
                pass
            except Exception as e:
                print(f"[YOLO] Inference error: {e}")
                time.sleep(0.1)


# ═══════════════════════════════════════════════════════════════════
#  Отрисовка детекций
# ═══════════════════════════════════════════════════════════════════

def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    if not detections:
        return frame
    out = frame.copy()
    colors = [
        (76, 175, 80), (33, 150, 243), (255, 152, 0), (156, 39, 176),
        (244, 67, 54), (0, 188, 212), (255, 235, 59), (121, 85, 72),
    ]
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        conf = det["conf"]
        label = det["label"]
        color = colors[det["cls"] % len(colors)]
        text = f"{label} {conf:.0%}"
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        (tw, th), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, y1 - th - baseline - 6),
                      (x1 + tw + 6, y1), color, -1)
        cv2.putText(out, text, (x1 + 3, y1 - baseline - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                    cv2.LINE_AA)
    return out


# ═══════════════════════════════════════════════════════════════════
#  RTSP Stream Player — прямое подключение через FFmpeg
# ═══════════════════════════════════════════════════════════════════

def _probe_resolution(url: str, timeout_s: int = 10) -> tuple[int, int] | None:
    """Определяет разрешение RTSP потока через ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-rtsp_transport", "tcp",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                url,
            ],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception as e:
        print(f"[ffprobe] {e}")
    return None


class RtspPlayer:
    """
    Подключается к камере по RTSP через FFmpeg.
    FFmpeg декодирует поток и выдаёт raw BGR24 кадры в stdout.
    Автоматически переподключается при обрыве.
    Определяет разрешение потока автоматически.
    """

    MAX_RECONNECT_DELAY = 15
    CONNECT_TIMEOUT_US = 10_000_000  # 10s

    def __init__(self, camera: dict, *,
                 on_frame=None, on_status=None):
        self.camera = camera
        self.on_frame = on_frame
        self.on_status = on_status
        self.width = 0
        self.height = 0
        self.running = False
        self.frame_count = 0
        self.byte_count = 0
        self._process = None
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        delay = 1
        while self.running:
            ok = self._run_once()
            if not self.running:
                break
            if ok:
                delay = 1
            else:
                delay = min(delay * 2, self.MAX_RECONNECT_DELAY)
            self._emit_status(
                f"Переподключение к «{self.camera['name']}» через {delay}с..."
            )
            for _ in range(delay * 10):
                if not self.running:
                    return
                time.sleep(0.1)

    def _run_once(self) -> bool:
        url = self.camera["url"]
        name = self.camera["name"]

        # Auto-detect resolution
        if self.width == 0:
            self._emit_status(f"Определение разрешения «{name}»...")
            res = _probe_resolution(url)
            if res:
                self.width, self.height = res
                print(f"[rtsp] {name}: {self.width}x{self.height}")
            else:
                self._emit_status(f"Не удалось определить разрешение «{name}»")
                return False

        frame_size = self.width * self.height * 3

        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-timeout", str(self.CONNECT_TIMEOUT_US),
            "-fflags", "+nobuffer",
            "-flags", "low_delay",
            "-i", url,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-an", "-sn",
            "-fps_mode", "cfr",
            "-r", "25",
            "pipe:1",
        ]

        self._emit_status(f"Подключение к «{name}» ({self.width}x{self.height})...")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_size * 2,
            )
        except FileNotFoundError:
            self._emit_status("Ошибка: FFmpeg не найден в PATH")
            self.running = False
            return False
        except Exception as e:
            self._emit_status(f"Ошибка запуска FFmpeg: {e}")
            return False

        threading.Thread(target=self._drain_stderr, daemon=True).start()

        self._emit_status(f"Ожидание потока «{name}»...")
        got_frames = False

        while self.running:
            data = self._read_exact(frame_size)
            if data is None:
                if self.running:
                    self._emit_status(f"Поток «{name}» прервался")
                break

            if not got_frames:
                got_frames = True
                self._emit_status(f"LIVE — «{name}» ({self.width}x{self.height})")

            self.frame_count += 1
            self.byte_count += len(data)

            frame = np.frombuffer(data, dtype=np.uint8).reshape(
                (self.height, self.width, 3)
            )

            if self.on_frame:
                self.on_frame(frame)

        self._kill_process()
        return got_frames

    def _read_exact(self, size: int) -> bytes | None:
        buf = bytearray()
        proc = self._process
        if not proc:
            return None
        while len(buf) < size:
            try:
                chunk = proc.stdout.read(size - len(buf))
            except (ValueError, OSError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _drain_stderr(self):
        proc = self._process
        if not proc or not proc.stderr:
            return
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if "Broken pipe" in text or "swscaler" in text:
                    continue
                print(f"[ffmpeg] {text}")
        except (ValueError, OSError):
            pass

    def _emit_status(self, text: str):
        if self.on_status:
            self.on_status(text)

    def stop(self):
        self.running = False
        self._kill_process()

    def _kill_process(self):
        proc = self._process
        if not proc:
            return
        self._process = None
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
#  Стили Tkinter
# ═══════════════════════════════════════════════════════════════════

def setup_styles():
    style = ttk.Style()
    style.theme_use("clam")

    style.configure("Green.TButton",
                     background="#4CAF50", foreground="white",
                     font=("Helvetica", 11, "bold"), padding=(12, 6))
    style.map("Green.TButton",
              background=[("active", "#388E3C"), ("disabled", "#555555")])

    style.configure("Red.TButton",
                     background="#f44336", foreground="white",
                     font=("Helvetica", 11, "bold"), padding=(12, 6))
    style.map("Red.TButton",
              background=[("active", "#C62828"), ("disabled", "#555555")])

    style.configure("Gray.TButton",
                     background="#616161", foreground="white",
                     font=("Helvetica", 11), padding=(10, 6))
    style.map("Gray.TButton",
              background=[("active", "#757575"), ("disabled", "#555555")])

    style.configure("YoloOff.TButton",
                     background="#455A64", foreground="white",
                     font=("Helvetica", 11, "bold"), padding=(12, 6))
    style.map("YoloOff.TButton",
              background=[("active", "#546E7A"), ("disabled", "#555555")])

    style.configure("YoloOn.TButton",
                     background="#2196F3", foreground="white",
                     font=("Helvetica", 11, "bold"), padding=(12, 6))
    style.map("YoloOn.TButton",
              background=[("active", "#1976D2")])

    style.configure("TCombobox", font=("Helvetica", 11))


# ═══════════════════════════════════════════════════════════════════
#  Приложение
# ═══════════════════════════════════════════════════════════════════

class App:
    CONN_DISCONNECTED = 0
    CONN_CONNECTING = 1
    CONN_LIVE = 2
    CONN_ERROR = 3

    STATUS_COLORS = {
        CONN_DISCONNECTED: "#888888",
        CONN_CONNECTING: "#FFC107",
        CONN_LIVE: "#4CAF50",
        CONN_ERROR: "#f44336",
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RTSP Video Player + YOLO")
        self.root.configure(bg="#1e1e1e")
        self.root.geometry("1320x820")
        self.root.minsize(640, 400)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        setup_styles()

        self.cameras = _load_cameras_from_env()
        self.player = None
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.status_text = tk.StringVar(value="Запуск...")
        self._photo = None
        self._conn_state = self.CONN_DISCONNECTED

        self.detector = YoloDetector()
        self._yolo_loaded = False
        self._yolo_loading = False

        self._build_ui()
        self._init_yolo_async()
        self.root.after(200, self._startup)

    # ── YOLO init ──────────────────────────────────────────────────

    def _init_yolo_async(self):
        model_path = _find_yolo_model()
        if not model_path:
            print("[YOLO] Модель не найдена")
            self.root.after(0, self._yolo_not_available)
            return

        self._yolo_loading = True
        self.root.after(0, lambda: self.btn_yolo.config(
            text="YOLO: загрузка...", state=tk.DISABLED))

        def _load():
            ok = self.detector.load_model(model_path)
            self.root.after(0, self._on_yolo_loaded, ok)

        threading.Thread(target=_load, daemon=True).start()

    def _on_yolo_loaded(self, success: bool):
        self._yolo_loading = False
        if success:
            self._yolo_loaded = True
            self.btn_yolo.config(
                text=f"YOLO: OFF ({self.detector.model_name})",
                style="YoloOff.TButton", state=tk.NORMAL,
            )
            self.status_text.set(
                f"YOLO {self.detector.model_name} [{self.detector.device.upper()}]"
            )
        else:
            self._yolo_not_available()

    def _yolo_not_available(self):
        self.btn_yolo.config(
            text="YOLO: N/A", state=tk.DISABLED,
            style="YoloOff.TButton",
        )

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        self.top_bar = tk.Frame(self.root, bg="#2d2d2d", padx=8, pady=6)
        self.top_bar.pack(fill=tk.X, side=tk.TOP)

        self.conn_indicator = tk.Canvas(
            self.top_bar, width=16, height=16,
            bg="#2d2d2d", highlightthickness=0,
        )
        self.conn_indicator.pack(side=tk.LEFT, padx=(0, 6))
        self._draw_indicator(self.CONN_DISCONNECTED)

        tk.Label(
            self.top_bar, text="Камера:", fg="white", bg="#2d2d2d",
            font=("Helvetica", 12),
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.stream_var = tk.StringVar()
        self.combo = ttk.Combobox(
            self.top_bar, textvariable=self.stream_var,
            values=[], state="disabled", width=40, font=("Helvetica", 11),
        )
        self.combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.btn_connect = ttk.Button(
            self.top_bar, text="Подключить", command=self._on_connect,
            style="Green.TButton", cursor="hand2", state=tk.DISABLED,
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_stop = ttk.Button(
            self.top_bar, text="Стоп", command=self._on_stop,
            style="Red.TButton", cursor="hand2", state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_yolo = ttk.Button(
            self.top_bar, text="YOLO: ...",
            command=self._on_toggle_yolo,
            style="YoloOff.TButton", cursor="hand2", state=tk.DISABLED,
        )
        self.btn_yolo.pack(side=tk.LEFT, padx=(0, 4))

        # Bottom status bar
        self.status_bar = tk.Frame(self.root, bg="#2d2d2d", padx=10, pady=4)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_left = tk.Label(
            self.status_bar, textvariable=self.status_text,
            fg="#aaaaaa", bg="#2d2d2d", font=("Helvetica", 11), anchor=tk.W,
        )
        self.status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.yolo_status_var = tk.StringVar(value="")
        self.status_right = tk.Label(
            self.status_bar, textvariable=self.yolo_status_var,
            fg="#2196F3", bg="#2d2d2d", font=("Helvetica", 11), anchor=tk.E,
        )
        self.status_right.pack(side=tk.RIGHT, padx=(10, 0))

        # Canvas — video area
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._draw_placeholder("Запуск...")

    # ── YOLO Toggle ────────────────────────────────────────────────

    def _on_toggle_yolo(self):
        if not self._yolo_loaded:
            return
        if self.detector.enabled:
            self.detector.enabled = False
            self.detector.stop()
            self.btn_yolo.config(
                text=f"YOLO: OFF ({self.detector.model_name})",
                style="YoloOff.TButton",
            )
            self.yolo_status_var.set("")
        else:
            self.detector.start()
            self.btn_yolo.config(
                text=f"YOLO: ON ({self.detector.model_name})",
                style="YoloOn.TButton",
            )

    # ── Drawing ────────────────────────────────────────────────────

    def _draw_indicator(self, state):
        self._conn_state = state
        color = self.STATUS_COLORS[state]
        self.conn_indicator.delete("all")
        self.conn_indicator.create_oval(2, 2, 14, 14, fill=color, outline=color)

    def _draw_placeholder(self, text="Выберите камеру и нажмите «Подключить»"):
        self.canvas.delete("all")
        cx = self.canvas.winfo_width() // 2 or 640
        cy = self.canvas.winfo_height() // 2 or 360
        self.canvas.create_text(
            cx, cy, text=text,
            fill="#666666", font=("Helvetica", 16), tag="placeholder",
        )

    def _on_canvas_resize(self, event):
        items = self.canvas.find_withtag("placeholder")
        if items:
            self.canvas.coords(items[0], event.width // 2, event.height // 2)

    # ── Startup ────────────────────────────────────────────────────

    def _startup(self):
        if not self.cameras:
            self.status_text.set("Камеры не найдены в .env")
            self._draw_placeholder(
                "Добавьте RTSP_CAM1_URL в .env файл"
            )
            return

        labels = [f"{c['name']}  (RTSP)" for c in self.cameras]
        self.combo.config(values=labels, state="readonly")
        self.btn_connect.config(state=tk.NORMAL)
        if labels:
            self.combo.current(0)
        self.status_text.set(f"Камер: {len(self.cameras)}. Выберите камеру.")
        self._draw_placeholder("Выберите камеру и нажмите «Подключить»")

    # ── Connect / Stop ─────────────────────────────────────────────

    def _on_connect(self):
        idx = self.combo.current()
        if idx < 0:
            self.status_text.set("Выберите камеру из списка")
            return

        self._stop_player()
        camera = self.cameras[idx]

        self._draw_placeholder(f"Подключение к «{camera['name']}»...")
        self.btn_connect.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.combo.config(state=tk.DISABLED)
        self._draw_indicator(self.CONN_CONNECTING)
        self.status_text.set(f"Подключение к «{camera['name']}»...")

        self.player = RtspPlayer(
            camera,
            on_frame=self._on_new_frame,
            on_status=lambda t: self.root.after(0, self._on_player_status, t),
        )
        self.player.start()
        self.root.after(100, self._update_canvas)

    def _on_player_status(self, text):
        self.status_text.set(text)
        if "LIVE" in text:
            self._draw_indicator(self.CONN_LIVE)
        elif "Переподключение" in text or "Подключение" in text or "Ожидание" in text:
            self._draw_indicator(self.CONN_CONNECTING)
        elif "Ошибка" in text or "прервался" in text:
            self._draw_indicator(self.CONN_ERROR)

    def _stop_player(self):
        if self.player:
            self.player.stop()
            self.player = None
        if self.detector.running:
            self.detector.stop()
        with self.frame_lock:
            self.current_frame = None

    def _on_stop(self):
        self._stop_player()
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.combo.config(state="readonly")
        self._draw_indicator(self.CONN_DISCONNECTED)
        self.status_text.set("Остановлено")
        self.yolo_status_var.set("")
        self._draw_placeholder()

    # ── Frame processing ───────────────────────────────────────────

    def _on_new_frame(self, frame_bgr: np.ndarray):
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        if self.detector.enabled and self.detector.running:
            self.detector.submit_frame(frame_bgr)
        with self.frame_lock:
            self.current_frame = frame_rgb

    def _update_canvas(self):
        if not self.player or not self.player.running:
            return

        # Schedule next update first so it keeps running during reconnect
        self.root.after(33, self._update_canvas)

        with self.frame_lock:
            frame = self.current_frame

        if frame is not None:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw > 1 and ch > 1:
                if self.detector.enabled:
                    dets, det_count, det_fps = self.detector.get_detections()
                    if dets:
                        frame = draw_detections(frame, dets)
                    self.yolo_status_var.set(
                        f"YOLO: {det_fps:.1f} fps | Объектов: {det_count}"
                    )
                else:
                    self.yolo_status_var.set("")

                h, w = frame.shape[:2]
                scale = min(cw / w, ch / h)
                nw, nh = int(w * scale), int(h * scale)
                resized = cv2.resize(frame, (nw, nh),
                                     interpolation=cv2.INTER_LINEAR)
                img = Image.fromarray(resized)
                self._photo = ImageTk.PhotoImage(image=img)
                self.canvas.delete("all")
                self.canvas.create_image(
                    cw // 2, ch // 2, anchor=tk.CENTER, image=self._photo,
                )
                if self._conn_state != self.CONN_LIVE:
                    self._draw_indicator(self.CONN_LIVE)

            if self.player:
                kb = self.player.byte_count // 1024
                self.status_text.set(
                    f"LIVE  |  {self.player.camera['name']}  |  "
                    f"Кадров: {self.player.frame_count}  |  "
                    f"Данных: {kb} KB"
                )

    # ── Close ──────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_player()
        self.detector.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
