"""The Tkinter window of the WinTAX ECU downloader.

Each session call runs on a worker thread. The session calls on_event. The callback only puts the event on a queue.
The window reads the queue on the Tk thread every 100 ms. No other thread touches a widget.
"""
import ipaddress
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from contextlib import closing
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from wtxapp import __version__
from wtxapp.session import RunSession, wtx_client

TITLE = f"WinTAX ECU Downloader {__version__}"
CONFIG_PATH = Path.home() / ".wtxapp.json"
DEFAULTS = {"ecu_ip": wtx_client.ECU_IP_DEFAULT, "input": "", "output": ""}
LOG_LINES = 5000  # ponytail: the log pane keeps the last 5000 lines. The run log file keeps every line.


def load_config(path=CONFIG_PATH):
    """Return the saved settings. A missing or unreadable file gives the defaults."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    return {k: data[k] if isinstance(data.get(k), str) else v for k, v in DEFAULTS.items()}


def save_config(cfg, path=CONFIG_PATH):
    """Write the settings. A file that cannot be written does not stop the app."""
    try:
        Path(path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def check_ip(text):
    """Return the ECU IP. Raise ValueError unless it is an IPv4 address. No DNS lookup runs."""
    ip = text.strip()
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        raise ValueError(f"Enter an IPv4 address for the ECU, for example {wtx_client.ECU_IP_DEFAULT}.") from None
    return ip


def check_output(output, input_path=""):
    """Return the output folder. Raise ValueError when it is empty or inside the input folder.

    The session writes its logs and downloads under the output folder, so the input folder must stay unchanged.
    """
    if not output.strip():
        raise ValueError("Choose an output folder first.")
    out = Path(output.strip()).resolve()
    if input_path.strip():
        src = Path(input_path.strip()).resolve()
        tree = src if src.is_dir() else src.parent
        if out == tree or tree in out.parents:
            raise ValueError("The output folder is inside the input folder. Choose another output folder.")
    return out


def progress_text(ev, elapsed):
    """Return the metrics line for one progress event: bytes, throughput and elapsed time."""
    return f"{ev['bytes']:,} bytes, {ev['rate_bps'] / 1000:.1f} kB/s, {elapsed:.1f} s elapsed"


def beacon_text(beacon):
    """Return the result of Test connection as one status line."""
    if beacon is None:
        return "No ECU found."
    return (f"The ECU is on the network. Its name is {beacon.name}, its alias is {beacon.alias} "
            f"and its state is {beacon.state}.")


def run_if_confirmed(confirm, start):
    """Call start only when confirm returns True. Return True when start ran."""
    if confirm() is not True:
        return False
    start()
    return True


# Each job runs one session call. It returns the fields for the end event, or None.
def _test(s, inp):
    return {"msg": beacon_text(s.discover())}


def _download(s, inp):
    s.download()


def _decode(s, inp):
    s.decode(inp)


def _package(s, inp):
    zip_path = s.package_results()
    return {"msg": f"The results zip is ready: {zip_path}", "path": zip_path}


def open_folder(path):
    """Show a folder in the OS file browser. Do not wait for the browser."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    except OSError:
        pass  # No file browser opened. The user can open the folder by hand.


