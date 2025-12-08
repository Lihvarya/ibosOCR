import cv2
import numpy as np
from paddleocr import PaddleOCR
import webbrowser
import urllib.parse
import os
import sys
import re
import time
import json
import tkinter as tk
from mss import mss
import keyboard
import threading
import http.server
import socketserver
import signal
import queue
import select

# --- Configuration ---
OCR_LANG = 'ch'
USE_GPU = False  # 如果有N卡并装了paddle-gpu，改为True
CONFIG_FILE = 'ocr_config.json'
OCR_INTERVAL_SECONDS = 0.5  # 缩短检测间隔，因为现在有画面变化检测，反应可以更快

# --- Hotkeys ---
TOGGLE_OCR_HOTKEY = 'ctrl+alt+o'
RESELECT_HOTKEY = 'ctrl+alt+r'
QUIT_HOTKEY = 'ctrl+alt+q'

# --- Server Configuration ---
SERVER_PORT = 8088
SERVER_ADDRESS = "localhost"
SSE_ENDPOINT = "/ocr-events"

# --- Global variables ---
capture_region_coords = None
ocr_instance = None
httpd = None
server_thread = None
running = True
ocr_active = False
last_cleaned_text = ""
last_processed_image_hash = None  # 用于检测画面变化
sse_clients = []  # 存储 (client_address, queue)
sse_lock = threading.Lock()  # 线程锁，保护 sse_clients


# --- Config Management ---
def load_config():
    global capture_region_coords
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            if isinstance(config.get('region'), dict):
                capture_region_coords = config['region']
                print(f"已加载截图区域: {capture_region_coords}")
                return True
        except Exception as e:
            print(f"加载配置失败: {e}")
    return False


def save_config(region):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'region': region}, f, indent=4)
        print(f"已保存截图区域: {region}")
    except Exception as e:
        print(f"保存配置失败: {e}")


# --- GUI Region Selection ---
def select_region_gui():
    print("\n请在屏幕上框选识别区域...")
    root = tk.Tk()
    root.attributes("-alpha", 0.3)
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    canvas = tk.Canvas(root, cursor="cross", bg='gray')
    canvas.pack(fill=tk.BOTH, expand=tk.YES)

    start_x, start_y, rect_id, selected_region = None, None, None, None

    def on_mouse_down(e):
        nonlocal start_x, start_y
        start_x, start_y = e.x, e.y
        if rect_id: canvas.delete(rect_id)

    def on_mouse_drag(e):
        nonlocal rect_id
        if start_x:
            if rect_id: canvas.delete(rect_id)
            rect_id = canvas.create_rectangle(start_x, start_y, e.x, e.y, outline='red', width=2)

    def on_mouse_up(e):
        nonlocal selected_region
        if start_x:
            x1, y1 = min(start_x, e.x), min(start_y, e.y)
            x2, y2 = max(start_x, e.x), max(start_y, e.y)
            if x2 - x1 > 10 and y2 - y1 > 10:
                selected_region = {'top': y1, 'left': x1, 'width': x2 - x1, 'height': y2 - y1}
        root.quit()

    canvas.bind("<ButtonPress-1>", on_mouse_down)
    canvas.bind("<B1-Motion>", on_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_mouse_up)

    # 添加退出绑定的Esc键
    root.bind("<Escape>", lambda e: root.quit())

    root.mainloop()
    try:
        root.destroy()
    except:
        pass

    if selected_region:
        print(f"区域已选择: {selected_region}")
        return selected_region
    print("区域选择取消。")
    return None


# --- OCR Utilities ---
def clean_ocr_text(text):
    if not text: return ""
    # 保留中文、英文、数字
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text)


def preprocess_image(img_np):
    try:
        gray = cv2.cvtColor(img_np, cv2.COLOR_BGRA2GRAY)
        # 简单的自适应二值化增强文字
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 5)
    except:
        return None


def images_are_similar(img1, img2):
    """简单的像素差异比较，如果变化很小则无需OCR"""
    if img1 is None or img2 is None: return False
    if img1.shape != img2.shape: return False
    try:
        diff = cv2.absdiff(img1, img2)
        non_zero_count = np.count_nonzero(diff)
        total_pixels = img1.size
        # 如果差异像素少于 1%，认为画面静止
        return (non_zero_count / total_pixels) < 0.01
    except:
        return False


# --- HTTP Request Handler ---
class SSEHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self.directory = directory or os.getcwd()
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        pass  # 禁用烦人的 HTTP 日志输出

    def do_GET(self):
        if self.path == SSE_ENDPOINT:
            self.handle_sse()
        else:
            super().do_GET()

    def handle_sse(self):
        global sse_clients
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        client_queue = queue.Queue()
        client_id = self.client_address
        print(f"SSE 客户端已连接: {client_id}")

        with sse_lock:
            sse_clients.append((client_id, client_queue))

        try:
            while running:
                # 使用 select 监听 socket 是否关闭
                r, _, _ = select.select([self.rfile], [], [], 0.1)
                if r:
                    # 客户端关闭连接
                    break

                try:
                    # 获取消息，超时以便检查 running 状态
                    msg = client_queue.get(timeout=0.1)
                    data = f"data: {msg}\n\n"
                    self.wfile.write(data.encode('utf-8'))
                    self.wfile.flush()
                    client_queue.task_done()
                except queue.Empty:
                    continue
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            print(f"SSE 客户端断开: {client_id}")
            with sse_lock:
                # 安全移除
                sse_clients = [c for c in sse_clients if c[0] != client_id]


