"""Tkinter front end with global hotkeys.

Threading model: Tk owns the main thread. Typing runs on a worker thread so the
window stays responsive, and the pynput hotkey listener runs on a third. Widget
updates from either background thread are marshalled back through
``root.after``, because Tk is not thread-safe.
"""

import threading
import tkinter as tk
from tkinter import ttk

from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.profiles import PRESETS, Profile, preset
from auto_essay_typer.simulator import simulate, total_duration_ms

HOTKEYS = {"<f9>": "start", "<f10>": "pause", "<esc>": "abort"}


def accessibility_hint() -> str:
    return (
        "Keystrokes are being blocked. Grant Accessibility permission to the app "
        "that launched this (Terminal, iTerm, or your IDE) in:\n\n"
        "System Settings > Privacy & Security > Accessibility\n\n"
        "Then quit and relaunch that app -- macOS does not apply the change to "
        "already-running processes."
    )


def profile_from_settings(preset_name: str, wpm: float, typo_rate: float) -> Profile:
    """Build a Profile from the three values the GUI exposes."""
    return preset(preset_name).with_overrides(wpm=wpm, typo_rate_per_100=typo_rate)


class TyperApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.driver: Driver | None = None
        self.worker: threading.Thread | None = None
        root.title("Auto Essay Typer")
        root.geometry("720x560")

        self.preset_var = tk.StringVar(value="natural")
        self.wpm_var = tk.DoubleVar(value=65.0)
        self.typo_var = tk.DoubleVar(value=1.2)
        self.seed_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Paste text, then press F9.")

        self._build_widgets()
        self._start_hotkey_listener()

    # -- layout -----------------------------------------------------------

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(frame, wrap="word", height=18, undo=True)
        self.text.pack(fill="both", expand=True, pady=(0, 10))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 8))

        ttk.Label(controls, text="Preset").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.preset_var,
            values=sorted(PRESETS),
            state="readonly",
            width=10,
        ).grid(row=0, column=1, padx=(4, 16))

        ttk.Label(controls, text="WPM").grid(row=0, column=2, sticky="w")
        ttk.Scale(controls, from_=20, to=140, variable=self.wpm_var, length=140).grid(
            row=0, column=3, padx=(4, 4)
        )
        ttk.Label(controls, textvariable=self.wpm_var, width=5).grid(row=0, column=4, padx=(0, 16))

        ttk.Label(controls, text="Typos/100").grid(row=0, column=5, sticky="w")
        ttk.Scale(controls, from_=0, to=6, variable=self.typo_var, length=110).grid(
            row=0, column=6, padx=(4, 16)
        )

        ttk.Label(controls, text="Seed").grid(row=0, column=7, sticky="w")
        ttk.Entry(controls, textvariable=self.seed_var, width=8).grid(row=0, column=8, padx=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Dry Run", command=self.dry_run).pack(side="left")
        ttk.Button(buttons, text="Start (F9)", command=self.start).pack(side="left", padx=6)
        ttk.Button(buttons, text="Abort (Esc)", command=self.abort).pack(side="left")

        ttk.Label(frame, textvariable=self.status_var, foreground="#555").pack(
            anchor="w", pady=(10, 0)
        )

    # -- helpers ----------------------------------------------------------

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _current_plan(self):
        text = self.text.get("1.0", "end-1c")
        if not text.strip():
            return None, None
        profile = profile_from_settings(
            self.preset_var.get(), round(self.wpm_var.get()), self.typo_var.get()
        )
        seed = int(self.seed_var.get()) if self.seed_var.get().strip().isdigit() else None
        return text, plan(text, profile, seed)

    # -- actions ----------------------------------------------------------

    def dry_run(self) -> None:
        text, actions = self._current_plan()
        if actions is None:
            self._set_status("Nothing to type.")
            return
        seconds = int(total_duration_ms(actions) / 1000)
        exact = simulate(actions) == text
        self._set_status(
            f"Dry run: {len(text)} chars, ~{seconds // 60}m {seconds % 60}s, "
            f"output {'matches input exactly' if exact else 'MISMATCH -- do not use'}."
        )

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        text, actions = self._current_plan()
        if actions is None:
            self._set_status("Nothing to type.")
            return

        self.driver = Driver()

        def work() -> None:
            try:
                result = self.driver.run(
                    actions,
                    on_progress=lambda done, total: self._set_status(
                        f"Typing {done}/{total}...  F10 pause, Esc abort"
                    ),
                    on_countdown=lambda n: self._set_status(
                        f"Click into your document -- starting in {n}..."
                    ),
                )
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                self._set_status(f"Error: {error}\n\n{accessibility_hint()}")
                return
            self._set_status("Done." if result is RunResult.COMPLETED else "Aborted.")

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def toggle_pause(self) -> None:
        if self.driver is None:
            return
        if self.driver.is_paused:
            self.driver.resume()
            self._set_status("Resumed.")
        else:
            self.driver.pause()
            self._set_status("Paused. F10 to resume.")

    def abort(self) -> None:
        if self.driver is not None:
            self.driver.abort()
            self._set_status("Aborted.")

    # -- hotkeys ----------------------------------------------------------

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 - GUI still usable via buttons
            self._set_status("Global hotkeys unavailable; use the buttons.")
            return
        listener = keyboard.GlobalHotKeys(
            {
                "<f9>": lambda: self.root.after(0, self.start),
                "<f10>": lambda: self.root.after(0, self.toggle_pause),
                "<esc>": lambda: self.root.after(0, self.abort),
            }
        )
        listener.daemon = True
        listener.start()


def main() -> int:
    root = tk.Tk()
    TyperApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
