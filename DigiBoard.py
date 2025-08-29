import os
import sys
import time
import threading
from pathlib import Path
import random  # for shuffling pages
import json
import logging
import fitz  # PyMuPDF
try:
    from PIL import Image, ImageTk  # Pillow is required for image handling with Tkinter
except ImportError as exc:
    raise ImportError(
        "Pillow (PIL) is required to run this script. Install it via 'pip install pillow'."
    ) from exc
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError as exc:
    raise ImportError(
        "Tkinter is required to run this script. On Windows it ships with Python; "
        "on Linux you may need to install the python3‑tk package."
    ) from exc

# Change this to point at the directory containing your notice PDFs
PDF_DIR = Path(__file__).resolve().parent / "notices"

# Optional: specify the path to a logo image to display at the top center of the noticeboard.
# If None, no logo is shown.  Use an absolute Path or a path relative to this script.
# Example on Windows: Path(r"E:\\Digiboard\\LOGO.png")
LOGO_PATH: Path | None = None
# Maximum height (in pixels) for the logo
LOGO_MAX_HEIGHT = 100

# ----------------------------------------------------------------------
# Configuration and logging
# The application reads settings from a config.json file located in the
# same directory as this script (APP_DIR).  If config.json is absent or
# incomplete, sensible defaults are used.
# --- Bundle-friendly paths ---
def _app_dir():
    # When bundled (PyInstaller), _MEIPASS points to the temp extraction dir
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent

APP_DIR = _app_dir()
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "noticeboard.log"


