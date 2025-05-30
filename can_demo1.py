#ใส่ข้อมูลแล้ว plot เลย
"""
CAN Frame Visualizer – Neo‑Dark UI ✨
===================================
✓ Auto‑install **matplotlib** (first run)
✓ Modern dark‑aqua theme (rounded corners & subtle shadow)
✓ Animated hover / pressed effects on buttons
✓ Input box glow on focus
✓ Header gradient bar

Run:
  python can_visualizer_bitstuff.py
"""

# ───────────────────────── AUTO‑INSTALL MATPLOTLIB ─────────────────────────
try:
    import matplotlib  # type: ignore
except ModuleNotFoundError:
    import subprocess, sys, importlib
    print("Installing matplotlib … (one‑time)")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "matplotlib", "--quiet"])
    importlib.invalidate_caches(); matplotlib = importlib.import_module("matplotlib")

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import tkinter as tk
from tkinter import ttk, messagebox

# ───────────────────────── BIT/FRAME UTILITIES ─────────────────────────

def int_to_bits(v: int, n: int):
    return [int(b) for b in f"{v:0{n}b}"]


def build_can_fields(hex_str: str):
    s = hex_str.lower().strip()
    if len(s) < 3 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError("Enter hex string, ≥3 chars (ID+DATA)")
    id_hex, data_hex = s[:3], s[3:]
    data_bytes = [data_hex[i:i+2] for i in range(0, len(data_hex), 2)]
    if len(data_bytes) > 8:
        raise ValueError("Classic CAN supports ≤8 data bytes (DLC 0‑8)")
    fields = [("SOF", [0]), ("ID", int_to_bits(int(id_hex, 16), 11)), ("RTR", [0]), ("IDE", [0]), ("r0", [0]),
              ("DLC", int_to_bits(len(data_bytes), 4))]
    for i, b in enumerate(data_bytes):
        fields.append((f"DATA{i}", int_to_bits(int(b, 16), 8)))
    fields += [("CRC", [0]*15), ("CRC_del", [1]), ("ACK", [0]), ("ACK_del", [1]), ("EOF", [1]*7)]
    return fields

# bit‑stuffing
def bitstuff(bits, until_raw):
    out, pos, cnt, last = [bits[0]], [], 1, bits[0]; raw = 1
    while raw < len(bits):
        b = bits[raw]
        if raw < until_raw and b == last and cnt == 5:
            out.append(1-last); pos.append(len(out)-1); cnt, last = 1, 1-last; continue
        out.append(b); cnt = cnt+1 if b==last else 1; last=b; raw += 1
    return out, pos

# ───────────────────────── PLOTTING ─────────────────────────

def plot_wave(fields):
    raw, secs = [], []
    for n,b in fields: s=len(raw); raw.extend(b); secs.append((n,s,len(b)))
    crc_end = next(s for s in secs if s[0]=="CRC")[1] + 15
    stuffed, stuff_pos = bitstuff(raw, crc_end)
    r2s, st_i = {}, 0
    for rw_i in range(len(raw)):
        while st_i in stuff_pos: st_i+=1
        r2s[rw_i]=st_i; st_i+=1
    sec_map=[(n,r2s[s],r2s[s+l-1]-r2s[s]+1) for n,s,l in secs]

    fig=Figure(figsize=(12,4),dpi=110); ax=fig.add_subplot(111)
    ax.step(range(len(stuffed)+1), stuffed+[stuffed[-1]], where='post', color='#4dd0e1', linewidth=1.6)
    if stuff_pos:
        ax.scatter([p+0.5 for p in stuff_pos],[stuffed[p] for p in stuff_pos],marker='x',color='#ff5252',s=50,label='Stuff bit')
        ax.legend(loc='upper right',fontsize=8,framealpha=0.25)
    ax.set_facecolor('#202124'); fig.patch.set_facecolor('#202124')

    for idx,(n,st,ln) in enumerate(sec_map):
        ax.axvline(st,color='#555',ls='--',lw=0.8)
        text=n.replace('_','\n'); rot=0 if ln>=4 else 90; fs=8 if ln>=4 else 7; y=1.34 if idx%2==0 else 1.18
        if rot: y=1.26
        ax.text(st+ln/2,y,text,ha='center',va='bottom',fontsize=fs,color='#e0e0e0',rotation=rot)
    ax.axvline(sec_map[-1][1]+sec_map[-1][2],color='#555',ls='--',lw=0.8)

    ax.set_ylim(-0.5,1.48); ax.set_yticks([0,1]); ax.set_yticklabels(['0','1'],color='#e0e0e0')
    ax.set_xlabel('Bit Index (after stuffing)',color='#e0e0e0')
    ax.set_title('CAN Frame – Bit‑Stuffing',color='#4dd0e1',pad=10,fontweight='bold')
    for sp in ax.spines.values(): sp.set_color('#e0e0e0')
    ax.tick_params(axis='x',colors='#e0e0e0')
    ax.grid(axis='x',color='#424242',ls='dotted',lw=0.5)
    fig.tight_layout(pad=1)
    return fig

