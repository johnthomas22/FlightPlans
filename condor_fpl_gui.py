#!/usr/bin/env python3
"""
condor_fpl_gui.py
-----------------
Tkinter GUI wrapper for the Condor FPL Generator.

Usage:
    python condor_fpl_gui.py

Requires:
    pip install pdfplumber
"""

import json
import math
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".condor_fpl_gui.json")

DEFAULT_FPL_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Condor3", "FlightPlans"
)

DEFAULT_CUP_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Condor3", "Turnpoints"
)

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Condor3", "FlightPlans"
)

DEFAULT_XCSOAR_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "XCSoarData", "tasks"
)


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings: dict):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers (replicate calc_task_distance without importing the whole module
# to avoid import errors if dependencies are missing at startup)
# ---------------------------------------------------------------------------

def _calc_distance(tps: list) -> float:
    dist = 0.0
    for i in range(len(tps) - 1):
        dx = tps[i + 1]["x"] - tps[i]["x"]
        dy = tps[i + 1]["y"] - tps[i]["y"]
        dist += math.sqrt(dx ** 2 + dy ** 2)
    return dist / 1000.0


def _fmt_latlon(lat, lon) -> str:
    """Format decimal lat/lon as a readable string, e.g. '42.8117°N  013.2090°E'."""
    if lat is None or lon is None:
        return "—"
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}\u00b0{lat_h}  {abs(lon):>8.4f}\u00b0{lon_h}"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Condor FPL Generator")
        self.resizable(True, True)
        self.minsize(700, 540)
        self.geometry("920x620")

        self._settings = load_settings()
        self._task = None          # parsed + resolved task dict
        self._pdf_path = tk.StringVar()
        self._fpl_dir  = tk.StringVar(value=self._settings.get("fpl_dir", DEFAULT_FPL_DIR))
        self._cup_dir  = tk.StringVar(value=self._settings.get("cup_dir", DEFAULT_CUP_DIR))
        self._out_path  = tk.StringVar()
        self._xcsoar_tsk = tk.StringVar(value=self._settings.get("xcsoar_tsk", ""))
        self._gen_xcsoar = tk.BooleanVar(value=self._settings.get("gen_xcsoar", True))
        self._status    = tk.StringVar(value="Ready — browse for a task briefing PDF to begin.")

        self._build_ui()
        self._toggle_xcsoar()   # apply initial enabled/disabled state

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = dict(padx=8, pady=4)

        # ---- Top frame: inputs ----------------------------------------
        top = ttk.LabelFrame(self, text="Input", padding=6)
        top.pack(fill="x", padx=10, pady=(10, 4))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Task PDF:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self._pdf_path).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse…", command=self._browse_pdf).grid(row=0, column=2, **pad)

        ttk.Label(top, text="FPL Dir:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self._fpl_dir).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse…", command=self._browse_fpl_dir).grid(row=1, column=2, **pad)

        ttk.Label(top, text="Turnpoints:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self._cup_dir).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse…", command=self._browse_cup_dir).grid(row=2, column=2, **pad)

        # ---- Middle: notebook with Task Details + Strategy tabs --------
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=4)

        mid = ttk.Frame(nb, padding=6)
        nb.add(mid, text="Task Details")
        mid.columnconfigure(1, weight=1)
        mid.columnconfigure(3, weight=1)

        # ---- Routing tab -----------------------------------------------
        tab_routing = ttk.Frame(nb, padding=6)
        nb.add(tab_routing, text="Routing")
        tab_routing.rowconfigure(0, weight=1)
        tab_routing.columnconfigure(0, weight=1)

        self._routing_text = tk.Text(
            tab_routing, wrap="word", state="disabled",
            font=("Segoe UI", 10), relief="flat", padx=8, pady=6, height=12,
        )
        # Text tags for formatting
        self._routing_text.tag_configure("overall", font=("Segoe UI", 10, "bold"))
        self._routing_text.tag_configure("leg_hdr", font=("Segoe UI", 10, "bold"),
                                          foreground="#1a5276")
        self._routing_text.tag_configure("bullet",  lmargin1=20, lmargin2=32)
        self._routing_text.tag_configure("tailwind", foreground="#1a7a1a")
        self._routing_text.tag_configure("headwind", foreground="#a93226")
        self._routing_text.tag_configure("neutral",  foreground="#7d6608")

        rout_vsb = ttk.Scrollbar(tab_routing, orient="vertical",
                                  command=self._routing_text.yview)
        self._routing_text.configure(yscrollcommand=rout_vsb.set)
        self._routing_text.grid(row=0, column=0, sticky="nsew")
        rout_vsb.grid(row=0, column=1, sticky="ns")

        # ---- Strategy tab -----------------------------------------------
        tab_strat = ttk.Frame(nb, padding=6)
        nb.add(tab_strat, text="Strategy")
        tab_strat.rowconfigure(0, weight=1)
        tab_strat.columnconfigure(0, weight=1)

        self._strategy_text = tk.Text(
            tab_strat, wrap="word", state="disabled",
            font=("Courier New", 9), relief="flat", height=12,
        )
        strat_vsb = ttk.Scrollbar(tab_strat, orient="vertical",
                                   command=self._strategy_text.yview)
        self._strategy_text.configure(yscrollcommand=strat_vsb.set)
        self._strategy_text.grid(row=0, column=0, sticky="nsew")
        strat_vsb.grid(row=0, column=1, sticky="ns")

        # Info grid
        info_fields = [
            ("Description:", "_lbl_desc",  "Aircraft:",   "_lbl_ac"),
            ("Landscape:",   "_lbl_ls",    "Date / Time:","_lbl_dt"),
            ("Airport:",     "_lbl_apt",   "Start Ht:",   "_lbl_sth"),
            ("Max Speed:",   "_lbl_spd",   "Wind:",       "_lbl_wind"),
        ]

        for row_i, (l1, attr1, l2, attr2) in enumerate(info_fields):
            ttk.Label(mid, text=l1, foreground="gray").grid(
                row=row_i, column=0, sticky="w", padx=(4, 2), pady=1)
            lbl1 = ttk.Label(mid, text="—")
            lbl1.grid(row=row_i, column=1, sticky="w", padx=(0, 12), pady=1)
            setattr(self, attr1, lbl1)

            ttk.Label(mid, text=l2, foreground="gray").grid(
                row=row_i, column=2, sticky="w", padx=(4, 2), pady=1)
            lbl2 = ttk.Label(mid, text="—")
            lbl2.grid(row=row_i, column=3, sticky="w", padx=(0, 4), pady=1)
            setattr(self, attr2, lbl2)

        # Route treeview
        ttk.Label(mid, text="Route:", foreground="gray").grid(
            row=len(info_fields), column=0, sticky="w", padx=(4, 2), pady=(6, 1))

        tree_frame = ttk.Frame(mid)
        tree_frame.grid(row=len(info_fields) + 1, column=0, columnspan=4,
                        sticky="ew", padx=4, pady=(0, 4))

        cols = ("#", "Type", "Name", "Radius", "Angle", "Coords")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=5)
        for c, w in zip(cols, (30, 55, 140, 70, 55, 230)):
            self._tree.heading(c, text=c)
            self._tree.column(c, width=w, anchor="w" if c in ("Name", "Coords") else "center",
                              stretch=(c == "Coords"))
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._lbl_dist = ttk.Label(mid, text="")
        self._lbl_dist.grid(row=len(info_fields) + 2, column=0, columnspan=4,
                            sticky="e", padx=4, pady=(0, 2))

        # ---- Bottom frame: output + generate ---------------------------
        bot = ttk.LabelFrame(self, text="Output", padding=6)
        bot.pack(fill="x", padx=10, pady=4)
        bot.columnconfigure(1, weight=1)

        ttk.Label(bot, text="Output FPL:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(bot, textvariable=self._out_path).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(bot, text="Browse…", command=self._browse_out).grid(row=0, column=2, **pad)

        ttk.Checkbutton(
            bot, text="Also generate XCSoar .tsk file", variable=self._gen_xcsoar,
            command=self._toggle_xcsoar,
        ).grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(bot, text="XCSoar TSK:").grid(row=2, column=0, sticky="w", **pad)
        self._xcsoar_entry = ttk.Entry(bot, textvariable=self._xcsoar_tsk)
        self._xcsoar_entry.grid(row=2, column=1, sticky="ew", **pad)
        self._xcsoar_browse_btn = ttk.Button(bot, text="Browse…", command=self._browse_xcsoar_tsk)
        self._xcsoar_browse_btn.grid(row=2, column=2, **pad)

        self._gen_btn = ttk.Button(
            bot, text="Generate FPL", command=self._on_generate,
            state="disabled", style="Accent.TButton",
        )
        self._gen_btn.grid(row=3, column=0, columnspan=3, pady=(6, 2))

        # ---- Status bar ------------------------------------------------
        status_bar = ttk.Label(self, textvariable=self._status,
                               relief="sunken", anchor="w", padding=(6, 2))
        status_bar.pack(fill="x", padx=0, pady=(4, 0), side="bottom")

    # ------------------------------------------------------------------
    # Browse callbacks
    # ------------------------------------------------------------------

    def _browse_pdf(self):
        init_dir = os.path.dirname(self._pdf_path.get()) or self._settings.get(
            "last_pdf_dir", os.path.expanduser("~"))
        path = filedialog.askopenfilename(
            title="Select task briefing PDF",
            initialdir=init_dir,
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            path = os.path.normpath(path)
            self._pdf_path.set(path)
            self._settings["last_pdf_dir"] = os.path.dirname(path)
            save_settings(self._settings)
            self._suggest_output(path)
            self._on_load()

    def _browse_fpl_dir(self):
        init_dir = self._fpl_dir.get() or DEFAULT_FPL_DIR
        path = filedialog.askdirectory(title="Select FPL lookup directory", initialdir=init_dir)
        if path:
            path = os.path.normpath(path)
            self._fpl_dir.set(path)
            self._settings["fpl_dir"] = path
            save_settings(self._settings)

    def _browse_cup_dir(self):
        init_dir = self._cup_dir.get() or DEFAULT_CUP_DIR
        path = filedialog.askdirectory(title="Select Turnpoints directory (.cup files)",
                                       initialdir=init_dir)
        if path:
            path = os.path.normpath(path)
            self._cup_dir.set(path)
            self._settings["cup_dir"] = path
            save_settings(self._settings)

    def _toggle_xcsoar(self):
        state = "normal" if self._gen_xcsoar.get() else "disabled"
        self._xcsoar_entry.config(state=state)
        self._xcsoar_browse_btn.config(state=state)
        self._settings["gen_xcsoar"] = self._gen_xcsoar.get()
        save_settings(self._settings)

    def _browse_xcsoar_tsk(self):
        current = self._xcsoar_tsk.get()
        init_dir = os.path.dirname(current) if current else DEFAULT_XCSOAR_DIR
        init_file = os.path.basename(current) if current else "task.tsk"
        path = filedialog.asksaveasfilename(
            title="Save XCSoar task as",
            initialdir=init_dir,
            initialfile=init_file,
            defaultextension=".tsk",
            filetypes=[("XCSoar task files", "*.tsk"), ("All files", "*.*")],
        )
        if path:
            path = os.path.normpath(path)
            self._xcsoar_tsk.set(path)
            self._settings["xcsoar_tsk"] = path
            save_settings(self._settings)

    def _browse_out(self):
        init_dir = os.path.dirname(self._out_path.get()) or self._settings.get(
            "last_out_dir", DEFAULT_OUTPUT_DIR)
        init_file = os.path.basename(self._out_path.get()) or "race.fpl"
        path = filedialog.asksaveasfilename(
            title="Save FPL as",
            initialdir=init_dir,
            initialfile=init_file,
            defaultextension=".fpl",
            filetypes=[("FPL files", "*.fpl"), ("All files", "*.*")],
        )
        if path:
            path = os.path.normpath(path)
            self._out_path.set(path)
            self._settings["last_out_dir"] = os.path.dirname(path)
            save_settings(self._settings)

    def _suggest_output(self, pdf_path: str):
        """Pre-fill the output FPL and XCSoar TSK paths based on the PDF name."""
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        # FPL output
        out_dir = self._settings.get("last_out_dir", DEFAULT_OUTPUT_DIR)
        self._out_path.set(os.path.normpath(os.path.join(out_dir, base + ".fpl")))
        # XCSoar TSK output: only auto-fill when the checkbox is enabled
        if self._gen_xcsoar.get():
            saved_tsk = self._settings.get("xcsoar_tsk", "")
            xcsoar_dir = os.path.dirname(saved_tsk) if saved_tsk else DEFAULT_XCSOAR_DIR
            self._xcsoar_tsk.set(os.path.normpath(os.path.join(xcsoar_dir, base + ".tsk")))

    # ------------------------------------------------------------------
    # Load PDF
    # ------------------------------------------------------------------

    def _on_load(self):
        pdf = self._pdf_path.get().strip()
        if not pdf:
            messagebox.showwarning("No PDF", "Please select a task briefing PDF first.")
            return
        if not os.path.isfile(pdf):
            messagebox.showerror("File not found", f"Cannot find:\n{pdf}")
            return

        fpl_dir = self._fpl_dir.get().strip()
        if not os.path.isdir(fpl_dir):
            messagebox.showerror("FPL Dir not found",
                                 f"FPL directory does not exist:\n{fpl_dir}\n\n"
                                 f"Please select a valid directory of .fpl files.")
            return

        self._set_status("Parsing PDF and resolving coordinates…")
        self._gen_btn.config(state="disabled")
        self._task = None
        self._clear_details()

        cup_dir = self._cup_dir.get().strip() or None

        # Run in background thread so the UI stays responsive
        threading.Thread(target=self._load_worker, args=(pdf, fpl_dir, cup_dir),
                         daemon=True).start()

    def _load_worker(self, pdf_path: str, fpl_dir: str, cup_dir: str):
        """Worker thread: parse PDF + resolve coords, then update UI."""
        try:
            # Import here so startup doesn't fail if pdfplumber is absent
            from condor_fpl_gen import pdf_to_task, calc_task_distance, generate_strategy
            task = pdf_to_task(pdf_path, fpl_dir, cup_dir)
            dist = calc_task_distance(task["turnpoints"])
            strategy = generate_strategy(task)
            self.after(0, self._on_load_success, task, dist, strategy)
        except SystemExit as e:
            self.after(0, self._on_load_error,
                       f"Could not resolve all turnpoints.\n\n"
                       f"Make sure the FPL Dir contains .fpl files that include the "
                       f"turnpoints for this task, or that the Turnpoints directory "
                       f"contains the correct .cup file for this landscape.")
        except ImportError as e:
            self.after(0, self._on_load_error,
                       f"Missing dependency:\n{e}\n\nRun:  pip install pdfplumber")
        except Exception as e:
            self.after(0, self._on_load_error, str(e))

    def _on_load_success(self, task: dict, dist: float, strategy: str):
        self._task = task
        self._populate_details(task, dist)
        self._show_routing(strategy)
        self._show_strategy(strategy)
        self._gen_btn.config(state="normal")
        n_tps = len(task["turnpoints"])
        self._set_status(
            f"Loaded: {task.get('description', 'task')}  —  "
            f"{n_tps} TPs, {dist:.1f} km"
        )

    def _on_load_error(self, msg: str):
        messagebox.showerror("Load failed", msg)
        self._set_status(f"Error loading PDF — see dialog for details.")

    # ------------------------------------------------------------------
    # Populate details panel
    # ------------------------------------------------------------------

    def _clear_details(self):
        for attr in ("_lbl_desc", "_lbl_ac", "_lbl_ls", "_lbl_dt",
                     "_lbl_apt", "_lbl_sth", "_lbl_spd", "_lbl_wind"):
            getattr(self, attr).config(text="—")
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._lbl_dist.config(text="")
        self._show_routing("")
        self._show_strategy("")

    def _show_strategy(self, text: str):
        """Replace the contents of the Strategy tab text widget."""
        self._strategy_text.config(state="normal")
        self._strategy_text.delete("1.0", "end")
        if text:
            self._strategy_text.insert("1.0", text)
        self._strategy_text.config(state="disabled")

    def _show_routing(self, strategy: str):
        """Extract ROUTING NOTES from the strategy and display with formatting."""
        import re
        widget = self._routing_text
        widget.config(state="normal")
        widget.delete("1.0", "end")

        if not strategy:
            widget.config(state="disabled")
            return

        # Extract the ROUTING NOTES section (stops at the next all-caps section)
        m = re.search(
            r'ROUTING NOTES\n[─]+\n(.*?)(?=\n[A-Z ]{4,}\n[─═]+|\Z)',
            strategy, re.DOTALL
        )
        if not m:
            widget.insert("end", "(No routing notes available.)")
            widget.config(state="disabled")
            return

        body = m.group(1).rstrip()

        for raw_line in body.split("\n"):
            line = raw_line.rstrip()

            # "Overall:" summary line
            if line.strip().startswith("Overall:"):
                widget.insert("end", line.strip() + "\n\n", "overall")

            # Leg header: "  Leg N (From → To):"
            elif re.match(r'\s+Leg \d+', line) or re.match(r'\s+Leg \d+', line):
                widget.insert("end", line.strip() + "\n", "leg_hdr")

            # Bullet points
            elif line.strip().startswith("•"):
                text_body = line.strip()[1:].strip()
                # Colour-code by sentiment
                if any(w in text_body for w in ("tailwind", "Tailwind", "favourable", "dolphin")):
                    tag = "tailwind"
                elif any(w in text_body for w in ("headwind", "Headwind", "difficult", "faster")):
                    tag = "headwind"
                else:
                    tag = "bullet"
                widget.insert("end", "  • " + text_body + "\n", tag)

            # Skip blank lines between legs — add a small gap instead
            elif line.strip() == "":
                widget.insert("end", "\n")

            else:
                widget.insert("end", line + "\n")

        widget.config(state="disabled")

    def _populate_details(self, task: dict, dist: float):
        wx = task.get("weather", {})

        self._lbl_desc.config(text=task.get("description", "—"))
        self._lbl_ac.config(text=task.get("aircraft", "—"))
        self._lbl_ls.config(text=task.get("landscape", "—"))

        date = task.get("task_date", "")
        hour = task.get("start_time", "")
        self._lbl_dt.config(text=f"{date}  {hour}:00" if date else "—")

        apt = task.get("airport_tp", {})
        self._lbl_apt.config(text=apt.get("name", "—"))
        self._lbl_sth.config(text=f"{task.get('start_height_m', '—')} m AGL")

        spd = task.get("max_start_speed_kts")
        self._lbl_spd.config(text=f"{spd} kts" if spd else "—")

        wd  = wx.get("wind_dir_deg", "—")
        ws  = wx.get("wind_speed_kts", "—")
        self._lbl_wind.config(text=f"{wd}\u00b0 @ {ws} kts")

        # Route tree
        for item in self._tree.get_children():
            self._tree.delete(item)

        tps = task.get("turnpoints", [])
        for i, tp in enumerate(tps):
            if i == 0:
                label = "S"
            elif i == len(tps) - 1:
                label = "F"
            else:
                label = str(i + 1)

            coords_str = _fmt_latlon(tp.get("lat"), tp.get("lon"))
            type_str   = "Cyl" if tp.get("angle_deg", 360) == 360 else "Sector"
            self._tree.insert("", "end", values=(
                label,
                type_str,
                tp["name"],
                f"{tp['radius_m']} m",
                f"{tp['angle_deg']}\u00b0",
                coords_str,
            ))

        # Airport row (displayed separately at top with tag)
        if apt:
            self._tree.insert("", 0, values=(
                "A",
                "Cyl",
                apt.get("name", ""),
                "3000 m",
                "90\u00b0",
                _fmt_latlon(apt.get("lat"), apt.get("lon")),
            ), tags=("airport",))
            self._tree.tag_configure("airport", foreground="gray")

        self._lbl_dist.config(text=f"Task distance: {dist:.1f} km")

        # Auto-size Name column to fit the longest name
        all_names = [tp["name"] for tp in tps]
        if apt:
            all_names.append(apt.get("name", ""))
        if all_names:
            from tkinter import font as tkfont
            f = tkfont.nametofont("TkDefaultFont")
            max_w = max(f.measure(n) for n in all_names)
            self._tree.column("Name", width=max(max_w + 16, 60))

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def _on_generate(self):
        if not self._task:
            messagebox.showwarning("No task", "Please load a PDF first.")
            return

        out = self._out_path.get().strip()
        if not out:
            messagebox.showwarning("No output path", "Please specify an output .fpl filename.")
            return

        out_dir = os.path.dirname(out)
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Cannot create directory", str(e))
                return

        if os.path.isfile(out):
            if not messagebox.askyesno(
                "Overwrite?",
                f"This file already exists:\n{out}\n\nOverwrite it?",
                icon="warning",
            ):
                return

        # --- XCSoar .tsk path (optional) -----------------------------------
        tsk_path = self._xcsoar_tsk.get().strip() if self._gen_xcsoar.get() else ""

        tps_have_latlon = all(
            tp.get("lat") is not None for tp in self._task.get("turnpoints", [])
        )
        write_tsk = bool(tsk_path and tps_have_latlon)

        if write_tsk and os.path.isfile(tsk_path):
            if not messagebox.askyesno(
                "Overwrite XCSoar task?",
                f"This XCSoar task file already exists:\n{tsk_path}\n\nOverwrite it?",
                icon="warning",
            ):
                write_tsk = False

        try:
            from condor_fpl_gen import build_fpl, build_xcsoar_tsk
            content = build_fpl(self._task)
            with open(out, "w", newline="\r\n", encoding="utf-8") as f:
                f.write(content)
            if write_tsk:
                tsk_dir = os.path.dirname(tsk_path)
                if tsk_dir and not os.path.isdir(tsk_dir):
                    os.makedirs(tsk_dir, exist_ok=True)
                with open(tsk_path, "w", encoding="utf-8") as f:
                    f.write(build_xcsoar_tsk(self._task))
        except Exception as e:
            messagebox.showerror("Generate failed", str(e))
            self._set_status("Generation failed.")
            return

        self._settings["last_out_dir"] = os.path.dirname(out)
        if write_tsk:
            self._settings["xcsoar_tsk"] = tsk_path
        save_settings(self._settings)

        msg = f"FPL file written:\n{out}"
        if write_tsk:
            msg += f"\n\nXCSoar task written:\n{tsk_path}"
        elif tsk_path and not tps_have_latlon:
            msg += f"\n\n(lat/lon missing from turnpoints — .tsk not written)"

        self._set_status(f"Done — written: {out}" + (f"  +  {tsk_path}" if write_tsk else ""))
        self._show_wide_info("Success", msg)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _show_wide_info(self, title: str, msg: str):
        """Show an info dialog wide enough that long file paths don't wrap."""
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text=msg, justify="left", padding=(16, 12)).pack()
        ttk.Button(dlg, text="OK", command=dlg.destroy, width=8).pack(pady=(0, 12))
        dlg.update_idletasks()
        # Enforce a minimum width so paths don't wrap
        min_w = 520
        if dlg.winfo_width() < min_w:
            dlg.geometry(f"{min_w}x{dlg.winfo_height()}")
        dlg.wait_window()

    def _set_status(self, msg: str):
        self._status.set(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # On Windows, prevent the console window from showing when run as .exe
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    app = App()
    # Try to apply a modern theme if available
    try:
        style = ttk.Style(app)
        available = style.theme_names()
        for preferred in ("vista", "clam", "alt"):
            if preferred in available:
                style.theme_use(preferred)
                break
        # Style for the Generate button
        style.configure("Accent.TButton", font=("", 10, "bold"))
    except Exception:
        pass

    app.mainloop()


if __name__ == "__main__":
    main()
