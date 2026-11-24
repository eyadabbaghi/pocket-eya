"""Eya Buddy: an anti-aliased floating character with a tiny chat drawer."""

from __future__ import annotations

import math
import random
import tkinter as tk
import ctypes
import time
from tkinter import font as tkfont
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError as exc:
    raise SystemExit(
        "Eya Buddy needs Pillow. Install it with: py -m pip install --user -r requirements.txt"
    ) from exc

from eya_buddy_chat import ChatReply, EyaChatEngine


SCALE = 3
SIZE = 180
KEY = "#010203"
INK = "#453b50"
PURPLE = "#8868ad"
LILAC = "#f1e9f7"
PINK = "#f2a2b3"


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]


def s(value: float) -> int:
    return round(value * SCALE)


class EyaBuddy:
    """A small transparent desktop character with a separate chat bubble."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Eya Buddy")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        root.resizable(False, False)

        self.mood = "happy"
        self.phase = 0.0
        self.blink_until = 0.0
        self.talk_until = 0.0
        self.chat_engine = EyaChatEngine()
        self.chat_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eya-reply")
        self.chat_results: Queue[tuple[int, Future[ChatReply]]] = Queue()
        self.reply_number = 0
        self.latest_reply_number = 0
        self.pending_message_indices: dict[int, int] = {}
        self.conversation: list[dict[str, str | int]] = []
        self.message_canvas: tk.Canvas | None = None
        self.status_canvas: tk.Canvas | None = None
        self.status_text_item: int | None = None
        self.message_frame: tk.Frame | None = None
        self.message_font: tkfont.Font | None = None
        self.popup_stage = "hidden"
        self.last_popup_stage = "greeting"
        self.greeting_window: tk.Toplevel | None = None
        self.press: tuple[int, int, int, int] | None = None
        self.drawer: tk.Toplevel | None = None
        self.drawer_visible = False
        self.closing = False
        self.sprite: ImageTk.PhotoImage | None = None
        self.layered = False
        self._memory_dc = None
        self._bitmap = None
        self._bitmap_bits = ctypes.c_void_p()

        self.canvas = tk.Canvas(root, width=SIZE, height=SIZE, bg=KEY,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        root.update_idletasks()
        x = root.winfo_screenwidth() - 205
        y = max(35, (root.winfo_screenheight() - 220) // 2)
        root.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
        root.update()
        self._setup_layered_window()
        self._animate()
        root.after(700, self._schedule_blink)
        root.after(180, self._show_greeting)
        root.after(100, self._poll_chat_results)

    def _setup_layered_window(self) -> None:
        """Enable real per-pixel alpha on Windows to avoid color-key fringes."""
        if not hasattr(ctypes, "windll"):
            return
        try:
            self._user32 = ctypes.windll.user32
            self._gdi32 = ctypes.windll.gdi32
            self._user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self._user32.GetWindowLongW.restype = ctypes.c_long
            self._user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            self._user32.SetWindowLongW.restype = ctypes.c_long
            self._user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            self._user32.SetWindowPos.restype = ctypes.c_int
            self._user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            self._user32.GetAncestor.restype = ctypes.c_void_p
            self._user32.GetDC.argtypes = [ctypes.c_void_p]
            self._user32.GetDC.restype = ctypes.c_void_p
            self._user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self._user32.ReleaseDC.restype = ctypes.c_int
            self._user32.UpdateLayeredWindow.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_POINT),
                ctypes.POINTER(_SIZE), ctypes.c_void_p, ctypes.POINTER(_POINT),
                ctypes.c_uint32, ctypes.POINTER(_BLENDFUNCTION), ctypes.c_uint32,
            ]
            self._user32.UpdateLayeredWindow.restype = ctypes.c_int
            self._gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            self._gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
            self._gdi32.CreateDIBSection.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint,
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32,
            ]
            self._gdi32.CreateDIBSection.restype = ctypes.c_void_p
            self._gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self._gdi32.SelectObject.restype = ctypes.c_void_p

            child_hwnd = ctypes.c_void_p(self.root.winfo_id())
            hwnd = self._user32.GetAncestor(child_hwnd, 2) or child_hwnd
            self._hwnd = hwnd
            ex_style = self._user32.GetWindowLongW(hwnd, -20)
            self._base_ex_style = ex_style
            self._user32.SetWindowLongW(hwnd, -20, ex_style | 0x00080000)  # WS_EX_LAYERED
            self._user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)

            screen_dc = self._user32.GetDC(None)
            self._memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
            info = _BITMAPINFO()
            info.bmiHeader = _BITMAPINFOHEADER(
                ctypes.sizeof(_BITMAPINFOHEADER), SIZE, -SIZE, 1, 32, 0, 0, 0, 0, 0, 0
            )
            self._bitmap = self._gdi32.CreateDIBSection(
                screen_dc, ctypes.byref(info), 0, ctypes.byref(self._bitmap_bits), None, 0
            )
            self._gdi32.SelectObject(self._memory_dc, self._bitmap)
            self._user32.ReleaseDC(None, screen_dc)
            self.layered = bool(self._bitmap and self._bitmap_bits.value)
        except (AttributeError, OSError, TypeError, ValueError):
            self.layered = False
        if not self.layered:
            # Fallback for Windows builds without layered-window support.
            try:
                self.root.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass

    def _show_layered_frame(self, frame: Image.Image) -> None:
        """Copy premultiplied BGRA pixels into a Windows alpha layered window."""
        if not self.layered:
            return
        premultiplied = frame.convert("RGBa")
        pixels = premultiplied.tobytes("raw", "BGRa")
        ctypes.memmove(self._bitmap_bits, pixels, len(pixels))
        screen_dc = self._user32.GetDC(None)
        destination = _POINT(self.root.winfo_x(), self.root.winfo_y())
        source = _POINT(0, 0)
        size = _SIZE(SIZE, SIZE)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        self._last_layered_update_ok = bool(self._user32.UpdateLayeredWindow(
            self._hwnd, screen_dc, ctypes.byref(destination),
            ctypes.byref(size), self._memory_dc, ctypes.byref(source),
            0, ctypes.byref(blend), 2,  # ULW_ALPHA
        ))
        self._user32.ReleaseDC(None, screen_dc)
        if not self._last_layered_update_ok:
            return

    def _fallback_to_colorkey(self, frame: Image.Image) -> None:
        """Keep the character visible if Windows refuses alpha-layer updates."""
        try:
            self._user32.SetWindowLongW(self._hwnd, -20, self._base_ex_style)
            self._user32.SetWindowPos(self._hwnd, None, 0, 0, 0, 0, 0x0027)
            self.root.attributes("-transparentcolor", KEY)
        except (AttributeError, OSError, tk.TclError):
            pass
        self.layered = False
        self.sprite = ImageTk.PhotoImage(frame)
        self.canvas.delete("buddy")
        self.canvas.create_image(0, 0, anchor="nw", image=self.sprite, tags="buddy")

    def _paint_character(self, blink: bool) -> Image.Image:
        """Draw at 3x resolution and downsample for crisp smooth edges."""
        dim = s(SIZE)
        image = Image.new("RGBA", (dim, dim), (1, 2, 3, 0))
        draw = ImageDraw.Draw(image)
        bob_amount = {"happy": 3.2, "excited": 4.0, "playful": 4.2, "sleepy": 1.0,
                      "focused": 0.65, "stressed": 1.15, "curious": 1.7}.get(self.mood, 2.0)
        bob_rate = 0.55 if self.mood in ("sleepy", "stressed") else 1.0
        bob = math.sin(self.phase * bob_rate) * bob_amount
        arm_amount = {"happy": 3.0, "excited": 3.8, "playful": 4.0, "sleepy": 0.8,
                      "focused": 0.5, "stressed": 1.0, "curious": 2.0}.get(self.mood, 2.0)
        arm_sway = math.sin(self.phase * 1.35) * arm_amount
        breathing = 0.5 + 0.5 * math.sin(self.phase * 0.45)

        def box(coords: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
            x1, y1, x2, y2 = coords
            return s(x1), s(y1 + bob), s(x2), s(y2 + bob)

        # Diffused grounding shadow behind the hovering bot.
        glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        glow_alpha = {"happy": 52, "excited": 62, "playful": 60, "sleepy": 25,
                      "focused": 24, "stressed": int(20 + breathing * 14),
                      "curious": 38}.get(self.mood, 32)
        glow_color = {
            "happy": (255, 145, 194), "excited": (255, 125, 210),
            "playful": (255, 118, 201),
            "sleepy": (177, 160, 222), "focused": (167, 154, 235),
            "stressed": (181, 162, 224), "curious": (191, 150, 247),
        }.get(self.mood, (220, 177, 231))
        gd.ellipse(box((45, 47, 135, 146)), fill=(*glow_color, glow_alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(s(12)))
        image.alpha_composite(glow)
        draw = ImageDraw.Draw(image)
        draw.ellipse(box((53, 158, 127, 168)), fill=(54, 43, 66, 28))

        # Little hovering arms, drawn behind the shell. Sway is gentle and offset.
        for side in (-1, 1):
            shoulder_x = 49 if side < 0 else 131
            elbow_x = 34 if side < 0 else 146
            hand_x = 27 if side < 0 else 153
            offset = -arm_sway if side < 0 else arm_sway
            shoulder_y = 101 + bob
            elbow_y = 95 + bob + offset
            hand_y = 109 + bob + offset * 1.25
            draw.line((s(shoulder_x), s(shoulder_y), s(elbow_x), s(elbow_y),
                       s(hand_x), s(hand_y)), fill="#b7a2ce", width=s(8), joint="curve")
            draw.line((s(shoulder_x), s(shoulder_y-1), s(elbow_x), s(elbow_y-1),
                       s(hand_x), s(hand_y-1)), fill="#fffdfd", width=s(4), joint="curve")
            draw.ellipse((s(elbow_x-4), s(elbow_y-4), s(elbow_x+4), s(elbow_y+4)),
                         fill="#d7c8e5", outline="#ad97c2", width=s(1))
            draw.ellipse((s(hand_x-5), s(hand_y-5), s(hand_x+5), s(hand_y+5)),
                         fill="#f8effb", outline="#b39dc7", width=s(1))

        # Floating antenna and softly shaded, rounded shell.
        draw.line((s(90), s(49+bob), s(90), s(34+bob)), fill="#aa93c2", width=s(4))
        draw.ellipse((s(85), s(24+bob), s(95), s(34+bob)), fill="#f5c5d6")
        draw.ellipse((s(87), s(26+bob), s(93), s(32+bob)), fill="#ffeff5")
        draw.rounded_rectangle(box((45, 47, 135, 150)), radius=s(31),
                               fill="#d7c9e5", outline="#a891be", width=s(1.5))
        draw.rounded_rectangle(box((48, 46, 132, 145)), radius=s(29),
                               fill="#fffdfb", outline="#ffffff", width=s(2))
        # Soft shell highlight and lower pastel chin panel.
        draw.arc(box((54, 49, 126, 119)), start=195, end=335,
                 fill="#ffffff", width=s(3))
        draw.rounded_rectangle(box((62, 128, 118, 139)), radius=s(5),
                               fill="#eee6f5", outline=None)
        draw.ellipse(box((84, 131, 96, 136)), fill="#d3c2e4")

        # Dark, glossy face screen with a subtle glass highlight.
        screen = box((55, 66, 125, 119))
        draw.rounded_rectangle(screen, radius=s(17), fill="#30283f",
                               outline="#74627f", width=s(1.5))
        draw.arc(box((61, 68, 119, 93)), 205, 330, fill="#55475f", width=s(1.5))

        # Diffused pink and violet light sits underneath the crisp LEDs.
        talking = time.monotonic() < self.talk_until
        eye_motion = math.sin(self.phase * 3.2) * 0.8 if talking else 0
        eye_y = 81 + bob + eye_motion
        eye_centers = (73, 107)
        light = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(light)
        if not blink and self.mood != "sleepy":
            for index, cx in enumerate(eye_centers):
                radius = 12 if self.mood == "excited" else 9
                power = 125 if self.mood == "stressed" else (150 if self.mood == "focused" else 210)
                if self.mood == "curious":
                    color = (189, 145, 255, 185)
                else:
                    color = (255, 105, 174, power) if index == 0 else (174, 117, 255, power)
                ld.ellipse((s(cx-radius), s(eye_y-3), s(cx+radius), s(eye_y+radius+4)),
                          fill=color)
            ld.ellipse(box((83, 101, 97, 112)), fill=(242, 95, 183, 155))
        light = light.filter(ImageFilter.GaussianBlur(s(6)))
        image.alpha_composite(light)
        draw = ImageDraw.Draw(image)

        # LED eye shapes: round, luminous, and easy to read at widget size.
        for index, cx in enumerate(eye_centers):
            color = "#ff9ac9" if index == 0 else "#c7a0ff"
            if blink:
                draw.arc((s(cx-8), s(eye_y-3), s(cx+8), s(eye_y+9)),
                         195, 345, fill=color, width=s(2.7))
            elif self.mood == "sleepy":
                draw.arc((s(cx-8), s(eye_y), s(cx+8), s(eye_y+13)),
                         195, 335, fill="#d59dbd", width=s(2.4))
            elif self.mood == "focused":
                draw.rounded_rectangle((s(cx-6), s(eye_y+3), s(cx+6), s(eye_y+10)),
                                       radius=s(3), fill=color)
                draw.line((s(cx-3), s(eye_y+5), s(cx+3), s(eye_y+5)),
                          fill="#fff4fc", width=s(1))
            elif self.mood == "stressed":
                draw.ellipse((s(cx-4), s(eye_y+2), s(cx+4), s(eye_y+11)), fill=color)
                draw.ellipse((s(cx-1), s(eye_y+4), s(cx+1), s(eye_y+6)), fill="#fff4fc")
            elif self.mood == "playful" and index == 0:
                draw.arc((s(cx-8), s(eye_y-2), s(cx+8), s(eye_y+12)),
                         195, 345, fill="#ff9ac9", width=s(2.8))
            elif self.mood == "curious":
                radius = 7 if index == 0 else 5
                draw.ellipse((s(cx-radius), s(eye_y+1), s(cx+radius), s(eye_y+1+radius*1.6)), fill="#c9a5ff")
                draw.ellipse((s(cx-2), s(eye_y+3), s(cx+1), s(eye_y+6)), fill="#fff4fc")
            elif self.mood == "excited":
                draw.polygon([(s(cx), s(eye_y-3)), (s(cx+3), s(eye_y+1)),
                              (s(cx+7), s(eye_y+2)), (s(cx+3), s(eye_y+5)),
                              (s(cx+4), s(eye_y+10)), (s(cx), s(eye_y+7)),
                              (s(cx-4), s(eye_y+10)), (s(cx-3), s(eye_y+5)),
                              (s(cx-7), s(eye_y+2)), (s(cx-3), s(eye_y+1))],
                             fill=color)
            else:
                eye_h = 15 if self.mood == "curious" else 12
                eye_w = 10 if self.mood == "curious" else 8
                draw.rounded_rectangle((s(cx-eye_w/2), s(eye_y), s(cx+eye_w/2),
                                        s(eye_y+eye_h)), radius=s(eye_w/2), fill=color)
                draw.ellipse((s(cx-2), s(eye_y+2), s(cx+1), s(eye_y+5)), fill="#fff4fc")
                if self.mood == "happy":
                    draw.arc((s(cx-5), s(eye_y+5), s(cx+5), s(eye_y+12)),
                             190, 345, fill="#fff4fc", width=s(1.1))

        if self.mood == "stressed":
            draw.line((s(65), s(eye_y-5), s(72), s(eye_y-2)), fill="#e6b6d0", width=s(1.5))
            draw.line((s(108), s(eye_y-2), s(115), s(eye_y-5)), fill="#e6b6d0", width=s(1.5))
        elif self.mood == "focused":
            draw.line((s(66), s(eye_y-3), s(79), s(eye_y-3)), fill="#a992c0", width=s(1.5))
            draw.line((s(101), s(eye_y-3), s(114), s(eye_y-3)), fill="#a992c0", width=s(1.5))
        elif self.mood == "curious":
            draw.arc((s(64), s(eye_y-9), s(82), s(eye_y-1)), 190, 345,
                     fill="#c6a1ef", width=s(1.7))

        # Glowing mouth changes shape with each mood.
        # Pillow arc orientation is easy to misread; draw happy smiles as a
        # filled curved polygon so their corners are visibly above the center.
        mouth_glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        mg = ImageDraw.Draw(mouth_glow)
        if self.mood in ("excited", "curious") or (talking and math.sin(self.phase * 3.5) > 0.05):
            mg.ellipse(box((83, 101, 97, 114)), fill=(242, 95, 183, 190))
        elif self.mood == "sleepy":
            mg.ellipse(box((84, 101, 96, 114)), fill=(205, 140, 179, 170))
        elif self.mood == "playful":
            mg.arc(box((81, 98, 99, 114)), 200, 340,
                   fill=(255, 118, 201, 210), width=s(3))
        else:
            if self.mood == "stressed":
                mg.arc(box((82, 98, 98, 113)), 20, 160,
                       fill=(242, 155, 189, 200), width=s(3))
            else:
                mg.line((s(83), s(106), s(87), s(110), s(91), s(111),
                         s(95), s(110), s(99), s(106)),
                        fill=(242, 95, 183, 200), width=s(3), joint="curve")
        image.alpha_composite(mouth_glow.filter(ImageFilter.GaussianBlur(s(4))))
        draw = ImageDraw.Draw(image)
        if self.mood in ("excited", "curious") or (talking and math.sin(self.phase * 3.5) > 0.05):
            draw.ellipse(box((84, 101, 96, 113)), fill="#ff9dce")
            if self.mood == "excited":
                draw.arc(box((86, 102, 94, 108)), 190, 345, fill="#fff2fa", width=s(1))
        elif self.mood == "sleepy":
            # A small soft yawn that gently opens and closes.
            yawn = 10 + (2 if math.sin(self.phase * 1.6) > 0.4 else 0)
            draw.ellipse(box((90-yawn/2, 102, 90+yawn/2, 111)), fill="#e991b9")
        elif self.mood == "playful":
            draw.arc(box((81, 98, 99, 114)), 200, 340, fill="#ff9dce", width=s(3))
            draw.ellipse(box((96, 105, 99, 108)), fill="#fff2fa")
        elif self.mood == "stressed":
            draw.arc(box((82, 100, 98, 114)), 20, 160, fill="#ed9abd", width=s(2.3))
        else:
            draw.line((s(83), s(106), s(87), s(110), s(91), s(111),
                       s(95), s(110), s(99), s(106)),
                      fill="#ff9dce", width=s(2.8), joint="curve")

        # Tiny status LEDs on the shell.
        draw.ellipse(box((55, 125, 59, 129)), fill="#f3abc7")
        draw.ellipse(box((121, 125, 125, 129)), fill="#c4a1eb")
        # Resize in premultiplied-alpha space so transparent pixels never pull
        # a dark matte into the antialiased shell and shadow edges.
        return image.convert("RGBa").resize((SIZE, SIZE), Image.Resampling.LANCZOS).convert("RGBA")

    def _animate(self) -> None:
        if not self.root.winfo_exists():
            return
        speed = {"happy": 0.075, "excited": 0.09, "playful": 0.095, "sleepy": 0.027,
                 "focused": 0.038, "stressed": 0.032}.get(self.mood, 0.06)
        self.phase += speed
        blinking = self.phase < self.blink_until
        frame = self._paint_character(blinking)
        if self.layered:
            try:
                self._show_layered_frame(frame)
            except (OSError, TypeError, ValueError, ctypes.ArgumentError):
                self._last_layered_update_ok = False
            if not self._last_layered_update_ok:
                self._fallback_to_colorkey(frame)
        else:
            self.sprite = ImageTk.PhotoImage(frame)
            self.canvas.delete("buddy")
            self.canvas.create_image(0, 0, anchor="nw", image=self.sprite, tags="buddy")
            self.canvas.tag_lower("buddy")
        self.root.after(34, self._animate)

    def _schedule_blink(self) -> None:
        self.blink_until = self.phase + 0.2
        self.root.after(random.randint(2500, 4300), self._schedule_blink)

    def _press(self, event: tk.Event) -> None:
        self.press = (event.x_root, event.y_root,
                      event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())

    def _move(self, event: tk.Event) -> None:
        if self.press is None:
            return
        _, _, dx, dy = self.press
        x, y = event.x_root - dx, event.y_root - dy
        self.root.geometry(f"+{x}+{y}")
        self._place_drawer()
        self._place_greeting()

    def _release(self, event: tk.Event) -> None:
        if self.press is None:
            return
        start_x, start_y, _, _ = self.press
        moved = abs(event.x_root - start_x) + abs(event.y_root - start_y) > 7
        self.press = None
        if not moved:
            self._on_robot_click()

    def _on_robot_click(self) -> None:
        if self.popup_stage == "transitioning":
            return
        if self.popup_stage == "greeting":
            self._dismiss_greeting(open_chat=False)
        elif self.popup_stage == "chat":
            self.toggle_drawer()
        elif self.last_popup_stage == "chat":
            self._make_drawer()
        else:
            self._show_greeting()

    def _show_greeting(self) -> None:
        if self.popup_stage not in ("hidden", "greeting"):
            return
        if self.greeting_window and self.greeting_window.winfo_exists():
            self.greeting_window.destroy()
        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.attributes("-alpha", 0.0)
        bubble.configure(bg="#f8f3fa")
        bubble.geometry("292x164+0+0")
        self.greeting_window = bubble
        self.popup_stage = "greeting"
        self.last_popup_stage = "greeting"

        c = tk.Canvas(bubble, width=292, height=164, bg="#f8f3fa", highlightthickness=0)
        c.pack()
        self._rounded(c, 7, 7, 285, 157, 24, fill="#e8dfea", outline="")
        self._rounded(c, 5, 4, 283, 153, 24, fill="#fffdfc", outline="#eee5ef", width=1)
        c.create_oval(20, 18, 54, 52, fill="#f2e8f8", outline="")
        c.create_text(37, 35, text="*", fill="#a17bb8", font=("Segoe UI", 17, "bold"))
        c.create_text(68, 29, anchor="w", text="A little hello", fill=INK,
                      font=("Segoe UI", 11, "bold"))
        c.create_text(25, 75, anchor="w", text="Hi Eya! How are you?\nWanna talk?",
                      fill="#554a60", font=("Segoe UI", 12, "bold"), width=244, justify="left")

        yes_shape = self._rounded(c, 20, 111, 156, 143, 15, fill="#8a68ae", outline="", tags="yes")
        yes_text = c.create_text(88, 127, text="Yes ✨", fill="white",
                                 font=("Segoe UI", 9, "bold"), tags="yes")
        later_shape = self._rounded(c, 166, 111, 272, 143, 15, fill="#f2edf5", outline="", tags="later")
        later_text = c.create_text(219, 127, text="Later", fill="#7f6c89",
                                   font=("Segoe UI", 9, "bold"), tags="later")
        for item in (yes_shape, yes_text):
            c.tag_bind(item, "<Button-1>", lambda _e: self._dismiss_greeting(open_chat=True))
            c.tag_bind(item, "<Enter>", lambda _e: c.itemconfigure(yes_shape, fill="#79559f"))
            c.tag_bind(item, "<Leave>", lambda _e: c.itemconfigure(yes_shape, fill="#8a68ae"))
        for item in (later_shape, later_text):
            c.tag_bind(item, "<Button-1>", lambda _e: self._dismiss_greeting(open_chat=False))
            c.tag_bind(item, "<Enter>", lambda _e: c.itemconfigure(later_shape, fill="#e9dfed"))
            c.tag_bind(item, "<Leave>", lambda _e: c.itemconfigure(later_shape, fill="#f2edf5"))
        self._place_greeting()
        self._fade_greeting_in(0.0)

    def _place_greeting(self) -> None:
        if not self.greeting_window or not self.greeting_window.winfo_exists():
            return
        root_x, root_y = self.root.winfo_x(), self.root.winfo_y()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = root_x - 300 if root_x > screen_w // 2 else root_x + SIZE + 8
        x = max(8, min(x, screen_w - 300))
        y = max(8, min(root_y + 12, screen_h - 172))
        self.greeting_window.geometry(f"292x164+{x}+{y}")

    def _fade_greeting_in(self, alpha: float) -> None:
        if not self.greeting_window or not self.greeting_window.winfo_exists():
            return
        alpha = min(0.96, alpha + 0.14)
        self.greeting_window.attributes("-alpha", alpha)
        if alpha < 0.96:
            self.root.after(14, lambda: self._fade_greeting_in(alpha))

    def _dismiss_greeting(self, open_chat: bool) -> None:
        if self.popup_stage != "greeting":
            return
        self.popup_stage = "transitioning"
        self._fade_greeting_out(0.96, open_chat)

    def _fade_greeting_out(self, alpha: float, open_chat: bool) -> None:
        if not self.greeting_window or not self.greeting_window.winfo_exists():
            self.greeting_window = None
            self.popup_stage = "hidden"
            if open_chat:
                self._make_drawer()
            return
        alpha -= 0.18
        if alpha <= 0:
            self.greeting_window.destroy()
            self.greeting_window = None
            self.popup_stage = "hidden"
            if open_chat:
                self.last_popup_stage = "chat"
                self._make_drawer()
            return
        self.greeting_window.attributes("-alpha", alpha)
        self.root.after(14, lambda: self._fade_greeting_out(alpha, open_chat))

    def _make_drawer(self) -> None:
        if self.drawer and self.drawer.winfo_exists():
            self.drawer.destroy()
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.attributes("-alpha", 0.0)
        panel.configure(bg="#f8f3fa")
        panel.resizable(False, False)
        panel.geometry("316x510+0+0")
        self.drawer = panel
        self.drawer_visible = True
        self.closing = False
        self.popup_stage = "chat"
        self.last_popup_stage = "chat"
        if not self.conversation:
            self.conversation.append({
                "role": "assistant",
                "text": "Hey Eya, your tiny robot pal is here. What's on your mind?",
            })

        c = tk.Canvas(panel, width=316, height=510, bg="#f8f3fa", highlightthickness=0)
        c.pack()
        self._rounded(c, 7, 7, 309, 503, 25, fill="#e9dfec", outline="")
        self._rounded(c, 5, 4, 307, 499, 25, fill="#fffdfc", outline="#eee7ef", width=1)
        c.create_oval(23, 22, 59, 58, fill="#f3eafa", outline="")
        c.create_text(41, 40, text="✦", fill="#a37ebd", font=("Segoe UI", 16, "bold"))
        c.create_text(72, 31, anchor="w", text="A little hello", fill=INK,
                      font=("Segoe UI", 12, "bold"))
        c.create_text(72, 51, anchor="w", text="your cozy corner", fill="#a398a8",
                      font=("Segoe UI", 8))
        close = c.create_oval(269, 23, 292, 46, fill="#f7f2f7", outline="", tags="close")
        close_text = c.create_text(280, 34, text="×", fill="#807487",
                                   font=("Segoe UI", 11), tags="close")
        for item in (close, close_text):
            c.tag_bind(item, "<Button-1>", lambda _e: self.toggle_drawer())
            c.tag_bind(item, "<Enter>", lambda _e: c.itemconfigure(close, fill="#f3e5ed"))
            c.tag_bind(item, "<Leave>", lambda _e: c.itemconfigure(close, fill="#f7f2f7"))

        c.create_text(25, 80, anchor="w", text="Hi Eya! How are you feeling today?",
                      fill=INK, font=("Segoe UI", 10, "bold"), width=265)
        self._rounded(c, 17, 96, 299, 283, 15, fill="#fbf8fc", outline="#f0e8f2", width=1)
        self.message_canvas = tk.Canvas(panel, width=264, height=176,
                                        bg="#fbf8fc", highlightthickness=0, bd=0)
        self.message_canvas.place(x=22, y=101)
        scroll = tk.Scrollbar(panel, orient="vertical", command=self.message_canvas.yview,
                              width=8, relief="flat", bd=0)
        scroll.place(x=288, y=103, height=173)
        self.message_canvas.configure(yscrollcommand=scroll.set)
        self.message_frame = tk.Frame(self.message_canvas, bg="#fbf8fc")
        self.message_canvas.create_window((0, 0), window=self.message_frame,
                                          anchor="nw", width=246, tags="message_window")
        self.message_frame.bind(
            "<Configure>",
            lambda _event: self.message_canvas.configure(
                scrollregion=self.message_canvas.bbox("all")
            ),
        )
        self.message_canvas.bind("<MouseWheel>", self._scroll_messages)
        self._render_chat_messages()

        c.create_text(25, 301, anchor="w", text="PICK YOUR MOOD", fill="#a49aaa",
                      font=("Segoe UI", 8, "bold"))

        for i, (label, mood) in enumerate((("Happy", "happy"), ("Tired", "sleepy"),
                                            ("Focused", "focused"), ("Stressed", "stressed"))):
            col, row = i % 2, i // 2
            x, y = 22 + col * 136, 318 + row * 40
            shape = self._rounded(c, x, y, x + 126, y + 34, 14,
                                  fill=LILAC, outline="", tags=f"mood{i}")
            dot = c.create_oval(x + 14, y + 13, x + 22, y + 21,
                                fill="#c9addd", outline="", tags=f"mood{i}")
            txt = c.create_text(x + 32, y + 17, anchor="w", text=label,
                                fill=PURPLE, font=("Segoe UI", 9, "bold"), tags=f"mood{i}")
            for item in (shape, dot, txt):
                c.tag_bind(item, "<Button-1>", lambda _e, m=mood, t=label: self.choose_mood(m, t))
                c.tag_bind(item, "<Enter>", lambda _e, it=shape: c.itemconfigure(it, fill="#e8dced"))
                c.tag_bind(item, "<Leave>", lambda _e, it=shape: c.itemconfigure(it, fill=LILAC))

        c.create_text(25, 405, anchor="w", text="A NOTE FOR ME", fill="#a49aaa",
                      font=("Segoe UI", 8, "bold"))
        self._rounded(c, 20, 421, 296, 465, 15, fill="#ffffff",
                      outline="#e9e1eb", width=1)
        self.entry = tk.Entry(panel, relief="flat", bd=0, bg="#ffffff", fg=INK,
                              insertbackground=PURPLE, font=("Segoe UI", 9))
        self.entry.place(x=35, y=433, width=203, height=20)
        self.entry.insert(0, "Type a little message...")
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<Return>", self.send_message)
        send = c.create_oval(251, 426, 286, 461, fill=PURPLE, outline="", tags="send")
        send_text = c.create_text(268, 443, text=">", fill="white",
                                  font=("Segoe UI", 11, "bold"), tags="send")
        for item in (send, send_text):
            c.tag_bind(item, "<Button-1>", self.send_message)
            c.tag_bind(item, "<Enter>", lambda _e: c.itemconfigure(send, fill="#72548f"))
            c.tag_bind(item, "<Leave>", lambda _e: c.itemconfigure(send, fill=PURPLE))
        self.status_canvas = c
        self.status_text_item = c.create_text(
            24, 486, anchor="w", text=self.chat_engine.status_for(),
            fill="#94879a", font=("Segoe UI", 8)
        )
        quit_link = c.create_text(282, 486, text="quit", fill="#aaa0ad",
                                  font=("Segoe UI", 8), tags="quit")
        c.tag_bind(quit_link, "<Button-1>", lambda _e: self._close_app())
        c.tag_bind(quit_link, "<Enter>", lambda _e: c.itemconfigure(quit_link, fill="#d4778b"))
        c.tag_bind(quit_link, "<Leave>", lambda _e: c.itemconfigure(quit_link, fill="#aaa0ad"))

        self._place_drawer()
        self._fade_in(0)

    def _render_chat_messages(self) -> None:
        if not self.message_frame or not self.message_canvas:
            return
        if self.message_font is None:
            self.message_font = tkfont.Font(self.root, family="Segoe UI", size=9)
        for child in self.message_frame.winfo_children():
            child.destroy()
        for item in self.conversation:
            is_user = item.get("role") == "user"
            row = tk.Frame(self.message_frame, bg="#fbf8fc")
            row.pack(fill="x", padx=4, pady=(3, 5))
            side = "e" if is_user else "w"
            if is_user:
                sender = "You"
                bg, fg = "#eee4f5", "#554662"
            else:
                sender = "Eya"
                bg, fg = "#ffffff", "#62576b"
            tk.Label(row, text=sender, bg="#fbf8fc", fg="#a79baa",
                     font=("Segoe UI", 7, "bold")).pack(anchor=side, padx=4, pady=(0, 2))
            body = self._wrap_chat_text(str(item.get("text", "")), 210)
            text_width = max(self.message_font.measure(line) for line in body)
            bubble_width = min(232, max(112, text_width + 22))
            bubble_height = self.message_font.metrics("linespace") * len(body) + 18
            bubble = tk.Canvas(row, width=bubble_width, height=bubble_height,
                               bg="#fbf8fc", highlightthickness=0, bd=0)
            self._rounded(bubble, 1, 1, bubble_width - 1, bubble_height - 1, 12,
                          fill=bg, outline="#eee7f1", width=1)
            bubble.create_text(11, 9, anchor="nw", text="\n".join(body),
                               font=self.message_font, fill=fg,
                               width=bubble_width - 22, justify="left")
            bubble.pack(anchor=side, padx=2)
            for widget in (row, *row.winfo_children()):
                widget.bind("<MouseWheel>", self._scroll_messages)
        self.root.after_idle(lambda: self.message_canvas.yview_moveto(1.0)
                             if self.message_canvas and self.message_canvas.winfo_exists() else None)

    def _wrap_chat_text(self, text: str, max_width: int) -> list[str]:
        if not self.message_font:
            return [text]
        lines: list[str] = []
        line = ""
        for word in text.split():
            chunks: list[str] = []
            chunk = ""
            for char in word:
                if chunk and self.message_font.measure(chunk + char) > max_width:
                    chunks.append(chunk)
                    chunk = char
                else:
                    chunk += char
            if chunk:
                chunks.append(chunk)
            for piece in chunks:
                candidate = f"{line} {piece}".strip()
                if line and self.message_font.measure(candidate) > max_width:
                    lines.append(line)
                    line = piece
                else:
                    line = candidate
        if line:
            lines.append(line)
        return lines or [""]

    def _scroll_messages(self, event: tk.Event) -> str:
        if self.message_canvas:
            self.message_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    @staticmethod
    def _rounded(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                 radius: int, **kwargs: object) -> int:
        points = [x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
                  x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
                  x1, y2, x1, y2-radius, x1, y1+radius, x1, y1]
        return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _fade_in(self, alpha: float) -> None:
        if not self.drawer or not self.drawer.winfo_exists() or self.closing:
            return
        alpha = min(0.96, alpha + 0.12)
        self.drawer.attributes("-alpha", alpha)
        if alpha < 0.96:
            self.root.after(14, lambda: self._fade_in(alpha))

    def _fade_out(self, alpha: float = 0.96) -> None:
        if not self.drawer or not self.drawer.winfo_exists():
            self.drawer = None
            self.drawer_visible = False
            self.popup_stage = "hidden"
            return
        alpha -= 0.16
        if alpha <= 0:
            self.drawer.destroy()
            self.drawer = None
            self.drawer_visible = False
            self.closing = False
            self.popup_stage = "hidden"
            self.last_popup_stage = "chat"
            self.message_canvas = None
            self.message_frame = None
            return
        self.drawer.attributes("-alpha", alpha)
        self.root.after(14, lambda: self._fade_out(alpha))

    def toggle_drawer(self) -> None:
        if self.drawer_visible:
            if not self.closing:
                self.closing = True
                self._fade_out()
            return
        if self.popup_stage == "hidden":
            self._make_drawer()

    def _place_drawer(self) -> None:
        if self.drawer and self.drawer.winfo_exists():
            x = self.root.winfo_x() - 119
            y = self.root.winfo_y() + 174
            x = max(8, min(x, self.root.winfo_screenwidth() - 324))
            y = min(y, self.root.winfo_screenheight() - 522)
            self.drawer.geometry(f"316x510+{x}+{y}")

    def choose_mood(self, mood: str, label: str) -> None:
        self.mood = mood
        self._submit_chat(f"I'm feeling {label.lower()} today.", mood_hint=label)

    def _submit_chat(self, message: str, mood_hint: str | None = None) -> None:
        self.mood = self.chat_engine.infer_mood(message, mood_hint)
        self.reply_number += 1
        request_id = self.reply_number
        self.latest_reply_number = request_id
        self.conversation.append({"role": "user", "text": message})
        self.conversation.append({
            "role": "assistant",
            "text": "Thinking...",
        })
        if self.status_canvas and self.status_text_item:
            waiting = (f"Thinking · {self.chat_engine.provider_name}"
                       if self.chat_engine.use_api else "Thinking...")
            self.status_canvas.itemconfigure(self.status_text_item, text=waiting)
        self.pending_message_indices[request_id] = len(self.conversation) - 1
        self._render_chat_messages()
        self.talk_until = time.monotonic() + 2.8
        future = self.chat_worker.submit(self.chat_engine.respond, message, mood_hint)
        future.add_done_callback(
            lambda completed, number=request_id: self.chat_results.put((number, completed))
        )

    def _poll_chat_results(self) -> None:
        try:
            while True:
                request_id, future = self.chat_results.get_nowait()
                try:
                    result = future.result()
                except Exception:
                    result = ChatReply(
                        "Oops, my words got tangled for a sec. Try me again?",
                        "focused", "Local",
                    )
                message_index = self.pending_message_indices.pop(request_id, None)
                if message_index is not None:
                    self.conversation[message_index]["text"] = result.text
                if (request_id == self.latest_reply_number and
                        self.status_canvas and self.status_text_item):
                    self.status_canvas.itemconfigure(
                        self.status_text_item,
                        text=self.chat_engine.status_for(result.source),
                    )
                if request_id == self.latest_reply_number:
                    self.mood = result.mood
                    self.talk_until = time.monotonic() + 2.8
                if self.drawer and self.drawer.winfo_exists():
                    self._render_chat_messages()
        except Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll_chat_results)

    def _close_app(self) -> None:
        self.chat_worker.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def _clear_placeholder(self, _event: tk.Event) -> None:
        if self.entry.get() == "Type a little message...":
            self.entry.delete(0, tk.END)

    def send_message(self, _event: tk.Event | None = None) -> None:
        message = self.entry.get().strip()
        if not message or message == "Type a little message...":
            return
        self.entry.delete(0, tk.END)
        self.root.after(10, self.entry.focus_set)
        self._submit_chat(message)


def main() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    EyaBuddy(root)
    root.mainloop()


if __name__ == "__main__":
    main()