# ───────────────────────── CUSTOM STYLES ─────────────────────────
PRIMARY='#26c6da'; PRIMARY_DARK='#0097a7'; BG='#2b2b2b'; FG='#e0e0e0'

class HoverButton(ttk.Button):
    def __init__(self, master, **kw):
        super().__init__(master, style='Accent.TButton', **kw)
        self.bind('<Enter>', lambda *_: self.configure(style='Hover.TButton'))
        self.bind('<Leave>', lambda *_: self.configure(style='Accent.TButton'))

class PlaceholderEntry(ttk.Entry):
    def __init__(self, master, placeholder, **kw):
        super().__init__(master, **kw); self.ph=placeholder; self._put()
        self.bind('<FocusIn>',self._in); self.bind('<FocusOut>',self._out)
    def _put(self): self.insert(0,self.ph); self['foreground']='#888'; self.configure(style='Placeholder.TEntry')
    def _in(self,_):
        if self.get()==self.ph:
            self.delete(0,'end'); self.configure(style='Active.TEntry'); self['foreground']='#fff'
    def _out(self,_):
        if not self.get(): self._put()

# ───────────────────────── MAIN APP ─────────────────────────
class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        master.title('CAN Frame Visualizer – Classic'); master.configure(bg=BG)
        master.geometry('1180x700'); master.minsize(820,520)

        style=ttk.Style(); style.theme_use('clam')
        style.configure('TFrame',background=BG); style.configure('TLabel',background=BG,foreground=FG)
        style.configure('Headline.TLabel',font=('Segoe UI',16,'bold'),foreground=PRIMARY)
        style.configure('Accent.TButton',background=PRIMARY,foreground='#111',font=('Segoe UI',11,'bold'),padding=10,relief='flat')
        style.configure('Hover.TButton',background=PRIMARY_DARK,foreground='#111',font=('Segoe UI',11,'bold'),padding=10,relief='flat')
        style.configure('Placeholder.TEntry',fieldbackground='#3c3f41',foreground='#888',padding=8,relief='flat')
        style.configure('Active.TEntry',fieldbackground='#455a64',foreground='#fff',padding=8,relief='flat')
        style.configure('TLabelframe', background=BG, foreground=FG, borderwidth=0)
        style.configure('TLabelframe.Label', foreground=FG, font=('Segoe UI',10,'bold'))

        self.pack(fill='both',expand=True)
        # Header gradient bar
        header=tk.Canvas(self,height=4,highlightthickness=0,bg=BG)
        header.pack(fill='x')
        header.create_rectangle(0,0,2000,4,fill=PRIMARY_DARK,outline='')

        ttk.Label(self,text='CAN Frame Visualizer',style='Headline.TLabel').pack(anchor='w',padx=12,pady=(12,6))

        lf=ttk.LabelFrame(self,text='Input Hex Frame'); lf.pack(fill='x',padx=10)
        lf.columnconfigure(1,weight=1)
        self.entry=PlaceholderEntry(lf,'e.g. 7FF1122334455667788',font=('Consolas',12)); self.entry.grid(row=0,column=1,sticky='ew',padx=6,pady=8)
        HoverButton(lf,text='🔍  Plot',command=self._plot).grid(row=0,column=2,padx=4)
        HoverButton(lf,text='⭮  Reset',command=self._reset).grid(row=0,column=3,padx=(0,8))

        self.plot_fr=ttk.Frame(self); self.plot_fr.pack(fill='both',expand=True,padx=10,pady=12)
        self.canvas=None

    def _plot(self):
        v=self.entry.get();
        if v==self.entry.ph: messagebox.showinfo('Info','Please enter a hex frame',parent=self); return
        try: fig=plot_wave(build_can_fields(v))
        except ValueError as e: messagebox.showerror('Error',str(e),parent=self); return
        if self.canvas: self.canvas.get_tk_widget().destroy()
        self.canvas=FigureCanvasTkAgg(fig,master=self.plot_fr); self.canvas.draw(); self.canvas.get_tk_widget().pack(fill='both',expand=True)

    def _reset(self):
        self.entry.delete(0,'end'); self.entry._put();
        if self.canvas: self.canvas.get_tk_widget().destroy(); self.canvas=None

# ───────────────────────── RUN ─────────────────────────
if __name__=='__main__':
    root=tk.Tk(); App(root); root.mainloop()