class App:
    """The main window. Build it on a Tk root, then run the root mainloop."""

    def __init__(self, root):
        self.root = root
        self.events = queue.Queue(maxsize=10000)  # ponytail: bounded. The window drains it every 100 ms, so it stays short.
        self.busy = False
        self.t0 = 0.0
        cfg = load_config()
        self.ip_var, self.in_var, self.out_var = (tk.StringVar(value=cfg[k]) for k in ("ecu_ip", "input", "output"))
        self.status = tk.StringVar(value="The app is ready.")
        self.metrics = tk.StringVar()
        for var in (self.ip_var, self.in_var, self.out_var):
            var.trace_add("write", self._save)  # Save each change. The last values survive a crash.
        root.title(TITLE)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = ttk.Frame(root, padding=10)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)

        ttk.Label(frame, text="ECU IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.ip_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=2)
        ttk.Label(frame, text="Input (optional)").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.in_var).grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(frame, text="Browse file", command=self.browse_file).grid(row=1, column=2, padx=2)
        ttk.Button(frame, text="Browse folder", command=self.browse_folder).grid(row=1, column=3, padx=2)
        ttk.Label(frame, text="Output folder").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.out_var).grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(frame, text="Browse", command=self.browse_output).grid(row=2, column=2, columnspan=2,
                                                                          sticky="ew", padx=2)

        bar = ttk.Frame(frame)
        bar.grid(row=3, column=0, columnspan=4, sticky="w", pady=6)
        actions = (("Test connection", self.on_test), ("Download", self.on_download), ("Decode", self.on_decode),
                   ("Open results folder", self.on_open), ("Package to send", self.on_package))
        self.buttons = [ttk.Button(bar, text=text, command=cmd) for text, cmd in actions]
        for button in self.buttons:
            button.pack(side="left", padx=(0, 6))
        ttk.Label(frame, textvariable=self.status).grid(row=4, column=0, columnspan=4, sticky="w")
        self.bar = ttk.Progressbar(frame, mode="indeterminate")  # ponytail: the download size is unknown, so no percent.
        self.bar.grid(row=5, column=0, columnspan=4, sticky="ew", pady=2)
        ttk.Label(frame, textvariable=self.metrics).grid(row=6, column=0, columnspan=4, sticky="w")
        self.log = scrolledtext.ScrolledText(frame, height=14, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, columnspan=4, sticky="nsew")
        self._job = root.after(100, self._drain)

    def browse_file(self):
        path = filedialog.askopenfilename(title="Choose a cableData.ztx file",
                                          filetypes=[("cableData.ztx", "cableData.ztx"), ("All files", "*.*")])
        if path:
            self.in_var.set(path)

    def browse_folder(self):
        path = filedialog.askdirectory(title="Choose a folder with lap files")
        if path:
            self.in_var.set(path)

    def browse_output(self):
        path = filedialog.askdirectory(title="Choose the output folder")
        if path:
            self.out_var.set(path)

    def on_test(self):
        found = self._inputs()
        if found:
            self._start("The ECU search starts. Wait for the result.", found, _test)

    def on_download(self):
        found = self._inputs()
        if found:
            run_if_confirmed(self._confirm_arm, lambda: self._start(
                "The download starts. The ECU logger stops while the download runs.", found, _download))

    def on_decode(self):
        found = self._inputs()
        if found:
            self._start("The decode starts. Wait for the result.", found, _decode)

    def on_package(self):
        found = self._inputs()
        if found:
            self._start("The packing starts. Wait for the zip file.", found, _package)

    def on_open(self):
        try:
            out = check_output(self.out_var.get())
        except ValueError as e:
            messagebox.showerror(TITLE, str(e))
            return
        if out.is_dir():
            open_folder(out)
        else:
            messagebox.showerror(TITLE, "The output folder does not exist yet.")

    def on_close(self):
        if self.busy and not messagebox.askyesno(TITLE, "An operation is still running. Close the app anyway?"):
            return
        self.root.after_cancel(self._job)
        self.root.destroy()

    def _confirm_arm(self):
        return messagebox.askyesno(TITLE, "This arms the ECU and stops its logger while the download runs. Continue?")

    def _inputs(self):
        """Return (ip, output, input) when the fields pass. Otherwise show the reason and return None."""
        try:
            ip = check_ip(self.ip_var.get())
            out = check_output(self.out_var.get(), self.in_var.get())
        except ValueError as e:
            messagebox.showerror(TITLE, str(e))
            return None
        return ip, str(out), self.in_var.get().strip()

    def _start(self, label, found, job):
        """Run one job on a worker thread. The window changes only on the Tk thread."""
        self.status.set(label)
        self.metrics.set("")
        self.t0 = time.monotonic()
        self._busy(True)  # ponytail: no job queue. The buttons stay disabled while one job runs.
        threading.Thread(target=self._worker, args=(*found, job), daemon=True).start()

    def _worker(self, ip, out, inp, job):
        end = {}
        try:
            with closing(RunSession(out, ecu_ip=ip, on_event=self.events.put)) as s:
                end = job(s, inp) or {}
        except Exception as e:  # Any failure must still end the job, or the buttons stay disabled.
            self.events.put({"type": "error", "msg": f"The run stopped. {type(e).__name__}: {e}"})
        self.events.put({"type": "end", **end})

    def _drain(self):
        """Apply the queued events on the Tk thread. Schedule this method again in 100 ms."""
        self._job = self.root.after(100, self._drain)
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            self._apply(ev)

    def _apply(self, ev):
        kind = ev.get("type")
        if kind in ("phase", "done"):
            self.status.set(ev.get("msg", ""))
        elif kind == "log":
            self._append(ev.get("msg", ""))
        elif kind == "progress":
            self.metrics.set(progress_text(ev, time.monotonic() - self.t0))
        elif kind == "error":
            self.status.set(ev.get("msg", "The run failed."))
        elif kind == "end":
            self._busy(False)
            if ev.get("msg"):
                self.status.set(ev["msg"])
            if ev.get("path"):
                open_folder(Path(ev["path"]).parent)

    def _busy(self, busy):
        self.busy = busy
        for button in self.buttons:
            button.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.bar.start(12)
        else:
            self.bar.stop()

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.delete("1.0", f"end-{LOG_LINES}l")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _save(self, *_):
        save_config({"ecu_ip": self.ip_var.get(), "input": self.in_var.get(), "output": self.out_var.get()})


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
