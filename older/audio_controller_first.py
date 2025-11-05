# -------------------------------------------------
#  audio_controller.py  (final version with UI upgrades)
# -------------------------------------------------
import os
import json
import time
import threading
import tkinter as tk
import customtkinter as ctk
import sounddevice as sd
from tkinter import filedialog, messagebox
from pydub import AudioSegment
from pydub.effects import low_pass_filter, high_pass_filter
import numpy as np

# -------------------------------------------------
# Helper to apply frequency filters based on slider values
# -------------------------------------------------
def apply_slider_filters(segment: AudioSegment, sliders):
    centres = [100 * (10 ** (i / 9)) for i in range(10)]
    filtered = segment
    for val, fc in zip(sliders, centres):
        bw = fc * (0.10 + val / 1000)
        low = max(fc - bw, 20)
        high = fc + bw
        filtered = high_pass_filter(filtered, low)
        filtered = low_pass_filter(filtered, high)
    return filtered


# -------------------------------------------------
# Audio thread for playback
# -------------------------------------------------
class AudioPlayer(threading.Thread):
    def __init__(self, file_path, device, stop_event, sliders):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.device = device
        self.stop_event = stop_event
        self.sliders = sliders

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            seg = apply_slider_filters(seg, self.sliders)
            raw = seg.raw_data
            samplerate = seg.frame_rate
            channels = seg.channels

            audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                audio_np = audio_np.reshape((-1, channels))

            sd.play(audio_np, samplerate, device=self.device, blocking=False)

            while sd.get_stream().active:
                if self.stop_event.is_set():
                    sd.stop()
                    break
                sd.sleep(100)
        except Exception as e:
            messagebox.showerror("Playback error", str(e))