def load_config():
    """Load configuration from config.json with sensible defaults."""
    cfg = {
        "pdf_dir": "notices",
        "logo_path": "",
        "cycle_interval": 10,
        "background_color": "#FFFFFF",
        "fit_mode": "fit_page",
        "zoom_step": 0.1,
        "max_logo_height": 100,
        "wheel_mode": "navigate",
        "page_indicator": "top-right",
        "toolbar": False,
        # Shuffle the order of all pages after loading.  If true, pages from
        # multiple PDFs are combined and then shuffled randomly.
        "shuffle_pages": False,
        # Number of pixels to pan when using pan controls (w/a/s/d).  Larger
        # values move faster across zoomed pages.
        "pan_step": 50,
        # Idle timeout in seconds.  If greater than zero, the noticeboard will
        # automatically pause and display a screensaver overlay after this
        # many seconds of no user interaction.  A value of 0 disables the
        # screensaver entirely.
        "idle_timeout": 0,
        # Colour of the idle overlay (screensaver).  Should contrast with
        # foreground text for readability.
        "idle_overlay_color": "#000000",
        # Text to display when the screensaver is active.
        "idle_overlay_text": "Idle",
        # Number of thumbnails to display in the bottom row for the most
        # recently modified notices.  These small previews show the first
        # page of each PDF.  Increase or decrease this number to show more
        # or fewer notices.
        "thumbnails_count": 4,
        # Height of each thumbnail image in the bottom row (pixels).
        # Larger values make the preview images more visible.  The width
        # is calculated automatically based on the aspect ratio of the
        # first page of each PDF.  Set this to a larger number to
        # increase thumbnail size.
        "thumbnail_height": 150,
        # Factor by which the selected thumbnail is enlarged for a parallax
        # effect.  Values greater than 1.0 enlarge the selected image.
        # A typical value is 1.2 (20% larger).
        "thumbnail_enlarge_factor": 1.2,
        # Whether to show the current date alongside the clock in the top
        # bar.  If true, the date is displayed in YYYY‑MM‑DD format
        # followed by the 12‑hour time.  If false, only the time is
        # displayed.
        "show_date": True,
        # Shuffle the order of PDF files during rotation.  When true,
        # the noticeboard will randomise the sequence of files each
        # time it loads or reloads them.
        "shuffle_files": False,
        # Colour used to highlight the thumbnail corresponding to the
        # currently displayed PDF.  This should be a valid Tkinter
        # colour code (e.g. hex or named colour).  The highlight is
        # applied as a border around the selected thumbnail.  A
        # soothing blue is used by default.
        "highlight_color": "#0077CC",
        # Whether to animate the scrolling of the thumbnail carousel when
        # navigating between files.  When set to False, the carousel
        # simply jumps to ensure the selected file is visible, avoiding
        # bouncy or stuttering animations.  Set to True for smooth
        # scrolling.
        "scroll_animation": False,
        # Font size for the clock/time display in the top bar.  A smaller
        # value reduces the space occupied by the clock.  Defaults to 24.
        "clock_font_size": 18,
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception as e:
            print(f"Warning: could not read {CONFIG_PATH}: {e}")
    return cfg

# Load user configuration
CFG = load_config()

# Initialise logging.  Messages about PDF loading and errors are written to
# noticeboard.log in the application directory.
logging.basicConfig(
    filename=LOG_PATH,
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)

# Override PDF_DIR, LOGO_PATH and LOGO_MAX_HEIGHT based on configuration.
PDF_DIR = (APP_DIR / CFG.get("pdf_dir", "notices")).resolve()
logo_path_str = CFG.get("logo_path", "")
if logo_path_str:
    try:
        LOGO_PATH = Path(logo_path_str).resolve()
    except Exception:
        LOGO_PATH = None
else:
    LOGO_PATH = None
try:
    LOGO_MAX_HEIGHT = int(CFG.get("max_logo_height", LOGO_MAX_HEIGHT))
except Exception:
    LOGO_MAX_HEIGHT = 100

class DigitalNoticeboard:
    def __init__(self, root: tk.Tk, pdf_paths, cycle_interval: int = 10) -> None:
     
        self.root = root
        self.pdf_paths = pdf_paths
        # Load configuration values
        cfg = CFG
        self.fit_mode = cfg.get("fit_mode", "fit_page")
        self.zoom = 1.0
        try:
            self.zoom_step = max(0.01, float(cfg.get("zoom_step", 0.1)))
        except Exception:
            self.zoom_step = 0.1
        self.wheel_mode = cfg.get("wheel_mode", "navigate")
        self.background_color = cfg.get("background_color", "#FFFFFF")
        self.page_indicator_pos = cfg.get("page_indicator", "top-right")
        self.show_toolbar = bool(cfg.get("toolbar", False))
        # Shuffle pages after loading?  True/False
        self.shuffle_pages = bool(cfg.get("shuffle_pages", False))
        # Pan step for panning controls (pixels)
        try:
            self.pan_step = int(cfg.get("pan_step", 50))
        except Exception:
            self.pan_step = 50
        # Idle/screen saver settings
        try:
            self.idle_timeout = int(cfg.get("idle_timeout", 0))
        except Exception:
            self.idle_timeout = 0
        self.idle_overlay_color = cfg.get("idle_overlay_color", "#000000")
        self.idle_overlay_text = cfg.get("idle_overlay_text", "Idle")

        # Number of thumbnails to display in bottom row
        try:
            self.thumbnails_count = int(cfg.get("thumbnails_count", 4))
        except Exception:
            self.thumbnails_count = 4
        # Height of thumbnails in pixels.  Use the configured value if
        # provided; otherwise fall back to a sensible default.
        try:
            self.thumbnail_height = int(cfg.get("thumbnail_height", 150))
        except Exception:
            self.thumbnail_height = 150
        # Whether to show date with the clock
        self.show_date = bool(cfg.get("show_date", False))
        # Shuffle files during rotation
        self.shuffle_files = bool(cfg.get("shuffle_files", False))
        try:
            self.max_logo_height = int(cfg.get("max_logo_height", LOGO_MAX_HEIGHT))
        except Exception:
            self.max_logo_height = LOGO_MAX_HEIGHT
        # Highlight colour for the active thumbnail
        self.highlight_color = cfg.get("highlight_color", "#FF0000")
        # Factor for enlarging the selected thumbnail
        try:
            self.thumbnail_enlarge_factor = float(cfg.get("thumbnail_enlarge_factor", 1.2))
            if self.thumbnail_enlarge_factor < 1.0:
                self.thumbnail_enlarge_factor = 1.0
        except Exception:
            self.thumbnail_enlarge_factor = 1.2

        # ------------------------------------------------------------------
        # Screen‑size adaptive scaling
        #
        # When running on displays of varying resolutions (for example when
        # remoting into the noticeboard on a small monitor), fixed pixel
        # values for the logo and thumbnail heights can cause the bottom
        # carousel to overflow off screen.  To make the user interface
        # responsive, compute reasonable defaults based on the current screen
        # dimensions.  If a value is already provided in the configuration
        # file, it will cap the computed size rather than being ignored.
        try:
            # Retrieve the height of the primary display in pixels.  This
            # allows the program to adapt to monitors of different sizes.
            screen_h = self.root.winfo_screenheight()
        except Exception:
            screen_h = 0
        if screen_h:
            # Target the thumbnail row height at roughly 12 percent of the
            # screen height.  If the configured value is smaller it will be
            # kept, otherwise it is limited to this computed size to
            # prevent overflow on short displays.
            computed_thumb_h = int(screen_h * 0.12)
            if computed_thumb_h > 0:
                self.thumbnail_height = min(self.thumbnail_height, computed_thumb_h)
            # Set the maximum height for the logo images to approximately
            # 8 percent of the screen height.  The existing configuration
            # value will prevail if it is smaller.
            computed_logo_h = int(screen_h * 0.08)
            if computed_logo_h > 0:
                self.max_logo_height = min(self.max_logo_height, computed_logo_h)
        # Determine cycle interval (seconds per page)
        ci = cfg.get("cycle_interval", cycle_interval)
        try:
            ci = int(ci)
        except Exception:
            ci = cycle_interval
        self.cycle_interval = max(3, ci)
        # Whether to animate carousel scrolling.  This is set in
        # configuration and allows the user to disable scrolling animations
        # entirely to prevent bounce/stutter effects.
        self.scroll_animation = bool(cfg.get("scroll_animation", False))

        # Clock font size: read from configuration.  If not provided or
        # invalid, default to 24 points.  This influences the size of the
        # clock text in the top bar and therefore the width of the left
        # column.
        try:
            self.clock_font_size = int(cfg.get("clock_font_size", 24))
        except Exception:
            self.clock_font_size = 24
        # Internal state
        # List of file dictionaries with keys 'pages', 'thumbnail', 'path', 'modified_time'
        self.files: list[dict] = []
        # Index of currently displayed file and page
        self.current_file_index = 0
        self.current_page_index = 0
        # Timer ID for rotation
        self.cycle_id = None
        # Pause state
        self.paused = False
        # Offsets used for panning when zoomed (>1).  Offset values are in
        # pixels of the resized image; they represent the top-left corner
        # of the cropping region when the image is larger than the display area.
        self.offset_x = 0
        self.offset_y = 0
        # Track last user interaction time for idle detection
        self.last_interaction_time = time.time()
        # Load PDF files and build UI
        self._load_files()
        self._build_ui()
        self._update_clock()
        # Begin automatic rotation by scheduling the next page.  When the
        # last page of a file has been displayed, the next invocation will
        # advance to the next file.
        self._schedule_next_page()

        # Start idle check if idle_timeout is enabled
        if self.idle_timeout and self.idle_timeout > 0:
            self._check_idle()

    def _load_pages(self) -> None:
        """Load every page from each PDF into the pages list as PIL images.

        Errors encountered while opening documents or rendering pages are logged
        and the offending files/pages are skipped.
        """
        for pdf_path in self.pdf_paths:
            try:
                doc = fitz.open(pdf_path)
            except Exception as exc:
                logging.error("Failed to open PDF %s: %s", pdf_path, exc)
                continue
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    pix = page.get_pixmap()
                    # Convert PyMuPDF pixmap to PIL Image
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    self.pages.append(img)
                except Exception as exc:
                    logging.error(
                        "Failed to render page %d of %s: %s",
                        page_num + 1,
                        pdf_path,
                        exc,
                    )
        if not self.pages:
            raise RuntimeError(
                "No pages loaded from PDFs; please place PDFs in the configured directory."
            )
        # Shuffle pages if configured.  This occurs after all pages are loaded
        # from all documents so that the order is fully randomised across
        # documents.  We shuffle a copy of the list in place.
        if getattr(self, "shuffle_pages", False):
            try:
                random.shuffle(self.pages)
            except Exception:
                pass

    def _load_files(self) -> None:
      
        self.files.clear()
        for pdf_path in self.pdf_paths:
            try:
                doc = fitz.open(pdf_path)
            except Exception as exc:
                logging.error("Failed to open PDF %s: %s", pdf_path, exc)
                continue
            pages: list[Image.Image] = []
            # Convert every page to a PIL Image
            for page_num in range(len(doc)):
                try:
                    page = doc[page_num]
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    pages.append(img)
                except Exception as exc:
                    logging.error(
                        "Failed to render page %d of %s: %s",
                        page_num + 1,
                        pdf_path,
                        exc,
                    )
            # Skip files with no renderable pages
            if not pages:
                continue
            # Create a thumbnail from the first page
            first_page = pages[0]
            # Determine target thumbnail height.  Use the instance's
            # configured value if available, otherwise fall back to a
            # reasonable default.  This allows thumbnails to be larger
            # and more visible when configured by the user.
            try:
                thumb_height = int(getattr(self, "thumbnail_height", CFG.get("thumbnail_height", 100)))
            except Exception:
                thumb_height = 100
            try:
                ratio = thumb_height / float(first_page.height)
                thumb_size = (int(first_page.width * ratio), thumb_height)
                thumbnail = first_page.resize(thumb_size, Image.LANCZOS)
            except Exception:
                # Fallback to original size if resizing fails
                thumbnail = first_page
            # Create a tinted version of the thumbnail to use when the
            # corresponding file is selected.  Blend the original
            # thumbnail with the highlight colour from the configuration.
            try:
                hl_hex = CFG.get("highlight_color", "#0077CC")
                # Ensure the string is in the form #RRGGBB
                if isinstance(hl_hex, str) and hl_hex.startswith("#") and len(hl_hex) == 7:
                    hr = int(hl_hex[1:3], 16)
                    hg = int(hl_hex[3:5], 16)
                    hb = int(hl_hex[5:7], 16)
                else:
                    hr, hg, hb = (0, 119, 204)  # fallback to blue
                overlay = Image.new("RGB", thumbnail.size, (hr, hg, hb))
                # Blend original thumbnail with overlay.  Adjust alpha to tune
                # the intensity of the tint (0.0 = original, 1.0 = full colour).
                tint_alpha = 0.3
                try:
                    # Ensure thumbnail is in RGB mode
                    base_img = thumbnail.convert("RGB")
                except Exception:
                    base_img = thumbnail
                try:
                    thumbnail_selected = Image.blend(base_img, overlay, tint_alpha)
                except Exception:
                    thumbnail_selected = base_img
            except Exception:
                thumbnail_selected = thumbnail
            # Create enlarged versions of the thumbnails for parallax effect
            try:
                factor = float(getattr(self, "thumbnail_enlarge_factor", 1.2))
                if factor < 1.0:
                    factor = 1.0
            except Exception:
                factor = 1.2
            try:
                width_enlarged = int(thumbnail.width * factor)
                height_enlarged = int(thumbnail.height * factor)
                thumbnail_enlarged = thumbnail.resize((width_enlarged, height_enlarged), Image.LANCZOS)
                thumbnail_selected_enlarged = thumbnail_selected.resize((width_enlarged, height_enlarged), Image.LANCZOS)
            except Exception:
                thumbnail_enlarged = thumbnail
                thumbnail_selected_enlarged = thumbnail_selected
            # Record file info
            try:
                modified_time = pdf_path.stat().st_mtime
            except Exception:
                modified_time = 0
            self.files.append({
                "pages": pages,
                "thumbnail": thumbnail,
                "thumbnail_selected": thumbnail_selected,
                "thumbnail_enlarged": thumbnail_enlarged,
                "thumbnail_selected_enlarged": thumbnail_selected_enlarged,
                "path": pdf_path,
                "modified_time": modified_time,
            })
        # If no files loaded, raise an error
        if not self.files:
            raise RuntimeError(
                "No PDF files loaded; please place PDFs in the configured directory."
            )
        # Shuffle files if configured
        if getattr(self, "shuffle_files", False):
            try:
                random.shuffle(self.files)
            except Exception:
                pass
    def _exit_app(self, event=None) -> None:
        """Exit the application cleanly when Escape is pressed."""
        try:
            self.root.destroy()
        except Exception:
            sys.exit(0)
    def _build_ui(self) -> None:
        """
        Set up the Tkinter widgets.

        The user interface consists of three main areas:

        * A top bar with a clock on the left, an optional logo centered, and
          a page number indicator on the right.  The logo is loaded from
          ``LOGO_PATH`` if provided and scaled to ``LOGO_MAX_HEIGHT`` while
          maintaining its aspect ratio.  If no logo path is supplied, the
          center column remains empty.
        * A large canvas area below the top bar that displays the current
          notice page.
        * Keyboard and mouse bindings for interactivity: space to pause/resume,
          left/right arrows to navigate, and the mouse/scroll wheel for
          Huion dial support.
        """

        self.root.title("DigiBoard")
        # Use the configured background colour
        self.root.configure(bg=self.background_color)
        self.root.attributes("-fullscreen", True)
     
# Window icon from config (optional)
        icon_path_str = CFG.get("icon_path", "")
        if icon_path_str:
            try:
                # Expand environment variables and user home in the path
                expanded_icon = os.path.expanduser(os.path.expandvars(icon_path_str))
                icon_candidate = Path(expanded_icon)
                # If the path isn't absolute, resolve it relative to the
                # application directory.  This allows specifying icon files
                # with relative paths in the configuration file (e.g.,
                # "Icon/digiboardicon.ico").
                if not icon_candidate.is_absolute():
                    icon_candidate = (APP_DIR / icon_candidate)
                if icon_candidate.exists():
                    self.root.iconbitmap(str(icon_candidate))
            except Exception as e:
                print(f"Could not set icon: {e}")

        # Top bar frame.  We place the clock on the left, the logo in
        # the centre and the page indicator on the right using grid
        # geometry.  To ensure the logo stays visually centred regardless
        # of the varying widths of the clock and page indicator, the
        # middle column is given a weight of 1 and the outer columns are
        # given a weight of 0.  This makes the centre column absorb all
        # extra horizontal space, keeping the logo centred in the window.
        self.top_frame = tk.Frame(self.root, bg=self.background_color)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)
        # Configure three columns: left and right columns get weight 0
        # (minimal width) and the centre gets weight 1 to expand.
        self.top_frame.columnconfigure(0, weight=0)
        self.top_frame.columnconfigure(1, weight=1)
        self.top_frame.columnconfigure(2, weight=0)

        # Clock label (left).  We will update it in _update_clock().  The
        # font size is configurable via ``clock_font_size`` in the
        # configuration.  A smaller value reduces the width occupied by
        # the clock, helping the logo remain centred.
        self.clock_label = tk.Label(
            self.top_frame,
            text="",
            font=("Helvetica", getattr(self, "clock_font_size", 24)),
            fg="black",
            bg=self.background_color,
        )
        self.clock_label.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))

        # Logo label (centre).  This will display the logo if provided.
        # Use anchor="center" so that any image or text is centred within
        # the label itself.  The column has weight 1, so the label's cell
        # expands to take up all remaining horizontal space, and sticky
        # "nsew" causes the label to expand with the cell.  Together,
        # this ensures the logo stays perfectly centred relative to the
        # clock and page indicator, regardless of their widths.
        self.top_logo_label = tk.Label(
            self.top_frame,
            bg=self.background_color,
            anchor="center"
        )
        self.top_logo_label.grid(row=0, column=1, sticky="nsew", pady=(10, 5))
        # Load the logo for the top bar
        top_logo_image = None
        if LOGO_PATH is not None:
            logo_source = None
            try:
                if LOGO_PATH.exists():
                    if LOGO_PATH.is_dir():
                        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp"):
                            files = list(LOGO_PATH.glob(pattern))
                            if files:
                                logo_source = files[0]
                                break
                    else:
                        logo_source = LOGO_PATH
                if logo_source is not None:
                    pil_logo = Image.open(logo_source)
                    # Scale the logo to max_logo_height
                    ratio = self.max_logo_height / float(pil_logo.height)
                    new_size = (int(pil_logo.width * ratio), self.max_logo_height)
                    pil_logo = pil_logo.resize(new_size, Image.LANCZOS)
                    top_logo_image = ImageTk.PhotoImage(pil_logo)
                    self.top_logo_label.config(image=top_logo_image)
                    self.top_logo_label.image = top_logo_image
            except Exception:
                pass

        # After laying out the clock, logo and page indicator, adjust the
        # widths of the left and right columns to be equal.  Without this
        # adjustment, the centre column (where the logo resides) can shift
        # off the true centre of the window when the clock and page
        # indicator have different widths.  By computing the maximum of
        # their current widths and setting that as the minimum size for
        # both outer columns, we ensure the logo column is centred within
        # the available space.
        try:
            # Force geometry update so winfo_width() returns correct values
            self.root.update_idletasks()
            left_width = self.clock_label.winfo_width()
            right_width = self.page_label.winfo_width()
            max_width = max(left_width, right_width)
            # Set both outer columns to the same minimum size
            self.top_frame.columnconfigure(0, minsize=max_width)
            self.top_frame.columnconfigure(2, minsize=max_width)
        except Exception:
            pass

        # Page indicator label (right).  Shows current notice index.
        self.page_label = tk.Label(
            self.top_frame,
            text="",
            font=("Helvetica", 18),
            fg="black",
            bg=self.background_color,
        )
        self.page_label.grid(row=0, column=2, sticky="e", padx=10, pady=(10, 5))

        # Main display area for the current notice
        self.image_label = tk.Label(self.root, bg=self.background_color)
        self.image_label.pack(expand=True, fill=tk.BOTH)

        # Bottom frame containing the carousel of thumbnails and centre logo
        self.bottom_frame = tk.Frame(self.root, bg=self.background_color)
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 10))
        # Canvas to hold the thumbnails (carousel).  Using a canvas allows
        # smooth horizontal scrolling when there are more files than can
        # fit on screen.
        self.thumbnails_canvas = tk.Canvas(
            self.bottom_frame,
            bg=self.background_color,
            highlightthickness=0,
        )
        self.thumbnails_canvas.pack(side="left", fill=tk.BOTH, expand=True)
        # A frame inside the canvas to contain the thumbnail labels
        self.thumbnails_container = tk.Frame(self.thumbnails_canvas, bg=self.background_color)
        # Add the frame to the canvas and store the window ID for later
        self.thumbnails_canvas_window = self.thumbnails_canvas.create_window(
            (0, 0), window=self.thumbnails_container, anchor="nw"
        )
        # Centre logo frame.  This will remain on the right side of the bottom
        # row and does not scroll with the carousel.
        self.center_logo_frame = tk.Frame(self.bottom_frame, bg=self.background_color)
        self.center_logo_frame.pack(side="right", padx=(10, 10))

        # Load and display the centre logo in the bottom row
        self.center_logo_label = tk.Label(self.center_logo_frame, bg=self.background_color)
        # Load the logo image for the centre if LOGO_PATH is provided
        centre_logo_image = None
        if LOGO_PATH is not None:
            logo_source = None
            try:
                if LOGO_PATH.exists():
                    if LOGO_PATH.is_dir():
                        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp"):
                            files = list(LOGO_PATH.glob(pattern))
                            if files:
                                logo_source = files[0]
                                break
                    else:
                        logo_source = LOGO_PATH
                if logo_source is not None:
                    pil_logo = Image.open(logo_source)
                    # Scale logo to fit bottom row height (use self.max_logo_height)
                    ratio = self.max_logo_height / float(pil_logo.height)
                    new_size = (int(pil_logo.width * ratio), self.max_logo_height)
                    pil_logo = pil_logo.resize(new_size, Image.LANCZOS)
                    centre_logo_image = ImageTk.PhotoImage(pil_logo)
                    self.center_logo_label.config(image=centre_logo_image)
                    self.center_logo_label.image = centre_logo_image
            except Exception as exc:
                print(f"Warning: could not load logo from {LOGO_PATH}: {exc}")
        self.center_logo_label.pack(expand=True)
        # Hide the bottom logo frame since the logo is now displayed in the
        # top bar.  This prevents duplication of the logo in the bottom row.
        try:
            self.center_logo_frame.pack_forget()
        except Exception:
            pass

        # Prepare thumbnail labels list (will be populated in _update_thumbnails)
        self.thumbnail_labels: list[tk.Label] = []

        # Optionally add a toolbar with common actions below bottom row
        if self.show_toolbar:
            self.toolbar_frame = tk.Frame(self.root, bg=self.background_color)
            self.toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 10))
            btn_prev = tk.Button(self.toolbar_frame, text="Prev", command=lambda: self._show_previous_file())
            btn_prev.pack(side="left", padx=5, pady=5)
            btn_pause = tk.Button(self.toolbar_frame, text="Pause", command=lambda: self._toggle_pause())
            btn_pause.pack(side="left", padx=5, pady=5)
            self.pause_button = btn_pause
            btn_next = tk.Button(self.toolbar_frame, text="Next", command=lambda: self._show_next_file())
            btn_next.pack(side="left", padx=5, pady=5)
            btn_fit = tk.Button(self.toolbar_frame, text="Fit", command=lambda: self._cycle_fit_mode())
            btn_fit.pack(side="left", padx=5, pady=5)
            btn_zoom_out = tk.Button(self.toolbar_frame, text="Zoom -", command=lambda: self._zoom_out())
            btn_zoom_out.pack(side="left", padx=5, pady=5)
            btn_zoom_in = tk.Button(self.toolbar_frame, text="Zoom +", command=lambda: self._zoom_in())
            btn_zoom_in.pack(side="left", padx=5, pady=5)
            btn_reload = tk.Button(self.toolbar_frame, text="Reload", command=lambda: self._reload_pdfs())
            btn_reload.pack(side="left", padx=5, pady=5)

        # Bind keyboard and mouse controls for file-level navigation
        self.root.bind("<space>", self._toggle_pause)
        self.root.bind("<Left>", self._show_previous_file)
        self.root.bind("<Right>", self._show_next_file)
        self.root.bind("+", self._zoom_in)
        self.root.bind("-", self._zoom_out)
        self.root.bind("0", self._zoom_reset)
        self.root.bind("f", self._cycle_fit_mode)
        self.root.bind("F", self._cycle_fit_mode)
        self.root.bind("r", self._reload_pdfs)
        self.root.bind("R", self._reload_pdfs)
        self.root.bind("<MouseWheel>", self._on_scroll)
        self.root.bind("<Button-4>", self._on_scroll)
        self.root.bind("<Button-5>", self._on_scroll)
        self.root.bind("<Escape>", self._exit_app)

        # Bind panning keys (inherited from earlier code)
        self.root.bind("w", self._pan_up)
        self.root.bind("W", self._pan_up)
        self.root.bind("s", self._pan_down)
        self.root.bind("S", self._pan_down)
        self.root.bind("a", self._pan_left)
        self.root.bind("A", self._pan_left)
        self.root.bind("d", self._pan_right)
        self.root.bind("D", self._pan_right)

        # Populate thumbnails in the bottom row now
        self._update_thumbnails()

        # The panning keys and idle overlay have already been bound and created above

    def _update_clock(self) -> None:
        """Update the clock on the top bar.

        The time is displayed in 12‑hour format.  If the
        ``show_date`` option is enabled, the current weekday, month,
        day and year are appended on a second line.  Leading zeros
        are removed from the hour for a cleaner appearance.
        """
        if self.show_date:
            # 12‑hour time string, stripping any leading zero from the hour
            time_str = time.strftime("%I:%M:%S %p").lstrip("0")
            # Day of week and date (e.g., "Wednesday, Aug 21 2025")
            date_str = time.strftime("%A, %b %d %Y")
            now_text = f"{time_str}\n{date_str}"
        else:
            # 12‑hour time only
            now_text = time.strftime("%I:%M:%S %p").lstrip("0")
        try:
            self.clock_label.config(text=now_text)
        except Exception:
            pass
        # Schedule next update
        self.root.after(1000, self._update_clock)

    def _schedule_next_page(self) -> None:
        """
        Schedule the display of the next page (or next file) after the
        configured interval.

        Unlike the earlier file‑level scheduler, this always schedules
        ``_show_next_page``.  When the current page is not the last page
        of the current PDF, ``_show_next_page`` simply advances to the
        next page.  When on the final page of a file, ``_show_next_page``
        will transition to the first page of the next file.
        """
        if not self.paused:
            self.cycle_id = self.root.after(self.cycle_interval * 1000, self._show_next_page)

    def _schedule_next_file(self) -> None:
        """Schedule the display of the next file after the cycle interval."""
        if not self.paused:
            self.cycle_id = self.root.after(self.cycle_interval * 1000, self._show_next_file)

    def _show_next_file(self, event=None) -> None:
        """Advance to the next PDF file and display its first page."""
        # Mark user interaction
        self._mark_interaction()
        if not self.files:
            return
        # Increment file index with wrap‑around
        self.current_file_index = (self.current_file_index + 1) % len(self.files)
        # Scroll the carousel to keep the new file visible
        try:
            self._scroll_to_index(self.current_file_index, animate=self.scroll_animation)
        except Exception:
            pass
        # Trigger an animation on the thumbnails to indicate selection
        try:
            self._animate_thumbnail_selection(self.current_file_index)
        except Exception:
            # Fallback to immediate highlight update if animation fails
            try:
                self._update_thumbnail_highlight()
            except Exception:
                pass
        # Reset page and panning offsets
        self.current_page_index = 0
        self.offset_x = 0
        self.offset_y = 0
        # Show first page of the new file
        self._show_page(0)

    def _show_previous_file(self, event=None) -> None:
        """Go back to the previous PDF file and display its first page."""
        # Mark user interaction
        self._mark_interaction()
        if not self.files:
            return
        self.current_file_index = (self.current_file_index - 1) % len(self.files)
        # Scroll the carousel to keep the new file visible
        try:
            self._scroll_to_index(self.current_file_index, animate=self.scroll_animation)
        except Exception:
            pass
        # Trigger an animation on the thumbnails for selection
        try:
            self._animate_thumbnail_selection(self.current_file_index)
        except Exception:
            try:
                self._update_thumbnail_highlight()
            except Exception:
                pass
        self.current_page_index = 0
        self.offset_x = 0
        self.offset_y = 0
        self._show_page(0)

    def _show_page(self, page_index: int) -> None:
        """Display a specific page within the current file.

        The ``page_index`` parameter refers to the page number within the
        currently selected PDF file.  This method handles scaling, zooming
        and cropping similarly to the original implementation, but now
        operates on ``self.files[self.current_file_index]['pages']``.
        It also updates the page/notice indicator and schedules the next
        file to display.
        """
        # Any call to show a page counts as user interaction
        self._mark_interaction()
        # Ensure there are files loaded
        if not self.files:
            return
        # Clamp current file index
        if self.current_file_index >= len(self.files):
            self.current_file_index = 0
        # Get pages of current file
        pages = self.files[self.current_file_index].get("pages", [])
        if not pages:
            return
        # Wrap page index around the number of pages in the current file
        page_index = page_index % len(pages)
        self.current_page_index = page_index
        img = pages[page_index]

        # Determine window dimensions
        win_w = self.root.winfo_width()
        # available height excludes the top bar and bottom row
        # Top frame height includes clock and page indicator; bottom frame holds thumbnails
        available_h = self.root.winfo_height() - self.top_frame.winfo_height() - self.bottom_frame.winfo_height() - 20
        if available_h <= 0:
            available_h = self.root.winfo_height()

        # Compute scaling factor based on fit mode
        img_w, img_h = img.width, img.height
        scale = 1.0
        if self.fit_mode == "fit_width":
            scale = win_w / img_w if img_w else 1.0
        elif self.fit_mode == "fit_height":
            scale = available_h / img_h if img_h else 1.0
        elif self.fit_mode == "fit_page":
            if img_w and img_h:
                scale = min(win_w / img_w, available_h / img_h)
        elif self.fit_mode == "actual_size":
            scale = 1.0
        # Apply zoom factor
        scale *= self.zoom
        # Ensure dimensions are at least 1 pixel
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        try:
            resized = img.resize((new_w, new_h), Image.LANCZOS)
        except Exception:
            resized = img
        # Determine display area dimensions
        display_w = win_w
        display_h = available_h
        # Reset offsets if zoom is default or image fits within display area
        if self.zoom <= 1.0 or (new_w <= display_w and new_h <= display_h):
            self.offset_x = 0
            self.offset_y = 0
        # Determine cropping bounds
        crop_left = 0
        crop_top = 0
        crop_right = new_w
        crop_bottom = new_h
        if new_w > display_w or new_h > display_h:
            max_x = max(0, new_w - display_w)
            max_y = max(0, new_h - display_h)
            try:
                self.offset_x = max(0, min(int(self.offset_x), max_x))
                self.offset_y = max(0, min(int(self.offset_y), max_y))
            except Exception:
                self.offset_x = 0
                self.offset_y = 0
            crop_left = self.offset_x
            crop_top = self.offset_y
            crop_right = crop_left + min(display_w, new_w)
            crop_bottom = crop_top + min(display_h, new_h)
            try:
                cropped = resized.crop((crop_left, crop_top, crop_right, crop_bottom))
            except Exception:
                cropped = resized
        else:
            cropped = resized
        # Convert to PhotoImage and update label
        photo = ImageTk.PhotoImage(cropped)
        self.image_label.config(image=photo)
        self.image_label.image = photo
        # Update notice indicator: display current file position (1-based)
        total_files = len(self.files)
        try:
            self.page_label.config(text=f"{self.current_file_index + 1} / {total_files}")
        except Exception:
            pass
        # We intentionally avoid updating the thumbnail highlight when
        # merely changing pages within the same file.  Updating
        # highlights (and thus enlarged thumbnails) on every page
        # change can cause the bottom carousel to appear to shake.
        # Cancel any pending rotation and schedule the next file
        if self.cycle_id:
            try:
                self.root.after_cancel(self.cycle_id)
            except Exception:
                pass
        # Schedule the next page (or file) after this one.  The next
        # transition will occur after ``cycle_interval`` seconds unless
        # paused.  When the current page is the last page of the file,
        # the scheduler will automatically advance to the next file.
        self._schedule_next_page()


    def _show_next_page(self, event=None) -> None:
        """
        Advance to the next page within the current file.

        When not at the last page of the current PDF, this method
        increments ``self.current_page_index`` and displays the next
        page.  When the current page is the last page, it advances to
        the next file (wrapping around if necessary) and displays its
        first page.  User interaction is marked to reset any idle
        timer.
        """
        # Mark user interaction
        self._mark_interaction()
        # Ensure there are files
        if not self.files:
            return
        # Get pages in current file
        if 0 <= self.current_file_index < len(self.files):
            pages = self.files[self.current_file_index].get("pages", [])
        else:
            pages = []
        # If there are pages in current file and we are not on last page
        if pages and (self.current_page_index + 1) < len(pages):
            self.current_page_index += 1
            self._show_page(self.current_page_index)
        else:
            # We are at the last page or no pages at all; move to next file
            self._show_next_file()

    def _show_previous_page(self, event=None) -> None:
        """Go back to the previous page."""
        # Mark user interaction
        self._mark_interaction()
        # Within the current file, move to the previous page (if any)
        pages = []
        if self.files and 0 <= self.current_file_index < len(self.files):
            pages = self.files[self.current_file_index].get("pages", [])
        if pages:
            self.current_page_index = (self.current_page_index - 1) % len(pages)
        else:
            self.current_page_index = 0
        self._show_page(self.current_page_index)

    def _toggle_pause(self, event=None) -> None:
        """Toggle pause/resume of the automatic page rotation."""
        # Mark user interaction
        self._mark_interaction()
        self.paused = not self.paused
        if self.paused and self.cycle_id:
            self.root.after_cancel(self.cycle_id)
        elif not self.paused:
            # When resuming, schedule the next page (or file) rather than
            # the next file only.  This ensures that pages within the
            # current file continue to display before advancing.
            self._schedule_next_page()
        # Update toolbar pause/play button text if present
        if hasattr(self, "pause_button"):
            self.pause_button.config(text="Play" if self.paused else "Pause")

    def _place_page_label(self) -> None:
        """Position the page indicator according to configuration."""
        try:
            self.page_label.place_forget()
        except Exception:
            pass
        # Choose top-right (ne) or bottom-right (se)
        if getattr(self, "page_indicator_pos", "top-right") == "bottom-right":
            self.page_label.place(relx=0.99, rely=0.99, anchor="se")
        else:
            self.page_label.place(relx=0.99, rely=0.01, anchor="ne")

    def _cycle_fit_mode(self, event=None) -> None:
        """Cycle through fit modes: fit_page → fit_width → fit_height → actual_size."""
        # Mark user interaction
        self._mark_interaction()
        modes = ["fit_page", "fit_width", "fit_height", "actual_size"]
        try:
            current_index = modes.index(self.fit_mode)
        except ValueError:
            current_index = 0
        self.fit_mode = modes[(current_index + 1) % len(modes)]
        # Redraw current page with new fit mode
        self._show_page(self.current_page_index)

    def _zoom_in(self, event=None) -> None:
        """Zoom in by zoom_step (up to 8x)."""
        # Mark user interaction
        self._mark_interaction()
        self.zoom = min(8.0, self.zoom + self.zoom_step)
        self._show_page(self.current_page_index)

    def _zoom_out(self, event=None) -> None:
        """Zoom out by zoom_step (down to 0.1x)."""
        # Mark user interaction
        self._mark_interaction()
        self.zoom = max(0.1, self.zoom - self.zoom_step)
        self._show_page(self.current_page_index)

    def _zoom_reset(self, event=None) -> None:
        """Reset zoom to default (1.0x)."""
        # Mark user interaction
        self._mark_interaction()
        self.zoom = 1.0
        self._show_page(self.current_page_index)

    def _reload_pdfs(self, event=None) -> None:
        """Reload all PDFs from the configured directory, refreshing the page list."""
        # Mark user interaction
        self._mark_interaction()
        # Cancel any scheduled page switch
        if self.cycle_id:
            try:
                self.root.after_cancel(self.cycle_id)
            except Exception:
                pass
        logging.info("Reloading PDFs from %s", PDF_DIR)
        try:
            new_pdf_files = find_pdf_files(PDF_DIR)
            if not new_pdf_files:
                logging.warning("No PDFs found during reload in %s", PDF_DIR)
            self.pdf_paths = new_pdf_files
            # Reset any existing pages/files and load files afresh
            self.files.clear()
            self._load_files()
            # Reset indices and offsets
            self.current_file_index = 0
            self.current_page_index = 0
            self.offset_x = 0
            self.offset_y = 0
            # Refresh thumbnails
            self._update_thumbnails()
            # Display the first notice
            self._show_page(0)
            # Schedule rotation if not paused.  Use page-based scheduling
            # so that pages within each PDF are shown sequentially before
            # moving to the next file.
            if not self.paused:
                self._schedule_next_page()
        except Exception as exc:
            logging.error("Error reloading PDFs: %s", exc)

    def _on_scroll(self, event) -> None:
        """
        Handle mouse wheel events for navigation and zoom.

        The behaviour of the wheel is controlled by ``self.wheel_mode``, which
        can be ``"navigate"`` (default) to move between pages or ``"zoom"`` to
        change the zoom level.  Holding the Shift key temporarily reverses the
        behaviour (navigate → zoom or zoom → navigate).  The scroll
        direction is determined from ``event.delta`` on Windows/macOS or
        ``event.num`` on Linux/X11.
        """
        # Mark user interaction (scrolling counts as interaction)
        self._mark_interaction()
        # Determine scroll delta (positive for wheel up, negative for wheel down)
        delta = getattr(event, "delta", 0)
        if delta == 0 and hasattr(event, "num"):
            # Linux: button 4 (num=4) is scroll up; button 5 (num=5) is scroll down
            if event.num == 4:
                delta = 120
            elif event.num == 5:
                delta = -120
        # Determine if Shift is held (bitmask 0x0001 typically indicates Shift)
        shift_pressed = bool(getattr(event, "state", 0) & 0x0001)
        # Decide the effective mode: wheel_mode or toggled if shift is pressed
        mode = self.wheel_mode
        if shift_pressed:
            mode = "zoom" if mode == "navigate" else "navigate"
        # Act based on mode and direction
        if mode == "zoom":
            if delta > 0:
                self._zoom_in()
            elif delta < 0:
                self._zoom_out()
        else:  # navigation
            # Navigate between files rather than pages
            if delta > 0:
                self._show_previous_file()
            elif delta < 0:
                self._show_next_file()

    # ------------------------------------------------------------------
    # Thumbnail highlighting and animation
    def _update_thumbnail_highlight(self) -> None:
        """
        Update the border highlighting of thumbnails based on the currently
        displayed file.  The active thumbnail has a coloured border
        defined by ``self.highlight_color`` while all others have no
        border.  This should be called whenever ``self.current_file_index``
        changes or when the thumbnail list is rebuilt.
        """
        # Determine total number of files to calculate neighbours.  We use
        # modulo arithmetic so that the first and last thumbnails can be
        # considered neighbours when wrapping around.
        total_files = len(self.files)
        # Identify the immediate neighbours of the current file
        if total_files > 0:
            left_idx = (self.current_file_index - 1) % total_files
            right_idx = (self.current_file_index + 1) % total_files
        else:
            left_idx = right_idx = -1
        for lbl in getattr(self, "thumbnail_labels", []):
            try:
                idx = getattr(lbl, "file_index", None)
            except Exception:
                idx = None
            # Selected thumbnail: enlarge and apply tinted overlay
            if idx is not None and idx == self.current_file_index:
                try:
                    lbl.config(
                        image=lbl.selected_enlarged_image,
                        highlightthickness=3,
                        highlightbackground=self.highlight_color,
                    )
                    lbl.image = lbl.selected_enlarged_image
                except Exception:
                    # Fallback to using the non‑enlarged tinted image
                    try:
                        lbl.config(
                            image=lbl.selected_image,
                            highlightthickness=3,
                            highlightbackground=self.highlight_color,
                        )
                        lbl.image = lbl.selected_image
                    except Exception:
                        lbl.config(highlightthickness=3, highlightbackground=self.highlight_color)
            # Immediate neighbours: slightly enlarge to create a subtle parallax
            elif idx is not None and (idx == left_idx or idx == right_idx):
                try:
                    lbl.config(
                        image=lbl.enlarged_image,
                        highlightthickness=1,
                        highlightbackground=self.background_color,
                    )
                    lbl.image = lbl.enlarged_image
                except Exception:
                    # Fallback to normal image
                    try:
                        lbl.config(
                            image=lbl.normal_image,
                            highlightthickness=1,
                            highlightbackground=self.background_color,
                        )
                        lbl.image = lbl.normal_image
                    except Exception:
                        lbl.config(highlightthickness=1, highlightbackground=self.background_color)
            else:
                # Other thumbnails: normal size, no highlight
                try:
                    lbl.config(
                        image=lbl.normal_image,
                        highlightthickness=0,
                        highlightbackground=self.background_color,
                    )
                    lbl.image = lbl.normal_image
                except Exception:
                    lbl.config(highlightthickness=0, highlightbackground=self.background_color)

    def _animate_thumbnail_selection(self, new_idx: int, step: int = 0) -> None:

        # We pulse the border thickness of the selected thumbnail while
        # leaving images unchanged.  On the first animation step, ensure
        # that the correct images (enlarged and tinted) are applied
        # according to the new selection.
        if step == 0:
            try:
                self._update_thumbnail_highlight()
            except Exception:
                pass
        # Define pulse steps only once
        if not hasattr(self, "_highlight_pulse_steps"):
            self._highlight_pulse_steps = [0, 1, 2, 3, 2, 1]
        steps = self._highlight_pulse_steps
        if step >= len(steps):
            # Finalize highlight state and exit
            try:
                self._update_thumbnail_highlight()
            except Exception:
                pass
            return
        thickness = steps[step]
        # Determine neighbours for consistent border handling
        total_files = len(self.files)
        if total_files > 0:
            left_idx = (new_idx - 1) % total_files
            right_idx = (new_idx + 1) % total_files
        else:
            left_idx = right_idx = -1
        for lbl in getattr(self, "thumbnail_labels", []):
            idx = getattr(lbl, "file_index", None)
            if idx is None:
                continue
            if idx == new_idx:
                # Pulse border on the selected thumbnail
                try:
                    lbl.config(highlightthickness=thickness, highlightbackground=self.highlight_color)
                except Exception:
                    pass
            elif idx == left_idx or idx == right_idx:
                # Neighbour thumbnails have a thin border to subtly separate
                try:
                    lbl.config(highlightthickness=1, highlightbackground=self.background_color)
                except Exception:
                    pass
            else:
                # All other thumbnails have no border
                try:
                    lbl.config(highlightthickness=0, highlightbackground=self.background_color)
                except Exception:
                    pass
        # Schedule the next pulse step
        self.root.after(80, lambda: self._animate_thumbnail_selection(new_idx, step + 1))

    # ------------------------------------------------------------------
    # Carousel scrolling
    def _scroll_to_index(self, index: int, animate: bool = True) -> None:
        """
        Scroll the thumbnails carousel so that the thumbnail corresponding to
        ``index`` is visible (ideally centered) within the canvas.  If
        there are fewer thumbnails than the number of visible slots,
        scrolling is not needed.  The ``animate`` flag determines
        whether the movement should be animated.

        :param index: Index of the file in ``self.files`` to bring into view
        :param animate: If True, animate the scroll; otherwise jump
        directly to the target position.
        """
        try:
            total_files = len(self.files)
            visible_count = int(getattr(self, "thumbnails_count", 1))
        except Exception:
            return
        if total_files <= visible_count or visible_count <= 0:
            # No need to scroll
            return

        try:
            thumb_width = getattr(self, "thumbnail_width", None)
            if thumb_width is None and self.thumbnail_labels:
                # Use first thumbnail image width plus horizontal padding
                first_lbl = self.thumbnail_labels[0]
                # width from the associated PIL image stored in normal_image
                # but PhotoImage width might be stored in normal_image.width()
                thumb_width = first_lbl.normal_image.width() + 10
                self.thumbnail_width = thumb_width
            elif thumb_width is None:
                # Fallback guess: use thumbnail_height as width
                thumb_width = self.thumbnail_height + 10
                self.thumbnail_width = thumb_width
            else:
                thumb_width = thumb_width
        except Exception:
            # Fallback: use thumbnail_height + padding
            thumb_width = int(getattr(self, "thumbnail_height", 100)) + 10
            self.thumbnail_width = thumb_width
        # Determine start index so that selected index is near the center
        # of the visible window.  Ensure start_index is within bounds.
        try:
            half = visible_count // 2
            start_index = index - half
            if start_index < 0:
                start_index = 0
            max_start = total_files - visible_count
            if start_index > max_start:
                start_index = max_start
        except Exception:
            start_index = 0
        # Compute target pixel position of the start of the visible window
        try:
            target_pixel = start_index * thumb_width
        except Exception:
            target_pixel = 0
        # Ensure layout is updated to compute sizes
        try:
            self.thumbnails_container.update_idletasks()
            content_width = self.thumbnails_container.winfo_width()
            visible_width = self.thumbnails_canvas.winfo_width()
        except Exception:
            return
        try:
            if content_width <= visible_width or content_width <= 0:
                fraction = 0.0
            else:
                # The scrollable width is content_width - visible_width
                scrollable_width = max(content_width - visible_width, 1)
                fraction = max(0.0, min(target_pixel / scrollable_width, 1.0))
        except Exception:
            fraction = 0.0
        if not animate:
            try:
                self.thumbnails_canvas.xview_moveto(fraction)
                self.current_scroll_fraction = fraction
            except Exception:
                pass
            return
        # Animate from current position to target fraction
        try:
            current = getattr(self, "current_scroll_fraction", None)
            if current is None:
                try:
                    # Query current fraction from canvas xview
                    current = self.thumbnails_canvas.xview()[0]
                except Exception:
                    current = 0.0
            # Clamp values between 0 and 1
            current = max(0.0, min(current, 1.0))
            target = max(0.0, min(fraction, 1.0))
            # If difference is small, jump directly
            if abs(target - current) < 0.001:
                try:
                    self.thumbnails_canvas.xview_moveto(target)
                    self.current_scroll_fraction = target
                except Exception:
                    pass
                return
            # Determine number of steps for smooth animation
            steps = 10
            delta = (target - current) / float(steps)
            # Closure for incremental updates
            def _animate_step(step_count: int, current_val: float) -> None:
                new_val = current_val + delta
                # Move canvas to new position
                try:
                    self.thumbnails_canvas.xview_moveto(new_val)
                    self.current_scroll_fraction = new_val
                except Exception:
                    pass
                # Schedule next step or finish
                if step_count < steps - 1:
                    self.root.after(40, lambda: _animate_step(step_count + 1, new_val))
                else:
                    # Final position
                    try:
                        self.thumbnails_canvas.xview_moveto(target)
                        self.current_scroll_fraction = target
                    except Exception:
                        pass
            # Start animation
            _animate_step(0, current)
        except Exception:
            # Fallback: jump to final
            try:
                self.thumbnails_canvas.xview_moveto(fraction)
                self.current_scroll_fraction = fraction
            except Exception:
                pass

    def _select_file(self, index: int) -> None:
        """
        Display the specified file by index.

        When a thumbnail in the bottom row is clicked, this method is
        invoked to switch the main display to that PDF's first page.
        It resets the page index, panning offsets and schedules the
        rotation as usual.  Indexes that are out of range are ignored.
        """
        # Mark user interaction for idle timeout handling
        self._mark_interaction()
        if not self.files:
            return
        try:
            index = int(index)
        except Exception:
            return
        if index < 0 or index >= len(self.files):
            return
        self.current_file_index = index
        self.current_page_index = 0
        self.offset_x = 0
        self.offset_y = 0
        # Scroll the carousel to bring the selected file into view
        try:
            self._scroll_to_index(self.current_file_index, animate=self.scroll_animation)
        except Exception:
            pass
        # Trigger an animation on the thumbnails
        try:
            self._animate_thumbnail_selection(self.current_file_index)
        except Exception:
            try:
                self._update_thumbnail_highlight()
            except Exception:
                pass
        # Show first page of selected file
        self._show_page(0)

    def run(self) -> None:
        """Display the first page and start Tkinter main loop."""
        self._show_page(0)
        self.root.mainloop()

    # ------------------------------------------------------------------
    # User interaction and idle handling
    def _mark_interaction(self) -> None:
        """Record the time of the most recent user interaction and hide idle overlay."""
        self.last_interaction_time = time.time()
        # Hide the idle overlay if it is currently displayed
        try:
            if hasattr(self, "idle_overlay"):
                self.idle_overlay.place_forget()
        except Exception:
            pass
        # If paused due to idle state, resume automatically
        # Only resume if we didn't manually pause
        if getattr(self, "idle_paused", False):
            self.idle_paused = False
            if self.paused:
                # Use _toggle_pause to unpause
                self._toggle_pause()

    def _check_idle(self) -> None:
        """Periodically check for idle timeout and show screensaver if needed."""
        # Only operate when idle_timeout is set
        try:
            timeout = int(self.idle_timeout)
        except Exception:
            timeout = 0
        if timeout and timeout > 0:
            now = time.time()
            if now - self.last_interaction_time >= timeout:
                # Trigger idle screensaver if not already active
                if not getattr(self, "idle_paused", False):
                    # Pause rotation if not already paused
                    if not self.paused:
                        self._toggle_pause()
                    self.idle_paused = True
                    # Display overlay
                    try:
                        self.idle_overlay.config(text=self.idle_overlay_text)
                        self.idle_overlay.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)
                    except Exception:
                        pass
            # Schedule next check in 1 second
            self.root.after(1000, self._check_idle)
        else:
            # If idle timeout disabled, do nothing
            pass

    def _update_thumbnails(self) -> None:

        # Destroy existing thumbnail labels and clear the container
        for lbl in getattr(self, "thumbnail_labels", []):
            try:
                lbl.destroy()
            except Exception:
                pass
        self.thumbnail_labels = []
        # Remove any existing widgets from the thumbnails container (safety)
        try:
            for child in self.thumbnails_container.winfo_children():
                child.destroy()
        except Exception:
            pass

        total_files = len(self.files)
        if total_files == 0:
            return

        max_w = 0
        max_h = 0
        for file_info in self.files:
            thumb_enl = file_info.get("thumbnail_enlarged")
            # Use enlarged version if present, otherwise fall back to normal
            if thumb_enl is not None:
                try:
                    w, h = thumb_enl.size
                except Exception:
                    w = thumb_enl.width
                    h = thumb_enl.height
            else:
                thumb_img = file_info.get("thumbnail")
                if thumb_img is None:
                    continue
                try:
                    w, h = thumb_img.size
                except Exception:
                    w = thumb_img.width
                    h = thumb_img.height
            if w > max_w:
                max_w = w
            if h > max_h:
                max_h = h
        # Add a small padding to height to separate the thumbnails from the bottom
        if max_h:
            max_h_with_pad = max_h + 10
        else:
            max_h_with_pad = 0

        for idx, file_info in enumerate(self.files):
            thumb_img = file_info.get("thumbnail")
            thumb_sel_img = file_info.get("thumbnail_selected")
            thumb_enlarged = file_info.get("thumbnail_enlarged")
            thumb_sel_enlarged = file_info.get("thumbnail_selected_enlarged")
            if thumb_img is None or thumb_sel_img is None:
                continue
            try:
                tk_img_normal = ImageTk.PhotoImage(thumb_img)
                tk_img_selected = ImageTk.PhotoImage(thumb_sel_img)

                if thumb_enlarged is not None:
                    tk_img_enlarged = ImageTk.PhotoImage(thumb_enlarged)
                else:
                    tk_img_enlarged = tk_img_normal
                if thumb_sel_enlarged is not None:
                    tk_img_sel_enlarged = ImageTk.PhotoImage(thumb_sel_enlarged)
                else:
                    tk_img_sel_enlarged = tk_img_selected
            except Exception:
                # Skip this file if image conversion fails
                continue
            lbl = tk.Label(
                self.thumbnails_container,
                image=tk_img_normal,
                bg=self.background_color,
                cursor="hand2",
                highlightthickness=0,
                highlightbackground=self.background_color,
                anchor="s",  # anchor image at bottom within the fixed-size label
                width=max_w,
                height=max_h,
            )
            # Prevent the label from shrinking/growing to its image size.
            lbl.pack_propagate(False)
            # Store different image variants on the label for quick switching
            lbl.normal_image = tk_img_normal
            lbl.selected_image = tk_img_selected
            lbl.enlarged_image = tk_img_enlarged
            lbl.selected_enlarged_image = tk_img_sel_enlarged
            lbl.image = tk_img_normal
            try:
                file_index = idx
            except Exception:
                file_index = 0
            lbl.file_index = file_index
            lbl.bind("<Button-1>", lambda event, idx=file_index: self._select_file(idx))
            lbl.pack(side="left", anchor="s", padx=5, pady=0)
            self.thumbnail_labels.append(lbl)
        # Update canvas height to accommodate the fixed thumbnail height
        try:
            self.thumbnails_container.update_idletasks()
            # Set the canvas height to the precomputed maximum thumbnail height plus padding
            if max_h_with_pad:
                self.thumbnails_canvas.config(height=max_h_with_pad)
            content_width = self.thumbnails_container.winfo_width()
            content_height = self.thumbnails_container.winfo_height()
            self.thumbnails_canvas.configure(scrollregion=(0, 0, content_width, content_height))
        except Exception:
            pass
        # After repopulating, update images and highlights to reflect
        # the current file selection
        try:
            self._update_thumbnail_highlight()
        except Exception:
            pass
        # Ensure the selected file is visible in the carousel
        try:
            self._scroll_to_index(self.current_file_index, animate=self.scroll_animation)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Panning controls
    def _pan_left(self, event=None) -> None:
        """Pan left by pan_step pixels if the image is zoomed beyond the display area."""
        # Mark user interaction
        self._mark_interaction()
        # Only pan when the zoomed image is wider than the display area
        # Negative pan moves left (decreasing offset_x)
        try:
            self.offset_x -= self.pan_step
        except Exception:
            self.offset_x = 0
        self._show_page(self.current_page_index)

    def _pan_right(self, event=None) -> None:
        """Pan right by pan_step pixels."""
        # Mark user interaction
        self._mark_interaction()
        try:
            self.offset_x += self.pan_step
        except Exception:
            self.offset_x = 0
        self._show_page(self.current_page_index)

    def _pan_up(self, event=None) -> None:
        """Pan up by pan_step pixels."""
        # Mark user interaction
        self._mark_interaction()
        try:
            self.offset_y -= self.pan_step
        except Exception:
            self.offset_y = 0
        self._show_page(self.current_page_index)

    def _pan_down(self, event=None) -> None:
        """Pan down by pan_step pixels."""
        # Mark user interaction
        self._mark_interaction()
        try:
            self.offset_y += self.pan_step
        except Exception:
            self.offset_y = 0
        self._show_page(self.current_page_index)


def find_pdf_files(directory: Path) -> list:
    """
    Recursively find PDF files in the given directory.

    This function performs a case‑insensitive check on the file suffix so
    that files with ``.PDF`` or mixed case extensions are detected as well.
    """
    pdfs: list[Path] = []
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            pdfs.append(path)
    pdfs.sort(key=lambda p: p.name.lower())
    return pdfs


def main(argv=None) -> None:
    pdf_files = find_pdf_files(PDF_DIR)
    if not pdf_files:
        print(f"No PDFs found in {PDF_DIR}. Please add your notice PDFs and restart.")
        return
    root = tk.Tk()
    board = DigitalNoticeboard(root, pdf_files, cycle_interval=10)
    board.run()


if __name__ == "__main__":
    main(sys.argv[1:])