# --- Server Management ---
class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True  # 允许主线程退出时杀死子线程


def start_web_server(port, directory):
    global httpd, server_thread
    try:
        from functools import partial
        handler = partial(SSEHandler, directory=directory)
        httpd = ThreadingHTTPServer((SERVER_ADDRESS, port), handler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        print(f"服务器运行中: http://{SERVER_ADDRESS}:{port}/")
        return True
    except Exception as e:
        print(f"启动服务器失败 (端口 {port} 可能被占用): {e}")
        return False


def broadcast_message(message):
    """向所有连接的 SSE 客户端发送消息"""
    with sse_lock:
        if not sse_clients: return
        for _, q in sse_clients:
            q.put(message)


# --- Logic Core ---
def perform_ocr_cycle(sct):
    global last_cleaned_text, last_processed_image_hash

    if not capture_region_coords or not ocr_instance: return

    try:
        # 获取截图 (BGR)
        sct_img = sct.grab(capture_region_coords)
        img_np = np.array(sct_img)

        # 预处理
        processed_image = preprocess_image(img_np)
        if processed_image is None: return

        # === 优化: 检查画面是否变化 ===
        if images_are_similar(processed_image, last_processed_image_hash):
            return  # 画面无变化，跳过 OCR

        last_processed_image_hash = processed_image.copy()  # 更新缓存

        # 执行 OCR
        result = ocr_instance.ocr(processed_image, cls=False)
        if not result or not result[0]: return

        # 提取文本
        lines = [line[1][0] for res_list in result for line in res_list]
        raw_text = "".join(lines).strip()
        cleaned_text = clean_ocr_text(raw_text)

        # 只有文本有意义且发生变化时才发送
        if cleaned_text and len(cleaned_text) > 1 and cleaned_text != last_cleaned_text:
            timestamp = time.strftime('%H:%M:%S')
            print(f"[{timestamp}] 识别到新内容: {cleaned_text}")
            last_cleaned_text = cleaned_text
            broadcast_message(cleaned_text)

    except Exception as e:
        print(f"OCR 循环错误: {e}")


# --- Hotkey Callbacks ---
def toggle_ocr():
    global ocr_active, last_cleaned_text, last_processed_image_hash
    ocr_active = not ocr_active
    if ocr_active:
        last_cleaned_text = "RESET"  # 强制刷新
        last_processed_image_hash = None
        print(f"\n>>> OCR 已开启 (间隔: {OCR_INTERVAL_SECONDS}s)")
    else:
        print("\n<<< OCR 已暂停")


def request_reselect():
    global capture_region_coords, ocr_active
    was_active = ocr_active
    ocr_active = False  # 暂停
    print("\n重新选择区域...")
    new_region = select_region_gui()
    if new_region:
        capture_region_coords = new_region
        save_config(new_region)
    if was_active:
        toggle_ocr()  # 恢复之前的状态


def request_quit():
    global running
    print("\n正在退出...")
    running = False


# --- Main ---
if __name__ == "__main__":
    # 处理 Ctrl+C
    signal.signal(signal.SIGINT, lambda s, f: request_quit())

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)  # 确保工作目录正确

    # 检查依赖文件
    if not os.path.exists('search_questions.html') or not os.path.exists('combined_questions_data.json'):
        print("错误: 缺少 html 或 json 数据文件。")
        sys.exit(1)

    # 启动服务器
    if not start_web_server(SERVER_PORT, script_dir):
        sys.exit(1)

    # 加载配置
    if not load_config():
        new_region = select_region_gui()
        if new_region:
            capture_region_coords = new_region
            save_config(new_region)
        else:
            sys.exit(1)

    # 初始化 OCR
    print("正在初始化 PaddleOCR (首次运行可能较慢)...")
    try:
        ocr_instance = PaddleOCR(use_angle_cls=False, lang=OCR_LANG, use_gpu=USE_GPU, show_log=False)
    except Exception as e:
        print(f"OCR 初始化失败: {e}")
        sys.exit(1)

    # 注册热键
    keyboard.add_hotkey(TOGGLE_OCR_HOTKEY, toggle_ocr)
    keyboard.add_hotkey(RESELECT_HOTKEY, request_reselect)
    keyboard.add_hotkey(QUIT_HOTKEY, request_quit)

    print(f"\n=== 就绪 ===")
    print(f"控制: {TOGGLE_OCR_HOTKEY} (开关) | {RESELECT_HOTKEY} (重选) | {QUIT_HOTKEY} (退出)")

    # 打开浏览器
    url = f"http://{SERVER_ADDRESS}:{SERVER_PORT}/search_questions.html"
    threading.Timer(1.0, lambda: webbrowser.open_new_tab(url)).start()

    # 主循环
    with mss() as sct:  # 初始化一次 MSS
        while running:
            start_time = time.time()
            if ocr_active:
                perform_ocr_cycle(sct)

            # 智能休眠
            elapsed = time.time() - start_time
            sleep_time = max(0.05, OCR_INTERVAL_SECONDS - elapsed)
            time.sleep(sleep_time)

    # 清理
    if httpd: httpd.shutdown()
    print("程序已结束。")