import os
import json
import threading
import tkinter as tk
import customtkinter as ctk
import sounddevice as sd
from tkinter import filedialog, messagebox
from pydub import AudioSegment

# -------------------------------------------------
# Optimized Audio Player
# -------------------------------------------------
class AudioPlayer(threading.Thread):
    def __init__(self, file_path, device_id, stop_event):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.device_id = device_id
        self.stop_event = stop_event

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            samples = seg.get_array_of_samples()
            audio_np = (np.array(samples).astype('float32') / 32768.0)
            if seg.channels > 1:
                audio_np = audio_np.reshape((-1, seg.channels))

            with sd.OutputStream(device=self.device_id, channels=seg.channels, samplerate=seg.frame_rate) as stream:
                blocksize = 1024
                idx = 0
                while idx < len(audio_np) and not self.stop_event.is_set():
                    block = audio_np[idx:idx + blocksize]
                    stream.write(block)
                    idx += blocksize
        except Exception as e:
            messagebox.showerror("Playback Error", str(e))

# -------------------------------------------------
# Main Application
# -------------------------------------------------
class AudioControllerApp(ctk.CTk):
    SUPPORTED_EXT = (".wav", ".aiff", ".flac", ".mp3", ".ogg", ".aac")

    def __init__(self):
        super().__init__()
        self.title("Audio Controller")
        self.geometry("960x600")
        self.resizable(False, False)

        self.current_track = None
        self.play_thread = None
        self.stop_event = threading.Event()
        self.selected_device_id = None
        self.keybinds = {}

        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        self._create_bottom_bar()
        self._populate_devices()
        self.bind_all("<KeyPress>", self._handle_keypress)

    def _create_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Audio", command=self._browse_file)
        file_menu.add_separator()
        file_menu.add_command(label="Save Profile", command=self._save_profile)
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

    def _create_bottom_bar(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", side="bottom", pady=5)

        ctk.CTkButton(frame, text="▶ Play", command=self._play_audio).pack(side="left", padx=5)
        ctk.CTkButton(frame, text="■ Stop", command=self._stop_audio).pack(side="left", padx=5)

    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            output_devices = [(i, d['name']) for i, d in enumerate(devices) if d['max_output_channels'] > 0 and d['hostapi'] == 0]
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
        label = ctk.CTkLabel(self.file_frame, text=os.path.basename(path))
        label.pack(pady=3, anchor="w")
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))

    def _select_audio(self, path):
        self.current_track = path

    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select an audio file.")
            return
        if self.play_thread and self.play_thread.is_alive():
            self._stop_audio()

        self.stop_event.clear()
        self.play_thread = AudioPlayer(self.current_track, self.selected_device_id, self.stop_event)
        self.play_thread.start()

    def _stop_audio(self):
        self.stop_event.set()
        sd.stop()

    def _open_keybind_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("Keybinds")
        win.geometry("300x200")

        ctk.CTkLabel(win, text="Press a key to bind Play").pack(pady=10)
        win.bind("<KeyPress>", lambda e: self._set_keybind(e, "play", win))

        ctk.CTkLabel(win, text="Press a key to bind Stop").pack(pady=10)
        win.bind("<KeyRelease>", lambda e: self._set_keybind(e, "stop", win))

    def _set_keybind(self, event, action, window):
        self.keybinds[action] = event.keysym
        messagebox.showinfo("Key Bound", f"{action.capitalize()} bound to {event.keysym}")
        window.destroy()

    def _handle_keypress(self, event):
        if event.keysym == self.keybinds.get("play"):
            self._play_audio()
        elif event.keysym == self.keybinds.get("stop"):
            self._stop_audio()

    def _save_profile(self):
        data = {"keybinds": self.keybinds}
        file = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if file:
            with open(file, "w") as f:
                json.dump(data, f, indent=4)

if __name__ == "__main__":
    import numpy as np
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp()
    app.mainloop()
