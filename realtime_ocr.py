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

# --- Configuration ---
OCR_LANG = 'ch'  # Assumes Chinese + English text
USE_GPU = False  # Set to True if you have GPU PaddlePaddle installed and configured
CONFIG_FILE = 'ocr_config.json' # Stores the selected screen region
# --- Hotkeys ---
TOGGLE_OCR_HOTKEY = 'ctrl+alt+o' # Start/Stop continuous OCR
RESELECT_HOTKEY = 'ctrl+alt+r' # Redefine region
QUIT_HOTKEY = 'ctrl+alt+q'     # Exit script
# --- Server Configuration ---
SERVER_PORT = 8088  # Port for the local web server (change if needed)
SERVER_ADDRESS = "localhost"
# --- OCR Loop ---
OCR_INTERVAL_SECONDS = 1.0 # How often to check the screen region when active

# --- Global variables ---
capture_region_coords = None # Dictionary like {'top': y, 'left': x, 'width': w, 'height': h}
ocr_instance = None         # Holds the initialized PaddleOCR engine
httpd = None                # Holds the server instance
server_thread = None        # Holds the server thread
running = True              # Controls the main script loop and server lifetime
ocr_active = False          # Controls whether the OCR loop runs (toggled by hotkey)
last_searched_text = ""     # Prevents searching for the exact same text repeatedly

# --- Configuration Management ---
def load_config():
    """Loads capture region coordinates from the config file."""
    global capture_region_coords
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Basic validation
                if isinstance(config.get('region'), dict) and all(k in config['region'] for k in ['top', 'left', 'width', 'height']):
                    capture_region_coords = config['region']
                    print(f"Loaded region from {CONFIG_FILE}: {capture_region_coords}")
                    return True
                else:
                    print(f"Config file {CONFIG_FILE} has invalid region format.")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from {CONFIG_FILE}.")
        except Exception as e:
            print(f"Error loading config file {CONFIG_FILE}: {e}")
    print("Config file not found or invalid. Please select the region.")
    return False

