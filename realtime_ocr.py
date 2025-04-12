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
import queue # Added for SSE message passing
import select # Added for non-blocking queue read in SSE handler

# --- Configuration ---
OCR_LANG = 'ch'
USE_GPU = False
CONFIG_FILE = 'ocr_config.json'
# --- Hotkeys ---
TOGGLE_OCR_HOTKEY = 'ctrl+alt+o'
RESELECT_HOTKEY = 'ctrl+alt+r'
QUIT_HOTKEY = 'ctrl+alt+q'
# --- Server Configuration ---
SERVER_PORT = 8088
SERVER_ADDRESS = "localhost"
SSE_ENDPOINT = "/ocr-events" # Path for Server-Sent Events
# --- OCR Loop ---
OCR_INTERVAL_SECONDS = 1.0

# --- Global variables ---
capture_region_coords = None
ocr_instance = None
httpd = None
server_thread = None
running = True
ocr_active = False
last_cleaned_text = "" # Store last *successfully processed* cleaned text
sse_clients = [] # List to keep track of connected SSE client queues
sse_message_queue = queue.Queue() # Queue for Python OCR loop to send messages to SSE handlers

# --- Config Management (load_config, save_config - unchanged) ---
def load_config():
    global capture_region_coords
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: config = json.load(f)
            if isinstance(config.get('region'), dict) and all(k in config['region'] for k in ['top', 'left', 'width', 'height']):
                capture_region_coords = config['region']; print(f"Loaded region: {capture_region_coords}"); return True
            else: print(f"Invalid region format in {CONFIG_FILE}.")
        except Exception as e: print(f"Error loading config {CONFIG_FILE}: {e}")
    print("Config file not found/invalid.")
    return False

def save_config(region):
    try:
        with open(CONFIG_FILE, 'w') as f: json.dump({'region': region}, f, indent=4)
        print(f"Saved region: {region}")
    except Exception as e: print(f"Error saving config: {e}")


# --- Region Selection GUI (select_region_gui - unchanged) ---
def select_region_gui():
    print("\nPlease select capture region...")
    root = tk.Tk(); root.attributes("-alpha", 0.3); root.attributes("-fullscreen", True)
    root.wait_visibility(root); root.attributes("-topmost", True)
    canvas = tk.Canvas(root, cursor="cross", bg='gray'); canvas.pack(fill=tk.BOTH, expand=tk.YES)
    start_x, start_y, rect_id, selected_region = None, None, None, None
    def on_m_down(e): nonlocal start_x,start_y; start_x,start_y=e.x,e.y; canvas.delete(rect_id) if rect_id else None
    def on_m_drag(e):
        nonlocal rect_id
        if start_x is not None: canvas.delete(rect_id) if rect_id else None; rect_id=canvas.create_rectangle(start_x,start_y,e.x,e.y,outline='red',width=2)
    def on_m_up(e):
        nonlocal selected_region
        if start_x is not None:
            x1,y1=min(start_x,e.x),min(start_y,e.y); x2,y2=max(start_x,e.x),max(start_y,e.y)
            if x2-x1 > 10 and y2-y1 > 10: selected_region = {'top': y1, 'left': x1, 'width': x2-x1, 'height': y2-y1}
        root.quit()
    canvas.bind("<ButtonPress-1>",on_m_down); canvas.bind("<B1-Motion>",on_m_drag); canvas.bind("<ButtonRelease-1>",on_m_up)
    root.mainloop()
    try: root.destroy()
    except: pass
    if selected_region: print(f"Region selected: {selected_region}"); return selected_region
    else: print("Region selection cancelled/invalid."); return None

# --- Text Cleaning (clean_ocr_text - unchanged) ---
def clean_ocr_text(text):
    if not text: return ""
    cleaned_text = re.sub(r'[^\u4e00-\u9fffA-Za-z]', '', text)
    return cleaned_text

# --- Image Preprocessing (preprocess_screen_capture - unchanged) ---
def preprocess_screen_capture(img_np):
    try:
        gray = cv2.cvtColor(img_np, cv2.COLOR_BGRA2GRAY)
        processed_img = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 5)
        return processed_img
    except Exception as e: print(f"Preprocessing error: {e}"); return None