# -------------------------------------------------
# Main Application
# -------------------------------------------------
class AudioControllerApp(ctk.CTk):
    SUPPORTED_EXT = (".wav", ".aiff", ".bwf", ".flac", ".alac",
                     ".mp3", ".aac", ".ogg")

    def __init__(self):
        super().__init__()
        self.title("Audio Controller – Vertical Sliders")
        self.geometry("1200x720")
        self.resizable(False, False)  # 🟢 Unresizable window
        self.configure(fg_color="#2b2b2b")

        self.file_entries = []
        self.play_thread = None
        self.stop_event = threading.Event()
        self.selected_device = None
        self.current_track = None
        self.timer_running = False

        # Create all UI sections
        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        self._create_center_sliders()
        self._create_bottom_bar()
        self._populate_devices()
        self._enable_drag_drop()

    # -------------------------------------------------
    # Menu bar (added "Add audio...")
    # -------------------------------------------------
    def _create_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add audio…", command=self._browse_file)  # 🟢 NEW
        file_menu.add_separator()
        file_menu.add_command(label="Save profile…", command=self._save_profile)
        menubar.add_cascade(label="File", menu=file_menu)

        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Keybinds…", command=self._open_keybind_dialog)
        menubar.add_cascade(label="Settings", menu=settings)

        self.config(menu=menubar)

    # -------------------------------------------------
    # Top bar (device selector)
    # -------------------------------------------------
    def _create_top_bar(self):
        top = ctk.CTkFrame(self, height=45, fg_color="#1e1e1e")
        top.pack(fill="x", side="top")

        self.device_var = tk.StringVar(value="Select device")
        self.device_menu = ctk.CTkOptionMenu(
            top,
            variable=self.device_var,
            values=[],
            command=self._on_device_selected,
            fg_color="#088888",
            button_color="#088888",
            dropdown_hover_color="#0aa3a3",
        )
        self.device_menu.pack(padx=10, pady=5, side="right")

    # -------------------------------------------------
    # Left panel (audio file list)
    # -------------------------------------------------
    def _create_left_panel(self):
        left = ctk.CTkFrame(self, width=340, fg_color="#1e1e1e")
        left.pack(fill="y", side="left", anchor="nw")
        left.pack_propagate(False)

        ctk.CTkLabel(left, text="Audio Files", font=("Helvetica", 14, "bold")).pack(pady=10)

        self.file_canvas = tk.Canvas(left, bg="#1e1e1e", highlightthickness=0)
        self.file_canvas.pack(side="left", fill="both", expand=True, padx=5)

        self.file_scroll = ctk.CTkScrollbar(left, orientation="vertical",
                                            command=self.file_canvas.yview)
        self.file_scroll.pack(side="right", fill="y")
        self.file_canvas.configure(yscrollcommand=self.file_scroll.set)

        self.file_frame = ctk.CTkFrame(self.file_canvas, fg_color="#1e1e1e")
        self.file_canvas.create_window((0, 0), window=self.file_frame, anchor="nw")
        self.file_frame.bind("<Configure>", lambda e: self.file_canvas.configure(
            scrollregion=self.file_canvas.bbox("all")))

        add_btn = ctk.CTkButton(
            left,
            text="Browse files…",
            command=self._browse_file,
            fg_color="#088888",
            hover_color="#0aa3a3",
            width=260,
        )
        add_btn.pack(side="bottom", pady=10)

    # -------------------------------------------------
    # Center section (sliders)
    # -------------------------------------------------
    def _create_center_sliders(self):
        center = ctk.CTkFrame(self, fg_color="#2b2b2b")
        center.pack(expand=True, fill="both", padx=15, pady=15)

        self.sliders = []
        hz_labels = [100, 200, 400, 800, 1600, 3200, 5000, 6400, 8000, 10000]
        tick_values = [0, 25, 50, 75, 100]

        for i, hz in enumerate(hz_labels):
            frame = ctk.CTkFrame(center, fg_color="#2b2b2b")
            frame.grid(row=0, column=i, padx=20, pady=5)

            # Percentage label above slider
            val_label = ctk.CTkLabel(frame, text="50%", font=("Helvetica", 12, "bold"))
            val_label.pack(pady=(0, 3))

            # Slider (white track, darker cyan handle)
            s = ctk.CTkSlider(
                frame,
                from_=0,
                to=100,
                number_of_steps=4,  # snap to 0,25,50,75,100
                width=20,
                height=250,
                orientation="vertical",
                fg_color="#ffffff",
                button_color="#088888",
                button_hover_color="#0aa3a3",
            )
            s.set(50)
            s.pack()

            # Tick marks beside slider
            tick_frame = ctk.CTkFrame(frame, fg_color="#2b2b2b")
            tick_frame.pack(pady=3)
            for v in tick_values:
                ctk.CTkLabel(tick_frame, text=str(v), font=("Helvetica", 10)).pack()

            # Frequency label under slider
            ctk.CTkLabel(frame, text=f"{hz} Hz").pack(pady=(5, 0))

            def update_label(event, slider=s, label=val_label):
                val = int(slider.get())
                label.configure(text=f"{val}%")

            s.bind("<ButtonRelease-1>", update_label)
            s.bind("<B1-Motion>", update_label)

            self.sliders.append(s)

    # -------------------------------------------------
    # Bottom bar (playback controls)
    # -------------------------------------------------
    def _create_bottom_bar(self):
        bottom = ctk.CTkFrame(self, height=50, fg_color="#1e1e1e")
        bottom.pack(fill="x", side="bottom")

        play_btn = ctk.CTkButton(bottom, text="▶ Play", fg_color="#088888",
                                 hover_color="#0aa3a3", command=self._play_audio)
        stop_btn = ctk.CTkButton(bottom, text="■ Stop", fg_color="#088888",
                                 hover_color="#0aa3a3", command=self._stop_audio)
        play_btn.pack(side="left", padx=10, pady=10)
        stop_btn.pack(side="left", padx=10, pady=10)

    # -------------------------------------------------
    # Device population
    # -------------------------------------------------
    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            names = [d["name"] for d in devices if d["max_output_channels"] > 0]
            self.device_menu.configure(values=names)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # -------------------------------------------------
    # Browse file (Add audio)
    # -------------------------------------------------
    def _browse_file(self):
        file = filedialog.askopenfilename(
            title="Select Audio File",
            filetypes=[("Audio files", "*.wav *.mp3 *.flac *.ogg *.aac *.aiff *.alac *.bwf")],
        )
        if file:
            self._add_audio_file(file)

    def _add_audio_file(self, path):
        if not path.lower().endswith(self.SUPPORTED_EXT):
            messagebox.showerror("Unsupported file", "This audio format is not supported.")
            return
        label = ctk.CTkLabel(self.file_frame, text=os.path.basename(path), fg_color="#2b2b2b")
        label.pack(anchor="w", padx=10, pady=5)
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))
        self.file_entries.append((label, path))

    def _select_audio(self, path):
        self.current_track = path
        for lbl, _ in self.file_entries:
            lbl.configure(fg_color="#2b2b2b")
        for lbl, p in self.file_entries:
            if p == path:
                lbl.configure(fg_color="#088888")

    # -------------------------------------------------
    # Playback logic
    # -------------------------------------------------
    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select an audio file first.")
            return
        if not self.selected_device:
            messagebox.showwarning("No device", "Please select an output device.")
            return

        if self.play_thread and self.play_thread.is_alive():
            self._stop_audio()

        sliders_values = [s.get() for s in self.sliders]
        self.stop_event.clear()
        self.play_thread = AudioPlayer(self.current_track, self.selected_device,
                                       self.stop_event, sliders_values)
        self.play_thread.start()

    def _stop_audio(self):
        self.stop_event.set()
        sd.stop()

    # -------------------------------------------------
    # Miscellaneous
    # -------------------------------------------------
    def _on_device_selected(self, value):
        self.selected_device = value

    def _save_profile(self):
        try:
            sliders = [s.get() for s in self.sliders]
            data = {"sliders": sliders}
            file = filedialog.asksaveasfilename(defaultextension=".json",
                                                filetypes=[("JSON", "*.json")])
            if file:
                with open(file, "w") as f:
                    json.dump(data, f, indent=4)
                messagebox.showinfo("Saved", "Profile saved successfully!")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _open_keybind_dialog(self):
        messagebox.showinfo("Keybinds", "Feature not implemented yet.")

    def _enable_drag_drop(self):
        # Placeholder for drag/drop integration
        pass


# -------------------------------------------------
# Run app
# -------------------------------------------------
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp()
    app.mainloop()
