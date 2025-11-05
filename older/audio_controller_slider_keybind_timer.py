import os
import json
import threading
import tkinter as tk
import customtkinter as ctk
import sounddevice as sd
import numpy as np
from tkinter import filedialog, messagebox
from pydub import AudioSegment

class AudioPlayer(threading.Thread):
    def __init__(self, file_path, device_id, stop_event, update_timer_callback):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.device_id = device_id
        self.stop_event = stop_event
        self.update_timer_callback = update_timer_callback

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            samples = np.array(seg.get_array_of_samples()).astype('float32') / 32768.0
            if seg.channels > 1:
                samples = samples.reshape((-1, seg.channels))

            duration = len(samples) / seg.frame_rate
            with sd.OutputStream(device=self.device_id, channels=seg.channels, samplerate=seg.frame_rate) as stream:
                blocksize = 1024
                idx = 0
                elapsed = 0
                while idx < len(samples) and not self.stop_event.is_set():
                    block = samples[idx:idx + blocksize]
                    stream.write(block)
                    idx += blocksize
                    elapsed += blocksize / seg.frame_rate
                    self.update_timer_callback(elapsed, duration)
            self.update_timer_callback(duration, duration)
        except Exception as e:
            messagebox.showerror("Playback Error", str(e))

class AudioControllerApp(ctk.CTk):
    SUPPORTED_EXT = (".wav", ".aiff", ".flac", ".mp3", ".ogg", ".aac")

    def __init__(self):
        super().__init__()
        self.title("Audio Controller")
        self.geometry("1000x700")
        self.resizable(False, False)

        self.current_track = None
        self.play_thread = None
        self.stop_event = threading.Event()
        self.selected_device_id = None
        self.track_keybinds = {}  # keybind per track
        self.global_stop_key = None
        self.sliders = []

        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        self._create_center_sliders()
        self._create_bottom_bar()
        self._populate_devices()

        self.bind_all("<KeyPress>", self._handle_keypress)

    def _create_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Audio", command=self._browse_file)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Keybinds", command=self._open_keybind_dialog)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.config(menu=menubar)

    def _create_top_bar(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", padx=10, pady=5)

        self.device_var = tk.StringVar(value="Select Output Device")
        self.device_menu = ctk.CTkOptionMenu(frame, variable=self.device_var, values=[], command=self._on_device_selected)
        self.device_menu.pack(side="right", padx=10)

    def _create_left_panel(self):
        frame = ctk.CTkFrame(self, width=300)
        frame.pack(side="left", fill="y", padx=10, pady=10)

        self.file_frame = ctk.CTkScrollableFrame(frame, label_text="Audio Files")
        self.file_frame.pack(fill="both", expand=True)

        ctk.CTkButton(frame, text="Add Audio", command=self._browse_file).pack(pady=10)

    def _create_center_sliders(self):
        center = ctk.CTkFrame(self)
        center.pack(fill="both", expand=True, padx=20)

        slider_labels = ["Bass", "Treble", "Gain"]
        for i, label in enumerate(slider_labels):
            ctk.CTkLabel(center, text=label, font=("Helvetica", 14)).grid(row=i, column=0, padx=10, pady=10, sticky="e")
            s = ctk.CTkSlider(center, from_=-10, to=10, number_of_steps=200, width=400)
            s.set(0)
            s.grid(row=i, column=1, padx=10, pady=10)
            self.sliders.append(s)

    def _create_bottom_bar(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", side="bottom", pady=5)

        self.timer_label = ctk.CTkLabel(frame, text="00:00 / 00:00", font=("Helvetica", 14))
        self.timer_label.pack(side="right", padx=10)

        ctk.CTkButton(frame, text="▶ Play", command=self._play_audio).pack(side="left", padx=5)
        ctk.CTkButton(frame, text="■ Stop", command=self._stop_audio).pack(side="left", padx=5)

    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            output_devices = [(i, d['name']) for i, d in enumerate(devices) if d['max_output_channels'] > 0]
            self.devices = {name: i for i, name in output_devices}
            self.device_menu.configure(values=list(self.devices.keys()))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_device_selected(self, value):
        self.selected_device_id = self.devices.get(value)

    def _browse_file(self):
        file = filedialog.askopenfilename(title="Select Audio File", filetypes=[("Audio Files", "*.wav *.mp3 *.flac *.ogg *.aac *.aiff")])
        if file:
            self._add_audio_file(file)

    def _add_audio_file(self, path):
        if not path.lower().endswith(self.SUPPORTED_EXT):
            messagebox.showerror("Unsupported File", "This format is not supported.")
            return

        frame = ctk.CTkFrame(self.file_frame)
        frame.pack(fill="x", pady=3)

        label = ctk.CTkLabel(frame, text=os.path.basename(path))
        label.pack(side="left", padx=5)
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))

        bind_btn = ctk.CTkButton(frame, text="Bind Key", width=60, command=lambda p=path: self._bind_key_to_track(p))
        bind_btn.pack(side="right", padx=5)

    def _select_audio(self, path):
        self.current_track = path

    def _bind_key_to_track(self, path):
        win = ctk.CTkToplevel(self)
        win.title("Bind Key")
        win.geometry("250x100")
        ctk.CTkLabel(win, text="Press a key to bind to this track").pack(pady=10)
        win.bind("<KeyPress>", lambda e: self._set_track_keybind(e, path, win))

    def _set_track_keybind(self, event, path, window):
        self.track_keybinds[event.keysym] = path
        messagebox.showinfo("Key Bound", f"{os.path.basename(path)} bound to {event.keysym}")
        window.destroy()

    def _open_keybind_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("Global Stop Key")
        win.geometry("250x100")
        ctk.CTkLabel(win, text="Press a key to bind global stop").pack(pady=10)
        win.bind("<KeyPress>", lambda e: self._set_global_stop_key(e, win))

    def _set_global_stop_key(self, event, window):
        self.global_stop_key = event.keysym
        messagebox.showinfo("Key Bound", f"Global Stop bound to {event.keysym}")
        window.destroy()

    def _handle_keypress(self, event):
        if event.keysym == self.global_stop_key:
            self._stop_audio()
        elif event.keysym in self.track_keybinds:
            self.current_track = self.track_keybinds[event.keysym]
            self._play_audio()

    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select or bind an audio file.")
            return
        if self.play_thread and self.play_thread.is_alive():
            self._stop_audio()

        self.stop_event.clear()
        self.play_thread = AudioPlayer(self.current_track, self.selected_device_id, self.stop_event, self._update_timer)
        self.play_thread.start()

    def _stop_audio(self):
        self.stop_event.set()
        sd.stop()

    def _update_timer(self, elapsed, duration):
        mins, secs = divmod(int(elapsed), 60)
        total_mins, total_secs = divmod(int(duration), 60)
        self.timer_label.configure(text=f"{mins:02}:{secs:02} / {total_mins:02}:{total_secs:02}")

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp()
    app.mainloop()