def save_config(region):
    """Saves the capture region coordinates to the config file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({'region': region}, f, indent=4) # Add indent for readability
        print(f"Saved region to {CONFIG_FILE}: {region}")
    except Exception as e:
        print(f"Error saving config file {CONFIG_FILE}: {e}")

# --- Region Selection GUI (using Tkinter) ---
def select_region_gui():
    """Uses a transparent window to allow the user to select a screen region."""
    print("\nPlease select the capture region: Click and drag from top-left to bottom-right.")

    root = tk.Tk()
    # Make window transparent and cover the screen
    root.attributes("-alpha", 0.3)
    root.attributes("-fullscreen", True)
    root.wait_visibility(root) # Ensure window is ready before making topmost
    root.attributes("-topmost", True) # Keep window on top

    canvas = tk.Canvas(root, cursor="cross", bg='gray') # Set bg for better visibility of window
    canvas.pack(fill=tk.BOTH, expand=tk.YES)

    start_x, start_y = None, None
    rect_id = None
    selected_region = None

    # Mouse button press -> Record starting point
    def on_mouse_down(event):
        nonlocal start_x, start_y
        start_x, start_y = event.x, event.y
        # Clear previous rectangle if any
        if rect_id:
            canvas.delete(rect_id)

    # Mouse drag -> Draw rectangle
    def on_mouse_drag(event):
        nonlocal rect_id
        if start_x is not None and start_y is not None:
            # Delete previous rectangle for smooth drawing
            if rect_id:
                canvas.delete(rect_id)
            # Draw new rectangle from start point to current point
            rect_id = canvas.create_rectangle(start_x, start_y, event.x, event.y, outline='red', width=2)

    # Mouse button release -> Finalize selection and close
    def on_mouse_up(event):
        nonlocal selected_region
        if start_x is not None and start_y is not None:
            end_x, end_y = event.x, event.y
            # Ensure coordinates are top-left and bottom-right
            x1 = min(start_x, end_x)
            y1 = min(start_y, end_y)
            x2 = max(start_x, end_x)
            y2 = max(start_y, end_y)
            # Check for minimum size to avoid accidental clicks
            if (x2 - x1) > 10 and (y2 - y1) > 10:
                selected_region = {'top': y1, 'left': x1, 'width': x2 - x1, 'height': y2 - y1}
        root.quit() # Exit Tkinter main loop

    # Bind mouse events
    canvas.bind("<ButtonPress-1>", on_mouse_down)
    canvas.bind("<B1-Motion>", on_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_mouse_up)

    print("Screen overlay active. Drag to select region.")
    root.mainloop() # Start Tkinter event loop
    # Clean up Tkinter window
    try:
        root.destroy()
    except tk.TclError:
        pass # Window might already be destroyed

    if selected_region:
         print(f"Region selected: {selected_region}")
         return selected_region
    else:
         print("Region selection cancelled or invalid (too small?).")
         return None

# --- Text Cleaning ---
def clean_ocr_text(text):
    """Removes symbols, punctuation, numbers, and whitespace, keeping letters and CJK characters."""
    if not text:
        return ""
    # Regular expression to keep:
    # \u4e00-\u9fff: CJK Unified Ideographs (most common Chinese characters)
    # A-Za-z: English uppercase and lowercase letters
    # Add other character ranges if needed (e.g., numbers: 0-9)
    cleaned_text = re.sub(r'[^\u4e00-\u9fffA-Za-z]', '', text)
    return cleaned_text

# --- Image Preprocessing ---
def preprocess_screen_capture(img_np):
    """Preprocesses the captured NumPy array for potentially better OCR results."""
    try:
        # Convert MSS's BGRA to Grayscale for thresholding
        gray = cv2.cvtColor(img_np, cv2.COLOR_BGRA2GRAY)

        # Apply Adaptive Thresholding - often good for varying lighting
        # THRESH_BINARY_INV might work better for dark text on light background
        processed_img = cv2.adaptiveThreshold(
            src=gray,
            maxValue=255,
            adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            thresholdType=cv2.THRESH_BINARY_INV,
            blockSize=11, # Size of the pixel neighborhood (must be odd). Tune this.
            C=5         # Constant subtracted from the mean. Tune this.
        )

        # Optional: Further processing like median blur for noise reduction
        # processed_img = cv2.medianBlur(processed_img, 3)

        # For debugging: Show the processed image
        # cv2.imshow("Processed Capture", processed_img)
        # cv2.waitKey(1) # Wait briefly to allow window to display

        return processed_img
    except Exception as e:
        print(f"Error during image preprocessing: {e}")
        return None

# --- Web Server Functions ---
def start_web_server(port, directory):
    """Starts the HTTP server in a separate thread."""
    global httpd, server_thread
    try:
        # Ensure the handler serves files from the specified directory
        class CustomHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

        socketserver.TCPServer.allow_reuse_address = True # Allow quick restarts
        httpd = socketserver.TCPServer((SERVER_ADDRESS, port), CustomHandler)

        print(f"Serving HTTP on http://{SERVER_ADDRESS}:{port}/ from directory {directory}...")
        # Run the server in a daemon thread so it exits when the main script exits
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        return True
    except OSError as e:
        print(f"Error starting web server (Port {port} likely in use): {e}")
        return False
    except Exception as e:
        print(f"Unexpected error starting web server: {e}")
        return False

def stop_web_server():
    """Stops the HTTP server thread gracefully."""
    global httpd, server_thread
    if httpd:
        print("\nShutting down web server...")
        httpd.shutdown() # Stop the serve_forever loop
        httpd.server_close() # Close the server socket
        print("Web server stopped.")
    if server_thread and server_thread.is_alive():
        server_thread.join(timeout=1) # Wait max 1 second for the thread to finish

# --- OCR and Search Cycle ---
def perform_ocr_and_search_cycle():
    """Captures the defined screen region, performs OCR, cleans text, and triggers search if text changed."""
    global capture_region_coords, ocr_instance, last_searched_text, SERVER_ADDRESS, SERVER_PORT

    # Pre-checks
    if not capture_region_coords:
        print("OCR Cycle Error: Capture region not set.", end="\r")
        return
    if ocr_instance is None:
        print("OCR Cycle Error: OCR engine not initialized.", end="\r")
        return

    try:
        # Capture the screen region
        with mss() as sct:
            sct_img = sct.grab(capture_region_coords)

        # Convert to NumPy array for OpenCV
        img_np = np.array(sct_img)

        # Preprocess the captured image
        processed_image = preprocess_screen_capture(img_np)
        if processed_image is None:
            print("Preprocessing failed.", end="\r")
            return

        # Perform OCR
        # cls=False might be slightly faster if text rotation isn't common
        result = ocr_instance.ocr(processed_image, cls=False)

        # Process results
        if not result or not result[0]:
            # print("OCR Cycle: No text detected in region.", end="\r") # Reduce console noise
            return

        # Extract text lines (result is list of lists in newer PaddleOCR versions)
        lines = [line[1][0] for res_list in result for line in res_list if line[1][1] > 0.6] # Optional confidence filter
        raw_text = "".join(lines).strip() # Join lines directly
        cleaned_text = clean_ocr_text(raw_text) # Apply aggressive cleaning

        # Trigger search ONLY if cleaned text is found and DIFFERENT from the last search
        if cleaned_text and cleaned_text != last_searched_text:
            print(f"\n[{time.strftime('%H:%M:%S')}] Text Change Detected!")
            print(f"    Raw: '{raw_text[:100]}{'...' if len(raw_text)>100 else ''}'") # Show snippet of raw
            print(f"    Cleaned & Searching: '{cleaned_text}'")

            last_searched_text = cleaned_text # Update the last searched text

            # --- Trigger Search via HTTP ---
            search_query = urllib.parse.quote_plus(cleaned_text)
            search_url = f"http://{SERVER_ADDRESS}:{SERVER_PORT}/search_questions.html?query={search_query}"

            webbrowser.open(search_url) # Opens in default browser (usually new tab)
            print(f"    Browser opened/triggered with search.")
        # else:
            # Optional: Indicate no change if debugging needed
            # if cleaned_text: print(f"OCR Cycle: Text '{cleaned_text}' unchanged.", end="\r")
            # else: print("OCR Cycle: Cleaned text is empty.", end="\r")


    except Exception as e:
        print(f"\n[{time.strftime('%H:%M:%S')}] Error during OCR cycle: {e}")
    finally:
         pass
         # cv2.destroyAllWindows() # Close any debug windows if opened in preprocess

# --- Hotkey Callbacks ---
def toggle_ocr_active():
    """Toggles the continuous OCR process ON/OFF."""
    global ocr_active, last_searched_text
    ocr_active = not ocr_active
    if ocr_active:
        last_searched_text = "" # Reset last search history when activating
        print(f"\n--- Continuous OCR Activated (Interval: {OCR_INTERVAL_SECONDS}s) ---")
    else:
        print("\n--- Continuous OCR Deactivated ---")

def trigger_reselect():
    """Stops OCR temporarily, allows region reselection, saves config."""
    global capture_region_coords, ocr_active
    was_active = ocr_active # Remember if OCR was running
    if ocr_active:
        toggle_ocr_active() # Deactivate OCR before showing GUI

    print(f"\n--- Hotkey '{RESELECT_HOTKEY}' pressed: Reselecting region ---")
    new_region = select_region_gui()
    if new_region:
        capture_region_coords = new_region
        save_config(capture_region_coords)
        print("Region updated. OCR remains deactivated.")
        # Optional: Reactivate if it was active before?
        # if was_active:
        #     toggle_ocr_active()
        #     print("OCR reactivated with new region.")
    else:
        print("Reselection cancelled. OCR remains deactivated.")
        # Optional: Reactivate if it was active before and selection failed?
        # if was_active: toggle_ocr_active()


def trigger_quit():
    """Signals the main loop and server to stop."""
    global running
    print(f"\n--- Hotkey '{QUIT_HOTKEY}' pressed: Initiating shutdown ---")
    running = False # Signal the main loop to exit

# --- Initialization and Main Loop ---
def initialize_ocr():
    """Initializes the PaddleOCR engine."""
    global ocr_instance
    print(f"\nInitializing PaddleOCR (lang='{OCR_LANG}', gpu={USE_GPU})... This may take a moment.")
    # First run might download models.
    try:
        ocr_instance = PaddleOCR(
            use_angle_cls=False, # Set True if rotated text is common
            lang=OCR_LANG,
            use_gpu=USE_GPU,
            show_log=False # Suppress verbose PaddleOCR logging
        )
        print("PaddleOCR initialized successfully.")
        return True
    except Exception as e:
        print(f"\nFATAL ERROR: Could not initialize PaddleOCR: {e}")
        print("Please ensure PaddlePaddle and PaddleOCR are installed correctly.")
        print("If using GPU, check CUDA/cuDNN compatibility.")
        return False

# --- Graceful Exit Handler ---
def signal_handler(sig, frame):
    """Handles Ctrl+C or other termination signals to ensure clean shutdown."""
    print("\nTermination signal received. Shutting down...")
    trigger_quit() # Use the same quit logic as the hotkey

# --- Main Execution ---
if __name__ == "__main__":
    # Register signal handlers for graceful exit (Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("--- Real-time Continuous Screen OCR & Search ---")
    print("      (Requires Admin/Root privileges for hotkeys)")

    # Determine script directory to find HTML/JSON files
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"Script Directory: {script_dir}")

    # Verify essential files exist
    search_html_path = os.path.join(script_dir, 'search_questions.html')
    json_data_path = os.path.join(script_dir, 'combined_questions_data.json')
    if not os.path.exists(search_html_path) or not os.path.exists(json_data_path):
         print("\nERROR: Missing essential files:")
         print(f" - search_questions.html: {'FOUND' if os.path.exists(search_html_path) else 'MISSING!'}")
         print(f" - combined_questions_data.json: {'FOUND' if os.path.exists(json_data_path) else 'MISSING!'}")
         print(f"Please ensure these files are in the directory: {script_dir}")
         sys.exit(1)

    # Start the local web server
    if not start_web_server(SERVER_PORT, script_dir):
         sys.exit(1) # Exit if server fails to start

    print(f"\n--- Hotkey Setup ---")
    print(f" - Press '{TOGGLE_OCR_HOTKEY}' to START/STOP continuous OCR.")
    print(f" - Press '{RESELECT_HOTKEY}' to redefine capture region (stops OCR).")
    print(f" - Press '{QUIT_HOTKEY}' to exit.")

    # Load OCR region config or prompt user to select it
    if not load_config():
        new_region = select_region_gui()
        if new_region:
            capture_region_coords = new_region
            save_config(capture_region_coords)
        else:
            print("\nERROR: Region selection is required. Exiting.")
            stop_web_server() # Stop server before exiting
            sys.exit(1)

    # Initialize the OCR engine
    if not initialize_ocr():
         stop_web_server()
         sys.exit(1)

    # Setup global hotkeys
    try:
        # trigger_on_release=False makes it react on press
        keyboard.add_hotkey(TOGGLE_OCR_HOTKEY, toggle_ocr_active, trigger_on_release=False)
        keyboard.add_hotkey(RESELECT_HOTKEY, trigger_reselect, trigger_on_release=False)
        keyboard.add_hotkey(QUIT_HOTKEY, trigger_quit, trigger_on_release=False)
        print("\nHotkeys registered successfully.")
        print(f"Press '{TOGGLE_OCR_HOTKEY}' to begin continuous OCR.")
    except ImportError:
         print("\nERROR: 'keyboard' library not found or could not be initialized.")
         print("Please install it: pip install keyboard")
         print("Ensure you are running with Admin/Root privileges.")
         stop_web_server()
         sys.exit(1)
    except Exception as e:
         print(f"\nERROR registering hotkeys: {e}")
         print("Ensure you are running with Admin/Root privileges.")
         stop_web_server()
         sys.exit(1)

    # --- Main Loop ---
    last_ocr_time = time.time()
    print("\nWaiting for hotkey commands...")
    while running:
        current_time = time.time()
        # Check if OCR is active and enough time has passed
        if ocr_active and (current_time - last_ocr_time >= OCR_INTERVAL_SECONDS):
            perform_ocr_and_search_cycle()
            last_ocr_time = current_time # Reset timer *after* processing

        # Small sleep to prevent the loop from consuming 100% CPU
        time.sleep(0.05)
    # --- End of Main Loop ---

    # Cleanup
    stop_web_server() # Ensure server is stopped on exit
    # Optional: keyboard cleanup (usually automatic, but can be explicit)
    # try:
    #     keyboard.unhook_all()
    # except Exception:
    #     pass
    print("\nScript finished.")