#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeEAK (GUI edition)
=====================
Merged‑feature version that keeps **all** original CodeEAK decoding / visual‑mark‑up
while adding quality‑of‑life controls inspired by *MyCode*:

1.  Launches even when no USB‑CAN adapter is attached.
2.  **Mode** selector – *Serial* (live capture) or *File* (re‑play raw dump).
3.  **Import File** button for selecting a .txt / .bin containing the raw capture.
4.  Frame(s) are **not plotted** until **Start** is pressed – you may import first,
   then analyse when ready.
5.  **Reset** returns the application to a clean state without losing any GUI
   element or original decoding capabilities.

The entire original decoding algorithm (``decode_8byte_aligned``) *and* plot
annotation logic from CodeEAK are kept intact; only small refactors were made to
wrap them in a Tk‑interface and split live/file update schedulers.

Tested on Python 3.11, matplotlib 3.8, Tk >= 8.6, pyserial 3.5.
"""

import threading
import struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from queue import Queue, Empty
import time
import sys

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ──────────────────────────── Optional serial import ────────────────────────────
try:
    import serial
    from serial.tools import list_ports
except ImportError:  # allow running in File mode without pyserial installed
    serial = None
    list_ports = lambda: []  # type: ignore

# ─── Constants ──────────────────────────────────────────────────────────────────
BAUD          = 1_152_000
READ_INTERVAL = 100   # ms between GUI updates
BIT_T         = 20    # nominal bit duration in timestamp ticks (same as CodeEAK)

# ---------- UI theme ----------
ACCENT      = "#0078D7"   # primary blue
ACCENT_DARK = "#005A9E"   # hover shade
BG_DARK     = "#1e1e1e"   # window background
FG_LIGHT    = "#ffffff"   # default foreground

# ─────────────────────────────── Helper functions ───────────────────────────────

def available_ports():
    if list_ports:
        return [p.device for p in list_ports.comports()]
    return []

# ────────────────────── Original CodeEAK decoding functions ─────────────────────
# Ported verbatim except for removal of 'global' ‑ we keep state in an object.

class CodeEAKDecoder:
    """State‑ful decoder reproducing original CodeEAK behaviour."""

    def __init__(self):
        self.reset()

    # public -------------------------------------------------------------------

    def reset(self):
        self.state_data              = []  # logic level transitions (0/1)
        self.timestamp_data          = []  # cumulative tick value of each transition
        self.total_time              = 0
        self.current_bit_state_pct   = [0, 0]
        self.current_bit_index       = 0
        self.bit_data                = []  # decoded bit stream (after stuff removal)
        self.bit_duration            = BIT_T

    def feed(self, payload: bytes):
        """Feed a *raw* byte string (multi‑records allowed) and update state."""
        i = 0
        while i < len(payload):
            # record header 0x11 0x00|01 0x01 0x00 ...timestamp(4)
            if payload[i:i+3] in (b"\x11\x00\x01", b"\x11\x01\x01"):
                rec = payload[i:i+8]
                if len(rec) < 8:
                    i += 1
                    continue

                state = rec[1]
                timestamp = struct.unpack("<I", rec[4:8])[0]

                # Reset stream if timestamp rolls over/backward (same as CodeEAK)
                if self.timestamp_data and timestamp < self.timestamp_data[-1]:
                    self.reset()

                # duplicate suppression
                if len(self.state_data) > 1 and state == self.state_data[-1]:
                    i += 8
                    continue

                # Append state twice (rising edge representation from original)
                if self.state_data:
                    self.state_data.append(self.state_data[-1])
                self.state_data.append(state)

                # Append timestamp twice to match state duplication
                if self.timestamp_data:
                    self.timestamp_data.append(timestamp)
                self.timestamp_data.append(timestamp)

                # Decode bits by integrating over BIT_T windows
                if self.current_bit_index > 40:  # dynamic tweak also from original
                    self.bit_duration = 20.1

                while self.current_bit_index * self.bit_duration <= timestamp:
                    if (self.current_bit_index + 1) * self.bit_duration <= timestamp:
                        # push bit based on duty cycle heuristics (unchanged)
                        if self.current_bit_state_pct != [0, 0]:
                            self.current_bit_state_pct[1 - state] = (
                                (self.current_bit_index + 1) * self.bit_duration
                                - self.total_time
                            )
                            if self.current_bit_state_pct[0] > 10:
                                self.bit_data.append(0)
                            elif self.current_bit_state_pct[0] == 10:
                                if len(self.bit_data) > 4 and sum(self.bit_data[-5:]) == 0:
                                    self.bit_data.append(1)
                                else:
                                    self.bit_data.append(0)
                            elif self.current_bit_state_pct[1] == 10:
                                if len(self.bit_data) > 4 and sum(self.bit_data[-5:]) == 5:
                                    self.bit_data.append(0)
                                else:
                                    self.bit_data.append(1)
                            else:
                                self.bit_data.append(1)
                        else:
                            self.bit_data.append(1 - state)
                        self.current_bit_state_pct = [0, 0]
                        self.current_bit_index += 1
                    else:
                        self.current_bit_state_pct[1 - state] = (
                            timestamp - self.current_bit_index * self.bit_duration
                        )
                        break

                self.total_time = timestamp
                i += 8
            else:
                i += 1

    # shorthand accessors -------------------------------------------------------

    @property
    def plot_x(self):
        return self.timestamp_data

    @property
    def plot_y(self):
        return self.state_data

    @property
    def bits(self):
        return self.bit_data

# ───────────────────────── Application (Tk wrapper) ─────────────────────────────

class LogicAnalyzerGUI:
    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("Logic Analyzer – CodeEAK GUI")
        master.geometry("1200x900")

        # NEW: dark background
        master.configure(bg=BG_DARK)

        # NEW: ttk Style definitions
        style = ttk.Style(master)
        style.theme_use("clam")                      # modern neutral base
        style.configure("TFrame",  background=BG_DARK)
        style.configure("TLabel",  background=BG_DARK, foreground=FG_LIGHT, font=("Segoe UI", 10))
        style.configure("TRadiobutton", background=BG_DARK, foreground=FG_LIGHT, font=("Segoe UI", 10))
        style.configure("TCombobox",
                        fieldbackground="#2e2e2e", background="#2e2e2e",
                        foreground=FG_LIGHT, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "#2e2e2e")])

        style.configure("Accent.TButton",
                        font=("Segoe UI", 10, "bold"), foreground=FG_LIGHT,
                        background=ACCENT, padding=8, borderwidth=0)
        style.map("Accent.TButton",
                background=[("active", ACCENT_DARK), ("disabled", "#555555")])

        style.configure("Secondary.TButton",
                        font=("Segoe UI", 10), foreground=FG_LIGHT,
                        background="#3c3f41", padding=8, borderwidth=0)
        style.map("Secondary.TButton",
                background=[("active", "#2d2f31"), ("disabled", "#555555")])

        # helper for hover effect
        def add_hover(widget, base="Accent.TButton", over="HoverAccent.TButton"):
            style.configure(over, background=ACCENT_DARK, foreground=FG_LIGHT)
            widget.bind("<Enter>", lambda *_: widget.configure(style=over))
            widget.bind("<Leave>", lambda *_: widget.configure(style=base))

        # ─── Top control bar ────────────────────────────────────────────────────
        ctrl_wrapper = tk.Frame(master, bg=BG_DARK)
        ctrl_wrapper.pack(fill="x", pady=(8, 4))
        tk.Frame(master, height=2, bg="#000000").pack(fill="x", pady=(0, 6))  # soft shadow

        ctrl = ttk.Frame(ctrl_wrapper, padding=(10, 6))
        ctrl.pack(fill="x")

        # Mode selector
        self.mode_var = tk.StringVar(value="Serial")
        ttk.Label(ctrl, text="Mode").pack(side="left", padx=(0, 6))
        for text in ("Serial", "File"):
            ttk.Radiobutton(ctrl, text=text, value=text,
                            variable=self.mode_var,
                            command=self._on_mode_changed).pack(side="left")

        # Port combobox
        ttk.Label(ctrl, text="Port").pack(side="left", padx=(16, 4))
        self.port_var = tk.StringVar()
        self.port_dd = ttk.Combobox(ctrl, textvariable=self.port_var,
                                    state="readonly", values=available_ports(), width=10)
        if self.port_dd["values"]:
            self.port_dd.current(0)
        self.port_dd.pack(side="left")

        # Import button + filename label
        self.import_btn = ttk.Button(ctrl, text="📂 Import",
                                    style="Secondary.TButton",
                                    command=self._import_file)
        self.import_btn.pack(side="left", padx=6)

        self.file_label_var = tk.StringVar(value="No file selected")
        ttk.Label(ctrl, textvariable=self.file_label_var,
                foreground="#bbbbbb").pack(side="left", padx=6)

        # Start / Stop / Reset buttons (+ hover)
        self.start_btn = ttk.Button(ctrl, text="▶ Start",
                                    style="Accent.TButton", command=self._start)
        self.stop_btn  = ttk.Button(ctrl, text="⏹ Stop",
                                    style="Accent.TButton", command=self._stop,
                                    state="disabled")
        self.reset_btn = ttk.Button(ctrl, text="🔄 Reset",
                                    style="Secondary.TButton", command=self._reset)

        for b in (self.start_btn, self.stop_btn):
            add_hover(b)
        add_hover(self.reset_btn, base="Secondary.TButton",
                over="HoverSecondary.TButton")
        style.configure("HoverSecondary.TButton",
                        background="#2d2f31", foreground=FG_LIGHT)

        self.start_btn.pack(side="left", padx=6)
        self.stop_btn .pack(side="left", padx=6)
        self.reset_btn.pack(side="left", padx=6)

        # ─── Figure / canvas ───────────────────────────────────────────────────
        self.fig = Figure(figsize=(10,5), dpi=100)
        self.ax  = self.fig.add_subplot(111)
        self._init_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, master).update()

        # ─── Runtime / data attributes ─────────────────────────────────────────
        self.decoder      = CodeEAKDecoder()
        self.queue        = Queue()
        self.stop_event   = threading.Event()
        self.serial_thr   = None
        self.file_bytes   = b""  # raw bytes loaded from file (File mode)
        self.file_offset  = 0    # byte offset while playing back

    # ───────────────────────────── GUI actions ────────────────────────────────

    def _on_mode_changed(self, *_):
        is_serial = self.mode_var.get() == "Serial"
        self.port_dd.config(state="readonly" if is_serial else "disabled")
        self.import_btn.config(state="disabled" if is_serial else "normal")

    # -------------------------------------------------------------------------

    def _import_file(self):
        fp = filedialog.askopenfilename(title="Select raw dump",
                                        filetypes=[("Text / Binary", "*.txt *.bin"), ("All", "*.*")])
        if not fp:
            return
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except Exception as e:
            messagebox.showerror("File error", str(e))
            return

        # ถ้าเป็นไฟล์ b'...' string literal
        if data.strip().startswith(b"b'"):
            import ast
            raw = b""
            for line in data.splitlines():
                try:
                    raw += ast.literal_eval(line.strip().decode("utf-8"))
                except Exception:
                    continue
            data = raw

        self.file_bytes  = data
        self.file_offset = 0
        filename = fp.split("/")[-1]
        self.file_label_var.set(f"Selected: {filename}")

    # -------------------------------------------------------------------------

    def _start(self):
        mode = self.mode_var.get()
        self.stop_event.clear()

        if mode == "Serial":
            port = self.port_var.get()
            if not port:
                messagebox.showwarning("Port required", "Select a COM port first.")
                return
            if serial is None:
                messagebox.showerror("pyserial missing", "Install pyserial to use Serial mode.")
                return
            # Launch background thread
            self.serial_thr = threading.Thread(target=self._serial_reader, args=(port,), daemon=True)
            self.serial_thr.start()
            # schedule UI polling
            self.master.after(READ_INTERVAL, self._update_from_queue)
        else:  # File mode
            if not self.file_bytes:
                messagebox.showwarning("No file", "Please import a raw data file first.")
                return
            self.master.after(READ_INTERVAL, self._update_from_file)

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    # -------------------------------------------------------------------------

    def _stop(self):
        self.stop_event.set()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    # -------------------------------------------------------------------------

    def _reset(self):
        self._stop()
        self.decoder.reset()
        self.queue.queue.clear()
        self.file_offset = 0
        self._init_axes()
        self.canvas.draw_idle()

    # ───────────────────────── Serial background thread ───────────────────────

    def _serial_reader(self, port: str):
        try:
            with serial.Serial(port, BAUD, timeout=1) as ser:
                while not self.stop_event.is_set():
                    pkt = ser.read(120)
                    if pkt:
                        self.queue.put(pkt)
        except serial.SerialException as e:
            messagebox.showerror("Serial error", str(e))
            self.stop_event.set()

    # ───────────────────────────── UI updaters ────────────────────────────────

    def _update_from_queue(self):
        # drain queue
        try:
            while True:
                chunk = self.queue.get_nowait()
                self.decoder.feed(chunk)
        except Empty:
            pass
        self._redraw_plot()
        if not self.stop_event.is_set():
            self.master.after(READ_INTERVAL, self._update_from_queue)

    def _update_from_file(self):
        if self.stop_event.is_set():
            return
        # feed up to ~120 bytes each iteration to mimic live behaviour
        step = min(120, len(self.file_bytes) - self.file_offset)
        if step > 0:
            chunk = self.file_bytes[self.file_offset:self.file_offset + step]
            self.file_offset += step
            self.decoder.feed(chunk)
        self._redraw_plot()
        if self.file_offset < len(self.file_bytes):
            self.master.after(READ_INTERVAL, self._update_from_file)
        else:
            self.stop_event.set()
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    # ───────────────────────────── Draw routine ───────────────────────────────

    def _init_axes(self):
        self.ax.clear()
        self.ax.set_xlabel("Time (ticks)")
        self.ax.set_ylabel("Logic level")
        self.ax.set_ylim(-0.5, 1.5)
        self.ax.set_xlim(0, 2500)
        self.ax.set_title("Live CAN Frame")
        self.ax.grid(True)
        # background bit‑grid every 20 ticks
        for x in range(0, 2500, BIT_T):
            self.ax.axvline(x, color="gray", linestyle="--", linewidth=0.5)

    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    def _redraw_plot(self):
        """Refresh the CAN-frame plot with live / file data."""
        # ─── base grid ────────────────────────────────────────────────────────
        self._init_axes()                                   # เคลียร์แกน + ตีเส้นย่อย

        bits         = self.decoder.bits                    # stream (อาจมี stuff-bit)
        bit_duration = BIT_T

        # ───────────────────── เพิ่ม : ดึงค่าฟิลด์เป็นเลขฐาน 16 ──────────────
        def _destuff(stream):
            """Return stream after removing CAN stuff bits (5 identical + 1 stuff)."""
            clean, run, last = [], 0, None
            i = 0
            while i < len(stream):
                b = stream[i]
                clean.append(b)
                run = run + 1 if b == last else 1
                last = b
                if run == 5:          # ข้ามบิตถัดไป (stuff-bit)
                    i += 1
                    run = 0
                i += 1
            return clean

        nb = _destuff(bits)                                # nb = no-stuff bits

                # ---------- แปลงบิตเป็นเลขฐาน 16 แบบ “แสดงเท่าที่มี” ----------
        hex_map = {}
        to_int = lambda s: int(''.join(map(str, s)), 2)

        # ▸ CAN-ID (ต้องมีครบ 11 บิตแรก + SOF = 12 บิต)
        if len(nb) >= 12:
            can_id = to_int(nb[1:12])
            hex_map["ID"] = f"0x{can_id:03X}"

        # ▸ DLC (ต้องมีอย่างน้อย 19 บิต)
        if len(nb) >= 19:
            dlc = to_int(nb[15:19])
            hex_map["DLC"] = f"0x{dlc:X}"
        else:
            dlc = 0

        # ▸ DATA (คำนวณเท่าที่มีจริง — สูงสุด 8 ไบต์)
        for i in range(min(dlc, 8)):
            start = 19 + i*8
            end   = start + 8
            if len(nb) >= end:
                data_val = to_int(nb[start:end])
                hex_map[f"DATA{i}"] = f"0x{data_val:02X}"
            else:
                break         # ไม่มีบิตครบ 8 บิตแล้ว หยุดวน

        # ▸ CRC (ต้องมีครบ 98 บิตถึงจะแสดง — ตัดสินใจให้เหมือนเดิม)
        if len(nb) >= 98:
            crc = to_int(nb[83:98])
            hex_map["CRC"] = f"0x{crc:04X}"

        # ───────────────────── Bus Idle 8 บิตนำหน้า ───────────────────────────
        idle_bits   = 8
        idle_x_end  = idle_bits * bit_duration
        idle_times  = [i * bit_duration for i in range(idle_bits + 1)] + [idle_x_end]
        idle_levels = [1] * (idle_bits + 1) + [0]
        self.ax.step(idle_times, idle_levels, where="post",
                     color="blue", linewidth=1.5)
        self.ax.text(idle_x_end / 2, -0.35, "Bus Idle",
                     fontsize=8, ha="center", va="bottom",
                     bbox=dict(boxstyle="round,pad=0.2",
                               edgecolor="black", facecolor="yellow"))

        # ───────────────────── ตารางฟิลด์ - fixed positions ───────────────────
        frame_labels = {
            0:  "SOF",  1:  "ID",    12: "RTR", 13: "IDE", 14: "r0", 15: "DLC",
            19: "DATA0", 27: "DATA1", 35: "DATA2", 43: "DATA3",
            51: "DATA4", 59: "DATA5", 67: "DATA6", 75: "DATA7",
            83: "CRC", 98: "CD", 99: "ACK", 100: "AD", 101: "EOF"
        }

        last_bit  = None
        run_len   = 0
        actual_idx = 0                                     # นับบิตจริง (มี stuff)

        # ───────────────────── วาดบิตทีละตัว ────────────────────────────────
        for idx, bit in enumerate(bits):
            x_mid = idle_x_end + idx * bit_duration + bit_duration / 2
            self.ax.text(x_mid, 1.10, str(bit), fontsize=8,
                         ha="center", va="center", color="blue")
            self.ax.text(x_mid, 1.20, str(idx), fontsize=7,
                         ha="center", va="center", rotation=90)

            # mark stuff-bit (บิตถัดจาก run-length 5)
            if bit == last_bit:
                run_len += 1
            else:
                if run_len >= 5:
                    # --- mark stuff-bit -----------------------------------------------------------
                    STUFF_BOX = dict(facecolor="#C80202",    # พื้นหลังแดงเข้ม
                                    edgecolor="none",
                                    boxstyle="round,pad=0.2",
                                    alpha=0.9)

                    # แสดงป้าย STUFF (ใช้ self.ax และ x_mid ที่มีอยู่แล้ว)
                    self.ax.text(x_mid, 1.38, "STUFF",   # หรือใช้ 1.37/1.40 ก็ได้ตามชอบ
                        fontsize=8,
                        ha="center", va="center",
                        color="white",
                        rotation=0,
                        bbox=STUFF_BOX)

                    # ไฮไลต์ช่วงบิตด้วยสีโปร่งแสง
                    self.ax.axvspan(x_mid - BIT_T/2,
                                    x_mid + BIT_T/2,
                                    facecolor="#FF0000", alpha=0.25)
                    run_len = 0
                    last_bit = bit
                    continue
                run_len = 1

            # วาดเส้นแบ่ง + label ฟิลด์
            if actual_idx in frame_labels:
                label      = frame_labels[actual_idx]
                x_label    = idle_x_end + idx * bit_duration
                self.ax.axvline(x_label, color='black', linewidth=1)

                next_key   = next((k for k in sorted(frame_labels) if k > actual_idx),
                                   actual_idx + 1)
                field_w    = (next_key - actual_idx) * bit_duration

                # --- เพิ่มเลข Hex ---
                if label in hex_map:
                    label_text = f"{label}\n{hex_map[label]}"
                else:
                    label_text = label if (next_key - actual_idx) > 1 else '\n'.join(label)

                self.ax.text(x_label + field_w/2, -0.35, label_text,
                             fontsize=8, ha='center', va='bottom',
                             bbox=dict(boxstyle="round,pad=0.2",
                                       edgecolor="black", facecolor="yellow"))

            last_bit   = bit
            actual_idx += 1

        next_key = next((k for k in sorted(frame_labels) if k > actual_idx), actual_idx + 1)
        field_w = (next_key - actual_idx) * bit_duration

        # ───────────────────── trace waveform ────────────────────────────────
        sig_x = [idle_x_end + i * bit_duration for i in range(len(bits)+1)]
        sig_y = bits + [bits[-1]]
        self.ax.step(sig_x, sig_y, where="post", color="blue", linewidth=1.5)

        self.canvas.draw_idle()

# ─────────────────────────────────── main ──────────────────────────────────────

def main():
    root = tk.Tk()
    root.option_add("*Font", "{Segoe UI} 10")   # (ถ้าชอบฟอนต์นี้)

    LogicAnalyzerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
