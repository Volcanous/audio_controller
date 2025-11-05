# -------------------------------------------------
#  audio_controller.py (Added keybinds + fixed device detection)
# -------------------------------------------------
import os
import json
import threading
import tkinter as tk
import customtkinter as ctk
import sounddevice as sd
from tkinter import filedialog, messagebox
from pydub import AudioSegment
import numpy as np

# -------------------------------------------------
# Audio Player Thread
# -------------------------------------------------
class AudioPlayer(threading.Thread):
    def __init__(self, file_path, device_id, stop_event, sliders):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.device_id = device_id
        self.stop_event = stop_event
        self.sliders = sliders

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            # lightweight normalization
            seg = seg.apply_gain(-seg.max_dBFS)
            raw = seg.raw_data
            audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            channels = seg.channels
            samplerate = seg.frame_rate
            if channels > 1:
                audio_np = audio_np.reshape((-1, channels))
            sd.play(audio_np, samplerate, device=self.device_id, blocking=False)
            while not self.stop_event.is_set() and sd.get_stream().active:
                sd.sleep(100)
            sd.stop()
        except Exception as e:
            messagebox.showerror("Playback error", str(e))

# -------------------------------------------------
# Main App
# -------------------------------------------------
class AudioControllerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Audio Controller")
        self.geometry("1360x768")
        self.resizable(False, False)
        self.configure(fg_color="#2b2b2b")

        self.stop_event = threading.Event()
        self.play_thread = None
        self.current_track = None
        self.selected_device_id = None
        self.keybinds = {}

        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        self._create_center_sliders()
        self._create_bottom_bar()
        self._populate_devices()
        self.bind_all("<KeyPress>", self._on_key_pressed)

    # -------------------------------------------------
    def _create_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add audio…", command=self._browse_file)
        file_menu.add_separator()
        file_menu.add_command(label="Save profile…", command=self._save_profile)
        menubar.add_cascade(label="File", menu=file_menu)

        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Keybinds…", command=self._open_keybind_dialog)
        menubar.add_cascade(label="Settings", menu=settings)
        self.config(menu=menubar)

    def _create_top_bar(self):
        top = ctk.CTkFrame(self, height=45, fg_color="#1e1e1e")
        top.pack(fill="x", side="top")

        self.device_var = tk.StringVar(value="Select device")
        self.device_menu = ctk.CTkOptionMenu(
            top, variable=self.device_var, values=[], command=self._on_device_selected,
            fg_color="#088888", button_color="#088888", dropdown_hover_color="#0aa3a3",
        )
        self.device_menu.pack(padx=10, pady=5, side="right")

    def _create_left_panel(self):
        left = ctk.CTkFrame(self, width=340, fg_color="#1e1e1e")
        left.pack(fill="y", side="left", anchor="nw")
        left.pack_propagate(False)

        ctk.CTkLabel(left, text="Audio Files", font=("Helvetica", 14, "bold")).pack(pady=10)
        self.file_frame = ctk.CTkFrame(left, fg_color="#1e1e1e")
        self.file_frame.pack(fill="both", expand=True)
        ctk.CTkButton(left, text="Browse files…", command=self._browse_file,
                      fg_color="#088888", hover_color="#0aa3a3", width=260).pack(side="bottom", pady=10)

    def _create_center_sliders(self):
        center = ctk.CTkFrame(self, fg_color="#2b2b2b")
        center.pack(expand=True, fill="both", padx=15, pady=15)

        self.sliders = []
        hz_labels = [100, 200, 400, 800, 1600, 3200, 5000, 6400, 8000, 10000]
        for i, hz in enumerate(hz_labels):
            frame = ctk.CTkFrame(center, fg_color="#2b2b2b")
            frame.grid(row=0, column=i, padx=20, pady=5)
            val_label = ctk.CTkLabel(frame, text="50%", font=("Helvetica", 12, "bold"))
            val_label.pack(pady=(0, 3))

            s = ctk.CTkSlider(frame, from_=0, to=100, number_of_steps=100, width=20, height=250,
                               orientation="vertical", fg_color="#ffffff", button_color="#088888",
                               button_hover_color="#0aa3a3")
            s.set(50)
            s.pack()
            s.bind("<B1-Motion>", lambda e, sl=s, lbl=val_label: lbl.configure(text=f"{int(sl.get())}%"))
            s.bind("<ButtonRelease-1>", lambda e, sl=s, lbl=val_label: lbl.configure(text=f"{int(sl.get())}%"))
            ctk.CTkLabel(frame, text=f"{hz} Hz").pack(pady=(0, 0))
            self.sliders.append(s)

        # Additional sliders: Bass, Treble, Gain
        extra_labels = ["Bass", "Treble", "Gain"]
        for j, label in enumerate(extra_labels):
            frame = ctk.CTkFrame(center, fg_color="#2b2b2b")
            frame.grid(row=1, column=j * 3, padx=20, pady=5)
            val_label = ctk.CTkLabel(frame, text="50%", font=("Helvetica", 12, "bold"))
            val_label.pack(pady=(0, 3))

            s = ctk.CTkSlider(frame, from_=0, to=100, number_of_steps=100, width=20, height=250,
                               orientation="vertical", fg_color="#ffffff", button_color="#088888",
                               button_hover_color="#0aa3a3")
            s.set(50)
            s.pack()
            s.bind("<B1-Motion>", lambda e, sl=s, lbl=val_label: lbl.configure(text=f"{int(sl.get())}%"))
            s.bind("<ButtonRelease-1>", lambda e, sl=s, lbl=val_label: lbl.configure(text=f"{int(sl.get())}%"))
            ctk.CTkLabel(frame, text=label).pack(pady=(0, 0))
            self.sliders.append(s)

    def _create_bottom_bar(self):
        bottom = ctk.CTkFrame(self, height=50, fg_color="#1e1e1e")
        bottom.pack(fill="x", side="bottom")
        ctk.CTkButton(bottom, text="▶ Play", fg_color="#088888", hover_color="#0aa3a3", command=self._play_audio).pack(side="left", padx=10, pady=10)
        ctk.CTkButton(bottom, text="■ Stop", fg_color="#088888", hover_color="#0aa3a3", command=self._stop_audio).pack(side="left", padx=10, pady=10)

    # -------------------------------------------------
    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            output_devices = [(i, d["name"]) for i, d in enumerate(devices) if d["max_output_channels"] > 0 and d["hostapi"] >= 0]
            unique_devices = {}
            for i, name in output_devices:
                if name not in unique_devices:
                    unique_devices[name] = i
            self.device_menu.configure(values=list(unique_devices.keys()))
            self.device_mapping = unique_devices
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_device_selected(self, value):
        self.selected_device_id = self.device_mapping.get(value, None)

    def _browse_file(self):
        file = filedialog.askopenfilename(title="Select Audio File",
                                          filetypes=[("Audio files", "*.wav *.mp3 *.flac *.ogg *.aac *.aiff *.alac *.bwf")])
        if file:
            self.current_track = file

    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select an audio file first.")
            return
        if not self.selected_device_id:
            messagebox.showwarning("No device", "Please select an output device.")
            return
        if self.play_thread and self.play_thread.is_alive():
            self._stop_audio()
        self.stop_event.clear()
        sliders_values = [s.get() for s in self.sliders]
        self.play_thread = AudioPlayer(self.current_track, self.selected_device_id, self.stop_event, sliders_values)
        self.play_thread.start()

    def _stop_audio(self):
        self.stop_event.set()
        sd.stop()

    def _save_profile(self):
        sliders = [s.get() for s in self.sliders]
        data = {"sliders": sliders}
        file = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if file:
            with open(file, "w") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("Saved", "Profile saved successfully!")

    # -------------------------------------------------
    # Keybind handling
    # -------------------------------------------------
    def _open_keybind_dialog(self):
        dialog = ctk.CTkInputDialog(text="Enter a key (e.g., 'p' for play):", title="Keybind Setup")
        key = dialog.get_input()
        if not key:
            return
        dialog = ctk.CTkInputDialog(text="Bind to (play/stop):", title="Bind Action")
        action = dialog.get_input()
        if action not in ("play", "stop"):
            messagebox.showerror("Invalid", "Action must be 'play' or 'stop'.")
            return
        self.keybinds[key.lower()] = action
        messagebox.showinfo("Bound", f"Key '{key}' bound to '{action}'.")

    def _on_key_pressed(self, event):
        key = event.keysym.lower()
        if key in self.keybinds:
            action = self.keybinds[key]
            if action == "play":
                self._play_audio()
            elif action == "stop":
                self._stop_audio()

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp()
    app.mainloop()
