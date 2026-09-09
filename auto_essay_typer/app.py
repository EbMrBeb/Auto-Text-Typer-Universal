"""Tkinter front end with global hotkeys, a live preview, and a full settings editor.

Threading: Tk owns the main thread; typing runs on a worker thread (a run
may take minutes) and the pynput hotkey listener runs on a third. Any
widget/StringVar mutation from a background thread MUST go through
``root.after(...)`` -- see ``_set_status`` and ``start()``'s worker. Dry Run
stays synchronous on the main thread: ``planner.plan`` never sleeps, so even
a long essay plans in well under a second -- only the real, sleep-driven run
in ``start()`` needs a worker thread.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from auto_essay_typer import config
from auto_essay_typer.actions import Action, KeyPress, Pause
from auto_essay_typer.driver import Driver, RunResult
from auto_essay_typer.planner import plan
from auto_essay_typer.preview import PreviewEvent, PreviewSummary, describe
from auto_essay_typer.profiles import PRESETS, Profile, preset
from auto_essay_typer.settings_panel import SettingsPanel
from auto_essay_typer.simulator import total_duration_ms

# Hotkey -> TyperApp method name.
HOTKEYS = {"<f9>": "start", "<f10>": "toggle_pause", "<esc>": "abort"}

_EVENT_TAGS = {"typo": "#b3261e", "revision": "#1a56db", "break": "#7c3aed", "pause": "#5f6368"}


def accessibility_hint() -> str:
    return (
        "Keystrokes are being blocked. Grant Accessibility permission to the app "
        "that launched this (Terminal, iTerm, or your IDE) in:\n\n"
        "System Settings > Privacy & Security > Accessibility\n\n"
        "Then quit and relaunch that app -- macOS does not apply the change to "
        "already-running processes."
    )


def profile_from_settings(preset_name: str, wpm: float, typo_rate: float) -> Profile:
    """Build a Profile from the three values the original GUI exposed (kept for tests)."""
    return preset(preset_name).with_overrides(wpm=wpm, typo_rate_per_100=typo_rate)


def _format_mmss(ms: float) -> str:
    minutes, seconds = divmod(max(0, int(ms / 1000.0)), 60)
    return f"{minutes}:{seconds:02d}"


def _cumulative_pause_ms_by_keypress(actions: list[Action]) -> list[float]:
    """result[i] = planned Pause ms up to and including the i-th KeyPress.

    Turns the driver's (done, total) progress callback -- a keypress count,
    no time position -- into a live "remaining planned time" ETA.
    """
    cumulative = [0.0]
    running = 0.0
    for action in actions:
        if isinstance(action, Pause):
            running += action.ms
        elif isinstance(action, KeyPress):
            cumulative.append(running)
    return cumulative


class TyperApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.driver: Driver | None = None
        self.worker: threading.Thread | None = None
        self._run_start_time: float | None = None
        self._profile_display_to_name: dict[str, str] = {}
        self.advanced_visible = False

        root.title("Auto Essay Typer")
        root.geometry("820x760")
        root.minsize(640, 560)

        self.profile_display_var = tk.StringVar(value="")
        self.wpm_var = tk.DoubleVar(value=65.0)
        self.typo_var = tk.DoubleVar(value=1.2)
        self.seed_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Paste text, then press F9.")
        self.counter_var = tk.StringVar(value="0 chars, 0 words")
        self.wpm_display_var = tk.StringVar(value="65 wpm")
        self.typo_display_var = tk.StringVar(value="1.2 /100")
        self.progress_pct_var = tk.StringVar(value="0%")
        self.eta_var = tk.StringVar(value="ETA --:--")
        self.elapsed_var = tk.StringVar(value="Elapsed 0:00")
        self.advanced_toggle_var = tk.StringVar(value="▸ Advanced settings")

        self._build_widgets()
        self._refresh_profile_list(select="natural")
        self._start_hotkey_listener()

    # -- layout ---------------------------------------------------------

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(frame, wrap="word", height=12, undo=True)
        self.text.pack(fill="both", expand=True, pady=(0, 2))
        self.text.bind("<<Modified>>", self._on_text_modified)
        ttk.Label(frame, textvariable=self.counter_var, foreground="#666").pack(anchor="e", pady=(0, 8))

        self._build_profile_row(frame)
        self._build_quick_controls(frame)

        toggle = ttk.Button(frame, textvariable=self.advanced_toggle_var, command=self._toggle_advanced)
        toggle.pack(anchor="w", pady=(2, 2))
        self.advanced_container = ttk.Frame(frame, height=230)
        self.advanced_container.pack_propagate(False)
        self.settings_panel = SettingsPanel(self.advanced_container)
        self.settings_panel.pack(fill="both", expand=True)

        self.buttons_frame = ttk.Frame(frame)
        self.buttons_frame.pack(fill="x", pady=(6, 8))
        ttk.Button(self.buttons_frame, text="Dry Run", command=self.dry_run).pack(side="left")
        self.start_button = ttk.Button(self.buttons_frame, text="Start (F9)", command=self.start)
        self.start_button.pack(side="left", padx=6)
        self.pause_button = ttk.Button(self.buttons_frame, text="Pause (F10)", command=self.toggle_pause)
        self.pause_button.pack(side="left")
        ttk.Button(self.buttons_frame, text="Abort (Esc)", command=self.abort).pack(side="left", padx=6)

        preview_frame = ttk.Frame(frame)
        preview_frame.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Label(preview_frame, text="Preview").pack(anchor="w")
        self.preview = tk.Text(preview_frame, wrap="word", height=10, state="disabled")
        self.preview.pack(fill="both", expand=True)
        for kind, color in _EVENT_TAGS.items():
            self.preview.tag_configure(kind, foreground=color)

        self._build_progress_row(frame)
        ttk.Label(frame, textvariable=self.status_var, foreground="#555").pack(anchor="w", pady=(6, 0))

    def _build_profile_row(self, frame: ttk.Frame) -> None:
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Profile").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile_display_var, state="readonly", width=22)
        self.profile_combo.grid(row=0, column=1, padx=(4, 12))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_profile_selected())
        ttk.Button(row, text="Save As...", command=self._save_as).grid(row=0, column=2, padx=2)
        ttk.Button(row, text="Delete", command=self._delete_profile).grid(row=0, column=3, padx=2)
        ttk.Button(row, text="Reload", command=self._reload_profiles).grid(row=0, column=4, padx=2)

    def _build_quick_controls(self, frame: ttk.Frame) -> None:
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="WPM").grid(row=0, column=0, sticky="w")
        wpm_scale = ttk.Scale(row, from_=20, to=140, variable=self.wpm_var, length=150, command=self._on_wpm_change)
        wpm_scale.grid(row=0, column=1, padx=4)
        ttk.Label(row, textvariable=self.wpm_display_var, width=8).grid(row=0, column=2, padx=(0, 16))
        ttk.Label(row, text="Typos/100").grid(row=0, column=3, sticky="w")
        typo_scale = ttk.Scale(row, from_=0, to=6, variable=self.typo_var, length=110, command=self._on_typo_change)
        typo_scale.grid(row=0, column=4, padx=4)
        ttk.Label(row, textvariable=self.typo_display_var, width=8).grid(row=0, column=5, padx=(0, 16))
        ttk.Label(row, text="Seed").grid(row=0, column=6, sticky="w")
        ttk.Entry(row, textvariable=self.seed_var, width=8).grid(row=0, column=7, padx=(4, 0))

    def _build_progress_row(self, frame: ttk.Frame) -> None:
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 4))
        self.progress = ttk.Progressbar(row, mode="determinate", length=260)
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=self.progress_pct_var, width=5).pack(side="left", padx=(6, 12))
        ttk.Label(row, textvariable=self.eta_var, width=12).pack(side="left", padx=(0, 12))
        ttk.Label(row, textvariable=self.elapsed_var, width=14).pack(side="left")

    def _on_text_modified(self, _event: object = None) -> None:
        self.text.edit_modified(False)
        content = self.text.get("1.0", "end-1c")
        self.counter_var.set(f"{len(content)} chars, {len(content.split())} words")

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_container.pack(fill="x", pady=(2, 6), before=self.buttons_frame)
            self.advanced_toggle_var.set("▾ Advanced settings")
        else:
            self.advanced_container.pack_forget()
            self.advanced_toggle_var.set("▸ Advanced settings")

    def _on_wpm_change(self, value_str: str) -> None:
        value = float(value_str)
        self.wpm_display_var.set(f"{value:.0f} wpm")
        self.settings_panel.set_value("wpm", round(value, 2))

    def _on_typo_change(self, value_str: str) -> None:
        value = float(value_str)
        self.typo_display_var.set(f"{value:.1f} /100")
        self.settings_panel.set_value("typo_rate_per_100", round(value, 2))

    # -- profile management -------------------------------------------------

    def _selected_profile_name(self) -> str:
        return self._profile_display_to_name.get(self.profile_display_var.get(), "natural")

    def _refresh_profile_list(self, select: str | None = None) -> None:
        target = select if select is not None else self._selected_profile_name()
        profiles = config.builtin_and_user_profiles()
        user_names = set(config.load_profiles())

        self._profile_display_to_name = {}
        labels = []
        for name in sorted(profiles):
            is_pure_builtin = name in PRESETS and name not in user_names
            label = f"{name}  [builtin]" if is_pure_builtin else name
            self._profile_display_to_name[label] = name
            labels.append(label)
        self.profile_combo["values"] = labels

        if target not in profiles and profiles:
            target = "natural" if "natural" in profiles else sorted(profiles)[0]
        for label, name in self._profile_display_to_name.items():
            if name == target:
                self.profile_display_var.set(label)
                break
        self._on_profile_selected()

    def _on_profile_selected(self) -> None:
        try:
            profile = config.resolve_profile(self._selected_profile_name())
        except KeyError:
            return
        self.wpm_var.set(profile.wpm)
        self.typo_var.set(profile.typo_rate_per_100)
        self.wpm_display_var.set(f"{profile.wpm:.0f} wpm")
        self.typo_display_var.set(f"{profile.typo_rate_per_100:.1f} /100")
        self.settings_panel.load(profile)

    def _save_as(self) -> None:
        name = simpledialog.askstring("Save profile", "Profile name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            profile = self._effective_profile()
        except ValueError as error:
            self._set_status(f"Cannot save: {error}")
            return
        config.save_profile(name, profile)
        self._refresh_profile_list(select=name)
        self._set_status(f"Saved profile {name!r} to {config.config_path()}.")

    def _delete_profile(self) -> None:
        name = self._selected_profile_name()
        if name not in config.load_profiles():
            self._set_status(f"{name!r} is a builtin profile and cannot be deleted.")
            return
        if not messagebox.askyesno("Delete profile", f"Delete profile {name!r}?", parent=self.root):
            return
        config.delete_profile(name)
        self._refresh_profile_list(select="natural")
        self._set_status(f"Deleted profile {name!r}.")

    def _reload_profiles(self) -> None:
        self._refresh_profile_list()
        self._set_status("Profile list reloaded.")

    # -- helpers --------------------------------------------------------------

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _effective_profile(self) -> Profile:
        """Profile implied by dropdown + quick controls + Advanced panel; may raise ValueError."""
        base = config.resolve_profile(self._selected_profile_name())
        base = base.with_overrides(wpm=round(self.wpm_var.get(), 2), typo_rate_per_100=round(self.typo_var.get(), 2))
        return self.settings_panel.to_profile(base)

    def _capture_settings(self) -> tuple[str, Profile, int | None] | tuple[None, None, None]:
        """Read every widget needed to plan. Main thread only; may raise ValueError."""
        text = self.text.get("1.0", "end-1c")
        if not text.strip():
            return None, None, None
        profile = self._effective_profile()
        seed_text = self.seed_var.get().strip()
        seed = int(seed_text) if seed_text.lstrip("-").isdigit() else None
        return text, profile, seed

    # -- dry run ----------------------------------------------------------------

    def dry_run(self) -> None:
        """Plan the current text and render the trace, synchronously (see module docstring)."""
        try:
            text, prof, seed = self._capture_settings()
        except ValueError as error:
            self._set_status(f"Invalid settings: {error}")
            return
        if text is None:
            self._set_status("Nothing to type.")
            return
        actions = plan(text, prof, seed)
        events, summary = describe(actions, text)
        self._render_preview(events, summary)
        minutes, seconds = divmod(int(summary.duration_ms / 1000), 60)
        self._set_status(f"Dry run: {summary.characters} chars, ~{minutes}m {seconds}s.")

    def _render_preview(self, events: list[PreviewEvent], summary: PreviewSummary) -> None:
        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        for event in events:
            line_start = self.preview.index("end-1c")
            self.preview.insert("end", f"[{event.at_char:>6}] {event.kind:<8} {event.detail}\n")
            self.preview.tag_add(event.kind, line_start, self.preview.index("end-1c"))
        if summary.typo_count + summary.revision_count + summary.break_count > len(events):
            self.preview.insert("end", "... (event list truncated; counts below are exact)\n")

        minutes, seconds = divmod(int(summary.duration_ms / 1000), 60)
        match_line = (
            "output matches input exactly"
            if summary.matches_input
            else "MISMATCH -- output does not match input, do not use"
        )
        self.preview.insert(
            "end",
            "\n--- summary ---\n"
            f"characters: {summary.characters}\n"
            f"estimated duration: {minutes}m {seconds}s\n"
            f"effective wpm: {summary.effective_wpm:.1f}\n"
            f"typos: {summary.typo_count}   revisions: {summary.revision_count}   "
            f"breaks: {summary.break_count}\n"
            f"longest pause: {summary.longest_pause_ms:.0f}ms\n"
            f"{match_line}\n",
        )
        self.preview.config(state="disabled")

    # -- run --------------------------------------------------------------------

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        try:
            text, prof, seed = self._capture_settings()
        except ValueError as error:
            self._set_status(f"Invalid settings: {error}")
            return
        if text is None:
            self._set_status("Nothing to type.")
            return

        self.start_button.config(state="disabled")
        self.pause_button.config(text="Pause (F10)")
        self.progress["value"] = 0
        self.progress_pct_var.set("0%")
        self.eta_var.set("ETA --:--")
        self.elapsed_var.set("Elapsed 0:00")
        self._run_start_time = None
        self.driver = Driver()

        def work() -> None:
            try:
                actions = plan(text, prof, seed)
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                self.root.after(0, self._on_run_finished, f"Planning failed: {error}")
                return

            total_ms = total_duration_ms(actions)
            cumulative = _cumulative_pause_ms_by_keypress(actions)

            def on_progress(done: int, total: int) -> None:
                self.root.after(0, self._update_progress, done, total, total_ms, cumulative)

            def on_countdown(n: int) -> None:
                self._set_status(f"Click into your document -- starting in {n}...")

            try:
                result = self.driver.run(actions, on_progress=on_progress, on_countdown=on_countdown)
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                self.root.after(0, self._on_run_finished, f"Error: {error}\n\n{accessibility_hint()}")
                return
            done_msg = "Done." if result is RunResult.COMPLETED else "Aborted."
            self.root.after(0, self._on_run_finished, done_msg)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _update_progress(self, done: int, total: int, total_ms: float, cumulative: list[float]) -> None:
        if self._run_start_time is None:
            self._run_start_time = time.monotonic()
        self.progress["maximum"] = max(total, 1)
        self.progress["value"] = done
        self.progress_pct_var.set(f"{(done / total * 100.0) if total else 100.0:.0f}%")
        elapsed_planned = cumulative[done] if done < len(cumulative) else total_ms
        self.eta_var.set(f"ETA {_format_mmss(max(0.0, total_ms - elapsed_planned))}")
        elapsed_real_ms = (time.monotonic() - self._run_start_time) * 1000
        self.elapsed_var.set(f"Elapsed {_format_mmss(elapsed_real_ms)}")
        self.status_var.set(f"Typing {done}/{total}...  F10 pause, Esc abort")

    def _on_run_finished(self, message: str) -> None:
        self.start_button.config(state="normal")
        self._run_start_time = None
        self.status_var.set(message)

    def toggle_pause(self) -> None:
        if self.driver is None:
            return
        if self.driver.is_paused:
            self.driver.resume()
            self.pause_button.config(text="Pause (F10)")
            self._set_status("Resumed.")
        else:
            self.driver.pause()
            self.pause_button.config(text="Resume (F10)")
            self._set_status("Paused. F10 to resume.")

    def abort(self) -> None:
        if self.driver is not None:
            self.driver.abort()
        self._set_status("Aborted.")

    # -- hotkeys ------------------------------------------------------------

    def _start_hotkey_listener(self) -> None:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 - GUI still usable via buttons
            self._set_status("Global hotkeys unavailable; use the buttons.")
            return
        listener = keyboard.GlobalHotKeys(
            {
                key: (lambda method=method_name: self.root.after(0, getattr(self, method)))
                for key, method_name in HOTKEYS.items()
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
