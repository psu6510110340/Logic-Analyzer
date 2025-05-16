#!/usr/bin/env python3
# coding: utf-8
"""
Modern CAN Square‑Wave Visualizer  🌙🟢
─────────────────────────────────────
Dark‑mode UI, accent buttons, and slick typography – while preserving every
original feature (auto‑scan COM/baud lists, ARM trigger, Start/Stop/Clear/
Open‑log, square‑wave plot).

► What’s new in this revision (May 15 2025)
  • Accent buttons are now *rounded rectangles* – a softer, modern feel
    using flat relief + extra padding (works cross‑platform with ttk).
  • Tweaked hover/active colours for better contrast on dark base.
  • Slightly larger default window (minsize) to match button padding.

Requires only standard Tkinter 8.6 + Matplotlib – no extra deps.
Run:
    python can_visualizer_modern.py
"""
import sys, threading
from datetime import datetime
from queue import Queue, Empty

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ───── Serial & Plot settings ───────────────────────────
BIT_MASK   = 0xFF        # 0x01 ➟ only bit‑0
READ_DELAY = 50          # ms – plot refresh
BAUD_LIST  = [9600, 19200, 38400, 57600, 115200, 250000,
              500000, 750000, 1000000]
# ─────────────────────────────────────────────────────────

def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []

def read_from_serial(port, baud, queue: Queue,
                     stop_evt: threading.Event,
                     trig_evt: threading.Event):
    import serial
    try:
        with serial.Serial(port, baud, timeout=1) as ser:
            while not stop_evt.is_set():
                if not trig_evt.is_set():
                    continue
                line = ser.readline().decode(errors="ignore").strip()
                if line:
                    queue.put(line)
    except serial.SerialException as e:
        queue.put(f"#ERR {e}")

def read_from_file(path, queue: Queue, stop_evt: threading.Event, *_):
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if stop_evt.is_set():
                break
            queue.put(ln.strip())

