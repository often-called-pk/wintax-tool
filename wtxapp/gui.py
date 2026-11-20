"""The Tkinter window of the WinTAX ECU downloader.

Each session call runs on a worker thread. The session calls on_event. The callback only puts the event on a queue.
The window reads the queue on the Tk thread every 100 ms. No other thread touches a widget.
The window keeps one RunSession for each output folder. Every button uses it, so the ECU identity that Test
connection reads reaches the download record and the name of the results zip.
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
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from wtxapp import __version__
from wtxapp.session import RunSession, wtx_client

TITLE = f"WinTAX ECU Downloader {__version__}"
CONFIG_PATH = Path.home() / ".wtxapp.json"
DEFAULTS = {"ecu_ip": wtx_client.ECU_IP_DEFAULT, "input": "", "output": ""}
LOG_LINES = 5000  # ponytail: the log pane keeps the last 5000 lines. The run log file keeps every line.
WRAP = 620  # ponytail: fixed pixels. A long status text wraps here, so the window keeps its width.


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


def check_output(output, input_path="", decrypt=False):
    """Return the output folder. Raise ValueError when it is empty or inside the Input folder.

    The session writes its logs and downloads under the output folder.
    A Decode must leave the Input folder unchanged, so the output folder cannot lie inside it.
    An Input that is a file, or a path that does not exist, has no folder to protect. A new folder beside it is fine.
    A Decrypt (decrypt=True) is stricter. It must leave the folder of the input file unchanged, so the output folder
    cannot lie inside that folder.
    """
    if not output.strip():
        raise ValueError("Choose an output folder first.")
    out = Path(output.strip()).resolve()
    if input_path.strip():
        src = Path(input_path.strip()).resolve()
        if src.is_dir() and out.is_relative_to(src):
            raise ValueError("The output folder is inside the folder in the Input box. Choose another output folder.")
        if decrypt and not src.is_dir() and out.is_relative_to(src.parent):
            raise ValueError("The output folder is inside the folder of the input file. Choose another output folder.")
    return out


def progress_text(ev, elapsed):
    """Return the metrics line for one progress event: bytes, throughput and elapsed time."""
    return f"{ev['bytes']:,} bytes, {ev['rate_bps'] / 1000:.1f} kB/s, {elapsed:.1f} s elapsed"


def beacon_text(beacon, ecu_ip=None):
    """Return the result of Test connection as one status line.

    ecu_ip is the address in the ECU IP box. When the beacon shows another address, the line says so first.
    """
    if beacon is None:
        return "No ECU found."
    text = (f"The ECU is on the network. Its name is {beacon.name}, its alias is {beacon.alias} "
            f"and its state is {beacon.state}.")
    if ecu_ip and beacon.ip != ecu_ip:
        text = (f"The IP does not match. The ECU reports {beacon.ip}, but the ECU IP box has {ecu_ip}. "
                f"Change the box to {beacon.ip}. {text}")
    return text


def error_text(ev):
    """Return the status text of an error event: the message and the hint, then the cause when the event has one."""
    text = " ".join(part for part in (ev.get("msg") or "The run failed.", ev.get("hint")) if part)
    return f"{text}\nCause: {ev['error']}" if ev.get("error") else text


def run_if_confirmed(confirm, start):
    """Call start only when confirm returns True. Return True when start ran."""
    if confirm() is not True:
        return False
    start()
    return True


# Each job runs one session call. It returns the fields for the end event, or None.
# The end event takes msg (the status line), path (a result file: its folder opens) and summary (the metrics line).
def _test(s, inp):
    before = s.metrics.get("error")  # The metrics keep the last error. A new error object means that this call failed.
    beacon = s.discover()
    error = s.metrics.get("error")
    if beacon is None and error is not before:
        return {"msg": f"The ECU search failed. {error['type']}: {error['msg']}"}
    return {"msg": beacon_text(beacon, s.ecu_ip)}  # s.ecu_ip is the address in the ECU IP box.


def _download(s, inp):
    if s.download():
        # The last progress event shows the rate of the last 0.2 s. After an idle end that is 0.0. Show the average.
        d = s.metrics["download"]
        stats = {"bytes": d["bytes"], "rate_bps": d["throughput_bps"]}
        return {"summary": progress_text(stats, s.metrics["phases"]["download"]["dur_s"])}


def _decode(s, inp):
    s.decode(inp)


def _decrypt(s, inp):
    s.decrypt(inp)


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
        self.session = None  # The worker thread sets it. The Tk thread reads it only when no job runs.
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
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        tabs = ttk.Notebook(frame)
        tabs.grid(row=0, column=0, sticky="ew")
        dl, adv = ttk.Frame(tabs, padding=8), ttk.Frame(tabs, padding=8)
        tabs.add(dl, text="Download")
        tabs.add(adv, text="Advanced")
        self.tabs = tabs
        self.buttons = []  # Busy gating disables every button here.

        def button(parent, text, cmd, **grid):
            self.buttons.append(ttk.Button(parent, text=text, command=cmd))
            self.buttons[-1].grid(**grid)

        for tab in (dl, adv):  # Both Output boxes share out_var, so they stay equal.
            tab.columnconfigure(1, weight=1)
            ttk.Label(tab, text="Output folder").grid(row=0, column=0, sticky="w")
            ttk.Entry(tab, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=6, pady=2)
            ttk.Button(tab, text="Browse", command=self.browse_output).grid(row=0, column=2, padx=2)
            button(tab, "Open results folder", self.on_open, row=0, column=3, padx=2)

        ttk.Label(dl, text="ECU IP").grid(row=1, column=0, sticky="w")
        ttk.Entry(dl, textvariable=self.ip_var).grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        button(dl, "Test connection", self.on_test, row=1, column=2, columnspan=2, sticky="ew", padx=2)
        ttk.Label(adv, text="Input").grid(row=1, column=0, sticky="w")
        ttk.Entry(adv, textvariable=self.in_var).grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(adv, text="Browse file", command=self.browse_file).grid(row=1, column=2, padx=2)
        ttk.Button(adv, text="Browse folder", command=self.browse_folder).grid(row=1, column=3, padx=2)
        for tab, actions in ((dl, (("Download", self.on_download), ("Package to send", self.on_package))),
                             (adv, (("Decode", self.on_decode), ("Decrypt", self.on_decrypt)))):
            bar = ttk.Frame(tab)
            bar.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
            for text, cmd in actions:
                button(bar, text, cmd, row=0, column=len(bar.winfo_children()), padx=(0, 6))
        ttk.Label(frame, textvariable=self.status, wraplength=WRAP).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.bar = ttk.Progressbar(frame, mode="indeterminate")  # ponytail: the download size is unknown, so no percent.
        self.bar.grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Label(frame, textvariable=self.metrics).grid(row=3, column=0, sticky="w")
        self.log = scrolledtext.ScrolledText(frame, height=14, wrap="word", state="disabled")
        self.log.grid(row=4, column=0, sticky="nsew")
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
        found = self._inputs(need_ip=True)
        if found:
            self._start("The ECU search starts. Wait for the result.", found, _test)

    def on_download(self):
        found = self._inputs(need_ip=True)
        if found:
            run_if_confirmed(self._confirm_arm, lambda: self._start(
                "The download starts. The ECU logger stops while the download runs.", found, _download))

    def on_decode(self):
        found = self._inputs(need_ip=False, decode=True)
        if found:
            self._start("The decode starts. Wait for the result.", found, _decode)

    def on_decrypt(self):
        found = self._inputs(need_ip=False, decode=True, decrypt=True)
        if found:
            self._start("The decrypt starts. Wait for the result.", found, _decrypt)

    def on_package(self):
        found = self._inputs(need_ip=False)
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
        if not self.busy:  # A running job still uses the session. The end of the process stops it.
            self._close()
        self.root.destroy()

    def _confirm_arm(self):
        return messagebox.askyesno(TITLE, "This arms the ECU and stops its logger while the download runs. Continue?",
                                   default=messagebox.NO)

    def _inputs(self, need_ip, decode=False, decrypt=False):
        """Return (ip, output, input) when the fields pass. Otherwise show the reason and return None.

        Test connection and Download need a valid ECU IP.
        Decode, Decrypt and Package run offline. A wrong ECU IP must not stop them, so they get None for the IP.
        Only Decode and Decrypt read the Input box. The other buttons get an empty input, so an old Input cannot block them.
        Decrypt (decrypt=True) also refuses an output folder inside the folder of the input file.
        """
        inp = self.in_var.get().strip() if decode else ""
        try:
            ip = check_ip(self.ip_var.get()) if need_ip else None
            out = check_output(self.out_var.get(), inp, decrypt)
        except ValueError as e:
            messagebox.showerror(TITLE, str(e))
            return None
        return ip, str(out), inp

    def _start(self, label, found, job):
        """Run one job on a worker thread. The window changes only on the Tk thread."""
        self.status.set(label)
        self.metrics.set("")
        self.t0 = time.monotonic()
        self._busy(True)  # ponytail: no job queue. The buttons stay disabled while one job runs.
        threading.Thread(target=self._worker, args=(*found, job), daemon=True).start()

    def _worker(self, ip, out, inp, job):
        end, s = {}, None
        try:
            s = self._session_for(out, ip)
            end = job(s, inp) or {}
        except Exception as e:
            if s is None:  # The session could not start, so no log exists. Only the window can show the failure.
                self.events.put({"type": "error", "msg": "The run stopped.", "error": f"{type(e).__name__}: {e}"})
            else:
                with suppress(OSError):  # The disk can be the cause. The error event goes out before the file write.
                    s._error("The run stopped.", e)  # The log, run.json and an error event
        finally:  # The end event must always follow, or the buttons stay disabled.
            self.events.put({"type": "end", **end})

    def _session_for(self, out, ip):
        """Return the session of the output folder. Run this on the worker thread.

        A new output folder closes the old session and starts a new one. The same folder keeps its session.
        The ECU identity that Test connection reads then stays in the metrics. The download record and the zip name use it.
        A valid ECU IP (ip is not None) goes to the session before the job runs.
        """
        # ponytail: the identity stays until the next Test connection finds an ECU. Download does not read the beacon.
        # Add that read to Download when users swap cars without a Test.
        s = self.session
        if s is None or s.dir != Path(out):
            self._close()
            s = self.session = RunSession(out, ecu_ip=ip or wtx_client.ECU_IP_DEFAULT, on_event=self.events.put)
        else:
            if ip:
                s.ecu_ip = s.metrics["ecu_ip"] = ip
            for name in ("logs", "download"):  # A user can delete these folders between two jobs. The download needs them.
                (s.dir / name).mkdir(parents=True, exist_ok=True)
        return s

    def _close(self):
        """Close the session and forget it. A disk error here must not stop the app."""
        s, self.session = self.session, None
        if s is not None:
            with suppress(OSError):
                s.close()

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
            self.status.set(error_text(ev))
        elif kind == "end":
            self._busy(False)
            if ev.get("msg"):
                self.status.set(ev["msg"])
            if ev.get("summary"):
                self.metrics.set(ev["summary"])
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