# --- Custom HTTP Handler with SSE ---
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    # Override constructor to specify directory
    def __init__(self, *args, directory=None, **kwargs):
        if directory is None:
            directory = os.getcwd()
        self.directory = directory
        super().__init__(*args, **kwargs)

    # Override translate_path to use the stored directory
    def translate_path(self, path):
        # Borrowed from standard library, but uses self.directory
        path = path.split('?',1)[0]
        path = path.split('#',1)[0]
        # Don't forget explicit trailing slash when normalizing. Issue17324
        trailing_slash = path.rstrip().endswith('/')
        try:
            path = urllib.parse.unquote(path, errors='surrogatepass')
        except UnicodeDecodeError:
            path = urllib.parse.unquote(path)
        path = os.path.normpath(path)
        words = path.split(os.sep)
        words = filter(None, words)
        path = self.directory # Use the instance directory
        for word in words:
            if os.path.dirname(word) or word in (os.curdir, os.pardir):
                continue # Ignore components that are not simple filenames
            path = os.path.join(path, word)
        if trailing_slash:
            path += '/'
        return path


    def do_GET(self):
        global sse_message_queue, running
        # Handle SSE connection request
        if self.path == SSE_ENDPOINT:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*') # Allow connections from file:/// or other origins if needed
            self.end_headers()

            print(f"SSE Client connected: {self.client_address}")
            local_queue = queue.Queue() # Each client gets its own queue copy mechanism (not ideal, but simple)
            sse_clients.append(local_queue) # Add this client's queue to the global list

            try:
                while running: # Keep connection open while script is running
                    try:
                        # Use select for non-blocking check on the socket
                        # This allows checking the `running` flag more frequently
                        ready_to_read, _, _ = select.select([self.rfile], [], [], 0.1)
                        if ready_to_read:
                            # If client sends data or closes connection, reading will succeed/fail
                            if not self.rfile.read(1): # Try reading 1 byte
                                print(f"SSE Client {self.client_address} closed connection (read check).")
                                break # Client likely disconnected

                        # Check our message queue (non-blocking)
                        message = None
                        while not sse_message_queue.empty(): # Drain the global queue into local
                            msg_item = sse_message_queue.get_nowait()
                            message = msg_item # Keep the last message if multiple came quickly
                            sse_message_queue.task_done()

                        if message is not None:
                            # Format and send SSE message: data: message_content\n\n
                            sse_data = f"data: {message}\n\n"
                            self.wfile.write(sse_data.encode('utf-8'))
                            self.wfile.flush() # Ensure data is sent immediately
                            # print(f"Sent to {self.client_address}: {message}") # Optional: Verbose logging

                    except queue.Empty:
                        time.sleep(0.1) # Wait briefly if queue is empty
                        continue
                    except BrokenPipeError:
                         print(f"SSE Client {self.client_address} disconnected (BrokenPipeError).")
                         break # Exit loop if client disconnects
                    except ConnectionResetError:
                         print(f"SSE Client {self.client_address} disconnected (ConnectionResetError).")
                         break
                    except Exception as e:
                         print(f"Error in SSE loop for {self.client_address}: {e}")
                         break # Exit on other errors too
            finally:
                print(f"SSE Client disconnected: {self.client_address}")
                # Clean up: Remove this client's queue (needs robust handling if queues are shared)
                # Simple approach for this example: clear the global list (not ideal for multiple clients)
                # A better approach would involve unique client IDs and removing specific queues.
                # For simplicity here, we just note disconnection. If using shared queues, proper removal is crucial.


        # Handle regular file requests (HTML, JSON, JS etc.)
        else:
            # Fallback to SimpleHTTPRequestHandler's file serving logic
            # The overridden translate_path ensures it uses the correct base directory
            super().do_GET()

# --- Web Server Functions ---
def start_web_server(port, directory):
    global httpd, server_thread
    try:
        # Use functools.partial to pass the directory to the handler constructor
        from functools import partial
        handler_with_directory = partial(RequestHandler, directory=directory)

        socketserver.TCPServer.allow_reuse_address = True
        httpd = socketserver.TCPServer((SERVER_ADDRESS, port), handler_with_directory)

        print(f"Serving HTTP+SSE on http://{SERVER_ADDRESS}:{port}/ from directory {directory}...")
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        return True
    except OSError as e:
        print(f"Error starting web server (Port {port} likely in use): {e}")
        return False
    except Exception as e:
        print(f"Unexpected error starting web server: {e}")
        return False

# stop_web_server remains the same
def stop_web_server():
    global httpd, server_thread
    if httpd:
        print("\nShutting down web server..."); httpd.shutdown(); httpd.server_close()
        print("Web server stopped.")
    if server_thread and server_thread.is_alive(): server_thread.join(timeout=1)


# --- OCR Cycle ---
def perform_ocr_and_search_cycle():
    global capture_region_coords, ocr_instance, last_cleaned_text, sse_message_queue

    if not capture_region_coords or ocr_instance is None: return

    try:
        with mss() as sct: sct_img = sct.grab(capture_region_coords)
        img_np = np.array(sct_img)
        processed_image = preprocess_screen_capture(img_np)
        if processed_image is None: return

        result = ocr_instance.ocr(processed_image, cls=False)

        if not result or not result[0]: return

        lines = [line[1][0] for res_list in result for line in res_list]
        raw_text = "".join(lines).strip()
        cleaned_text = clean_ocr_text(raw_text)

        # Check if cleaned text is valid and different from last time
        if cleaned_text and cleaned_text != last_cleaned_text:
            print(f"\n[{time.strftime('%H:%M:%S')}] OCR Found New Text:")
            print(f"    Cleaned: '{cleaned_text}'")

            last_cleaned_text = cleaned_text # Update history

            # --- Send to SSE Queue ---
            sse_message_queue.put(cleaned_text)
            print(f"    Sent to SSE queue for browser update.")
            # --- NO webbrowser.open() here anymore ---

    except Exception as e:
        print(f"\nError during OCR cycle: {e}")