# ──────────  Main Application  ──────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("CAN Square‑Wave Visualizer – Modern UI")
        root.configure(bg="#2b2d31")
        root.minsize(800, 460)

        # === ttk styling =================================
        self._init_style()

        # === Top bar =====================================
        top = ttk.Frame(root, padding=(8, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(7, weight=1)  # allow spacing flex

        ttk.Label(top, text="COM Port").grid(row=0, column=0, sticky="w")
        self.cbo_port = ttk.Combobox(top, width=12, state="readonly")
        self._refresh_com_list()
        self.cbo_port.grid(row=0, column=1, padx=(4, 12))

        ttk.Label(top, text="Baud").grid(row=0, column=2, sticky="w")
        self.cbo_baud = ttk.Combobox(top, width=12, state="readonly",
                                     values=[str(b) for b in BAUD_LIST])
        self.cbo_baud.set("1000000")
        self.cbo_baud.grid(row=0, column=3, padx=(4, 12))

        self.var_trigger = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="ARM Trigger", variable=self.var_trigger,
                        style="Accent.TCheckbutton").grid(row=0, column=4)

        ttk.Button(top, text="⟳ Rescan COM", command=self._refresh_com_list,
                   style="Accent.TButton").grid(row=0, column=5, padx=(16, 0))

        # === Matplotlib canvas ============================
        fig = Figure(figsize=(8, 4), dpi=100, facecolor="#2b2d31")
        self.ax  = fig.add_subplot(111, facecolor="#1e1f22")
        self.line, = self.ax.plot([], [], drawstyle="steps-post", lw=1.8,
                                  marker="", color="#4ade80")
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Logic Level")
        self.ax.set_yticks([0, 1])
        self.ax.set_ylim(-.2, 1.2)
        self._restyle_axes()

        self.canvas = FigureCanvasTkAgg(fig, master=root)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)

        # === Control buttons ==============================
        ctrl = ttk.Frame(root, padding=(0, 6))
        ctrl.grid(row=2, column=0)

        self.btn_start = ttk.Button(ctrl, text="▶ Start",
                                     command=self.start, style="Accent.TButton")
        self.btn_stop  = ttk.Button(ctrl, text="■ Stop", command=self.stop,
                                    state="disabled", style="AccentDanger.TButton")
        self.btn_file  = ttk.Button(ctrl, text="📂 Open Log",
                                    command=self.open_file)
        self.btn_clear = ttk.Button(ctrl, text="⟲ Clear",
                                    command=self.clear)
        for i, b in enumerate((self.btn_start, self.btn_stop, self.btn_clear, self.btn_file)):
            b.grid(row=0, column=i, padx=6)

        # === Data & worker ================================
        self.q      = Queue()
        self.stop_e = threading.Event()
        self.trig_e = threading.Event()
        self.worker = None
        self.reader = read_from_serial  # can swap to file reader

        self.xs, self.ys = [], []
        self.t0 = None

        root.after(READ_DELAY, self._update_plot)

    # ─────── Styling helper ──────────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background="#2b2d31")
        style.configure("TLabel", background="#2b2d31", foreground="#e5e7eb",
                        font=("Segoe UI", 10))
        style.configure("TCheckbutton", background="#2b2d31", foreground="#e5e7eb",
                        font=("Segoe UI", 10))
        style.configure("TCombobox", fieldbackground="#1e1f22",
                        background="#1e1f22", foreground="#e5e7eb",
                        arrowsize=14, relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", "#1e1f22")],
                  foreground=[("readonly", "#e5e7eb")])

        # Accent buttons (blue) – rounded rectangle look via flat relief & padding
        common_btn = dict(font=("Segoe UI", 11, "bold"), padding=(14, 6),
                          foreground="#ffffff", borderwidth=0, relief="flat",
                          focusthickness=1, focuscolor="none")
        style.configure("Accent.TButton", background="#3b82f6", **common_btn)
        style.map("Accent.TButton",
                  background=[("active", "#2563eb"), ("disabled", "#555")],
                  relief=[("pressed", "flat"), ("!pressed", "flat")])

        # Danger (red) for Stop
        style.configure("AccentDanger.TButton", background="#ef4444", **common_btn)
        style.map("AccentDanger.TButton",
                  background=[("active", "#b91c1c"), ("disabled", "#555")])

        # Accent checkbutton indicator colour tweak
        style.map("Accent.TCheckbutton",
                  indicatorcolor=[("selected", "#4ade80"), ("!selected", "#555")])

    def _restyle_axes(self):
        for spine in self.ax.spines.values():
            spine.set_color("#9ca3af")
        self.ax.tick_params(axis="both", colors="#e5e7eb")
        self.ax.xaxis.label.set_color("#e5e7eb")
        self.ax.yaxis.label.set_color("#e5e7eb")
        self.ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.3, color="#9ca3af")

    # ─────── UI Actions ─────────────────────────────────
    def _refresh_com_list(self):
        ports = list_serial_ports()
        self.cbo_port["values"] = ports
        if ports:
            self.cbo_port.set(ports[0])
        else:
            self.cbo_port.set("")

    def start(self):
        port = self.cbo_port.get()
        baud = self.cbo_baud.get()
        if not port:
            messagebox.showwarning("Config",
                                   "ไม่พบ COM Port – กรุณา Rescan หรือต่ออุปกรณ์")
            return
        if not baud.isdigit():
            messagebox.showwarning("Config", "เลือก Baud rate ให้ถูกต้อง")
            return

        if self.worker and self.worker.is_alive():
            return
        self.stop_e.clear()
        (self.trig_e.set() if self.var_trigger.get() else self.trig_e.clear())
        self.worker = threading.Thread(target=self.reader,
                                       args=(port, int(baud),
                                             self.q, self.stop_e, self.trig_e),
                                       daemon=True)
        self.worker.start()
        self.btn_start.state(["disabled"])
        self.btn_stop.state(["!disabled"])

    def stop(self):
        self.stop_e.set()
        self.btn_start.state(["!disabled"])
        self.btn_stop.state(["disabled"])

    def clear(self):
        self.stop()
        self.xs.clear(); self.ys.clear(); self.t0 = None
        self.line.set_data([], [])
        self.ax.relim(); self.ax.autoscale_view()
        self.canvas.draw_idle()

    def open_file(self):
        path = filedialog.askopenfilename(title="Select log",
                                          filetypes=[("Log/Text", "*.txt *.log"),
                                                     ("All", "*.*")])
        if not path:
            return
        self.clear()
        self.reader = read_from_file
        self.worker = threading.Thread(target=self.reader,
                                       args=(path, self.q, self.stop_e, None),
                                       daemon=True)
        self.worker.start()
        self.btn_start.state(["disabled"])
        self.btn_stop.state(["!disabled"])

    # ─────── Plot updater ───────────────────────────────
    def _update_plot(self):
        updated = False
        try:
            while True:
                ln = self.q.get_nowait()
                if ln.startswith("#ERR"):
                    messagebox.showerror("Serial Error", ln[4:])
                    self.stop()
                    break
                updated |= self._parse_line(ln)
        except Empty:
            pass

        if updated:
            self.line.set_data(self.xs, self.ys)
            self.ax.relim(); self.ax.autoscale_view()
            self.canvas.draw_idle()

        self.root.after(READ_DELAY, self._update_plot)

    def _parse_line(self, ln: str) -> bool:
        parts = ln.split()
        if len(parts) < 2:
            return False
        try:
            ts, byte0 = parts[0], parts[1]
            t = datetime.fromisoformat(ts)
            if self.t0 is None:
                self.t0 = t
            self.xs.append((t - self.t0).total_seconds())
            self.ys.append(1 if (int(byte0, 16) & BIT_MASK) else 0)
            return True
        except (ValueError, IndexError):
            return False


# ─────── Run ───────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
