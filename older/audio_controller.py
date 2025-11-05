import os
import json
import threading
import tkinter as tk
import customtkinter as ctk
import sounddevice as sd
from tkinter import filedialog, messagebox
from pydub import AudioSegment
import numpy as np
import time

# -------------------------------------------------
# Optimized Audio Player with EQ and Timer
# -------------------------------------------------
class AudioPlayer(threading.Thread):
    def __init__(self, file_path, device_id, stop_event, eq_values, update_timer):
        super().__init__(daemon=True)
        self.file_path = file_path
        self.device_id = device_id
        self.stop_event = stop_event
        self.eq_values = eq_values
        self.update_timer = update_timer

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            samples = seg.get_array_of_samples()
            audio_np = np.array(samples).astype('float32') / 32768.0
            if seg.channels > 1:
                audio_np = audio_np.reshape((-1, seg.channels))

            duration = len(audio_np) / seg.frame_rate
            start_time = time.time()

            with sd.OutputStream(device=self.device_id, channels=seg.channels, samplerate=seg.frame_rate) as stream:
                blocksize = 1024
                idx = 0
                while idx < len(audio_np) and not self.stop_event.is_set():
                    block = audio_np[idx:idx + blocksize]
                    block = self._apply_eq(block)
                    stream.write(block)
                    idx += blocksize
                    elapsed = time.time() - start_time
                    self.update_timer(elapsed, duration)

        except Exception as e:
            messagebox.showerror("Playback Error", str(e))

    def _apply_eq(self, block):
        bass_gain = self.eq_values.get('bass', 1.0)
        treble_gain = self.eq_values.get('treble', 1.0)
        gain = self.eq_values.get('gain', 1.0)

        # Apply simple frequency shaping
        fft_data = np.fft.rfft(block, axis=0)
        freqs = np.fft.rfftfreq(len(block), 1 / 44100)

        # Bass boost (below 250 Hz)
        fft_data[freqs < 250] *= bass_gain

        # Treble boost (above 4000 Hz)
        fft_data[freqs > 4000] *= treble_gain

        block_eq = np.fft.irfft(fft_data, axis=0)
        block_eq *= gain
        return np.clip(block_eq, -1.0, 1.0)

# -------------------------------------------------
# Main Application
# -------------------------------------------------
class AudioControllerApp(ctk.CTk):
    SUPPORTED_EXT = (".wav", ".aiff", ".flac", ".mp3", ".ogg", ".aac")

    def __init__(self):
        super().__init__()
        self.title("Audio Controller")
        self.geometry("960x700")
        self.resizable(False, False)

        self.current_track = None
        self.play_thread = None
        self.stop_event = threading.Event()
        self.selected_device_id = None
        self.track_labels = {}
        self.keybinds = {}

        self.eq_values = {'bass': 1.0, 'treble': 1.0, 'gain': 1.0}

        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        self._create_bottom_bar()
        self._create_eq_sliders()
        self._populate_devices()

        self.bind_all("<KeyPress>", self._handle_keypress)

    # ---------------------------- UI ----------------------------
    def _create_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Audio", command=self._browse_file)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)

    def _create_top_bar(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", padx=10, pady=5)

        self.device_var = tk.StringVar(value="Select Output Device")
        self.device_menu = ctk.CTkOptionMenu(frame, variable=self.device_var, values=[], command=self._on_device_selected)
        self.device_menu.pack(side="right", padx=10)

        self.timer_label = ctk.CTkLabel(frame, text="00:00 / 00:00", font=("Arial", 16))
        self.timer_label.pack(side="left", padx=10)

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

    def _create_eq_sliders(self):
        frame = ctk.CTkFrame(self)
        frame.pack(side="right", fill="y", padx=20, pady=10)

        self.sliders = {}
        for name in ['Bass', 'Treble', 'Gain']:
            ctk.CTkLabel(frame, text=f"{name}").pack(pady=(10, 0))
            slider = ctk.CTkSlider(frame, from_=0.5, to=2.0, number_of_steps=30, command=lambda val, n=name: self._update_eq(n, val))
            slider.set(1.0)
            slider.pack(pady=10)
            self.sliders[name.lower()] = slider

    # ---------------------------- Logic ----------------------------
    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            output_devices = [(i, d['name']) for i, d in enumerate(devices) if d['max_output_channels'] > 0 and d['hostapi'] == 0]
            seen = set()
            unique_devices = []
            for i, name in output_devices:
                if name not in seen:
                    unique_devices.append((i, name))
                    seen.add(name)
            self.devices = {name: i for i, name in unique_devices}
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

        name = os.path.basename(path)
        frame = ctk.CTkFrame(self.file_frame)
        frame.pack(fill="x", pady=3)

        label = ctk.CTkLabel(frame, text=name)
        label.pack(side="left", padx=5)
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))

        bind_btn = ctk.CTkButton(frame, text="Bind Key", width=70, command=lambda p=path: self._bind_key_to_audio(p))
        bind_btn.pack(side="right", padx=5)

        self.track_labels[path] = label

    def _select_audio(self, path):
        self.current_track = path

    def _bind_key_to_audio(self, path):
        win = ctk.CTkToplevel(self)
        win.title(f"Bind Key - {os.path.basename(path)}")
        win.geometry("250x100")
        ctk.CTkLabel(win, text="Press a key to bind to this track").pack(pady=10)
        win.bind("<KeyPress>", lambda e: self._set_track_keybind(e, path, win))

    def _set_track_keybind(self, event, path, window):
        self.keybinds[event.keysym] = path
        messagebox.showinfo("Key Bound", f"Key '{event.keysym}' bound to {os.path.basename(path)}")
        window.destroy()

    def _handle_keypress(self, event):
        if event.keysym in self.keybinds:
            self.current_track = self.keybinds[event.keysym]
            self._play_audio()
        elif event.keysym == 'Escape':  # Global Stop
            self._stop_audio()

    def _update_eq(self, name, value):
        self.eq_values[name.lower()] = float(value)

    def _update_timer_label(self, elapsed, duration):
        elapsed_str = time.strftime('%M:%S', time.gmtime(elapsed))
        duration_str = time.strftime('%M:%S', time.gmtime(duration))
        self.timer_label.configure(text=f"{elapsed_str} / {duration_str}")

    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select an audio file.")
            return
        if self.play_thread and self.play_thread.is_alive():
            self._stop_audio()

        self.stop_event.clear()
        self.play_thread = AudioPlayer(self.current_track, self.selected_device_id, self.stop_event, self.eq_values, self._update_timer_label)
        self.play_thread.start()

    def _stop_audio(self):
        self.stop_event.set()
        sd.stop()

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp()
    app.mainloop()