# --- Hotkey Callbacks ---
def toggle_ocr_active():
    global ocr_active, last_cleaned_text
    ocr_active = not ocr_active
    if ocr_active:
        last_cleaned_text = "!RESET!" # Force update on first OCR after activation
        print(f"\n--- Continuous OCR Activated (Interval: {OCR_INTERVAL_SECONDS}s) ---")
    else:
        print("\n--- Continuous OCR Deactivated ---")

# trigger_reselect remains the same
def trigger_reselect():
    global capture_region_coords, ocr_active
    was_active = ocr_active
    if ocr_active: toggle_ocr_active() # Pause OCR
    print(f"\n--- Hotkey '{RESELECT_HOTKEY}' pressed: Reselecting region ---")
    new_region = select_region_gui()
    if new_region:
        capture_region_coords = new_region; save_config(capture_region_coords)
        print("Region updated. OCR remains deactivated.")
    else:
        print("Reselection cancelled. OCR remains deactivated.")
        # if was_active: toggle_ocr_active() # Option to restart if cancelled

# trigger_quit remains the same
def trigger_quit():
    global running
    print(f"\n--- Hotkey '{QUIT_HOTKEY}' pressed: Initiating shutdown ---")
    running = False

# --- Initialization ---
# initialize_ocr remains the same
def initialize_ocr():
    global ocr_instance
    print(f"\nInitializing PaddleOCR...")
    try:
        ocr_instance = PaddleOCR(use_angle_cls=False, lang=OCR_LANG, use_gpu=USE_GPU, show_log=False)
        print("PaddleOCR initialized.")
        return True
    except Exception as e: print(f"\nFATAL OCR Init Error: {e}"); return False

# signal_handler remains the same
def signal_handler(sig, frame): print("\nTermination signal received..."); trigger_quit()

# --- Main Execution ---
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    print("--- Real-time OCR -> Single Web Page Update via SSE ---")
    print("      (Requires Admin/Root privileges)")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"Script Directory: {script_dir}")

    # Verify files
    search_html_path=os.path.join(script_dir,'search_questions.html')
    json_data_path=os.path.join(script_dir,'combined_questions_data.json')
    if not os.path.exists(search_html_path) or not os.path.exists(json_data_path):
         print(f"\nERROR: Missing files in {script_dir}\n - search_questions.html: {'OK' if os.path.exists(search_html_path) else 'MISSING!'}\n - combined_questions_data.json: {'OK' if os.path.exists(json_data_path) else 'MISSING!'}")
         sys.exit(1)

    # Start Server
    if not start_web_server(SERVER_PORT, script_dir): sys.exit(1)

    print(f"\n--- Hotkey Setup ---")
    print(f" - Press '{TOGGLE_OCR_HOTKEY}' to START/STOP continuous OCR.")
    print(f" - Press '{RESELECT_HOTKEY}' to redefine capture region (stops OCR).")
    print(f" - Press '{QUIT_HOTKEY}' to exit.")

    # Load/Select Region
    if not load_config():
        new_region = select_region_gui()
        if new_region: capture_region_coords = new_region; save_config(new_region)
        else: print("\nERROR: Region selection required."); stop_web_server(); sys.exit(1)

    # Initialize OCR
    if not initialize_ocr(): stop_web_server(); sys.exit(1)

    # Setup Hotkeys
    try:
        keyboard.add_hotkey(TOGGLE_OCR_HOTKEY, toggle_ocr_active, trigger_on_release=False)
        keyboard.add_hotkey(RESELECT_HOTKEY, trigger_reselect, trigger_on_release=False)
        keyboard.add_hotkey(QUIT_HOTKEY, trigger_quit, trigger_on_release=False)
        print("\nHotkeys registered.")
    except Exception as e:
         print(f"\nERROR registering hotkeys: {e}\nEnsure Admin/Root privileges."); stop_web_server(); sys.exit(1)

    # --- Open browser ONCE ---
    initial_url = f"http://{SERVER_ADDRESS}:{SERVER_PORT}/search_questions.html"
    print(f"\nOpening search page: {initial_url}")
    webbrowser.open_new_tab(initial_url) # Use open_new_tab to prefer a new tab
    print(f"Press '{TOGGLE_OCR_HOTKEY}' to start sending OCR updates to the page.")
    # ---

    # --- Main Loop ---
    last_ocr_time = time.time()
    print("\nWaiting for hotkey commands or exit signal...")
    while running:
        current_time = time.time()
        if ocr_active and (current_time - last_ocr_time >= OCR_INTERVAL_SECONDS):
            perform_ocr_and_search_cycle()
            last_ocr_time = current_time

        time.sleep(0.05) # Prevent high CPU usage
    # --- End Main Loop ---

    # Cleanup
    stop_web_server()
    print("\nScript finished.")