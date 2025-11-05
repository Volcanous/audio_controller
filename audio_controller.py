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
        # placeholders for filters (initialized in run when samplerate known)
        self.filters = []
        
        # Reduce CPU usage with thread priority
        try:
            import win32api, win32process, win32con
            self.pid = win32api.GetCurrentProcessId()
            self.handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, True, self.pid)
            win32process.SetPriorityClass(self.handle, win32process.HIGH_PRIORITY_CLASS)
        except ImportError:
            # Optional: if on Windows but win32api not available
            pass

    def run(self):
        try:
            seg = AudioSegment.from_file(self.file_path)
            # remember sample rate for processing
            self.samplerate = seg.frame_rate
            samples = seg.get_array_of_samples()
            audio_np = np.array(samples).astype('float32') / 32768.0
            if seg.channels > 1:
                audio_np = audio_np.reshape((-1, seg.channels))

            duration = len(audio_np) / seg.frame_rate
            start_time = time.time()

            # initialize IIR peaking filters for each band (once we know samplerate and channels)
            self._init_filters(seg.frame_rate, seg.channels)

            # Choose blocksize to match latency (pick nearest power-of-two bucket).
            latency = 0.05  # 50ms latency target
            # pick a practical block size near desired latency
            target_frames = int(seg.frame_rate * latency)
            choices = [256, 512, 1024, 2048, 4096, 8192]
            # choose the nearest block size from choices
            blocksize = min(choices, key=lambda x: abs(x - target_frames))
            
            with sd.OutputStream(device=self.device_id, 
                               channels=seg.channels, 
                               samplerate=seg.frame_rate,
                               blocksize=blocksize,
                               latency=latency) as stream:
                idx = 0
                last_update = time.time()
                update_interval = 0.1  # Update UI every 100ms
                
                print(f"[DEBUG] Playback loop started for {self.file_path}, duration={duration:.2f}s, blocksize={blocksize}")
                while idx < len(audio_np) and not self.stop_event.is_set():
                    if idx == 0:
                        print(f"[DEBUG] Entered playback loop for {self.file_path}")
                    block = audio_np[idx:idx + blocksize]
                    if len(block) < blocksize:  # Pad last block if needed
                        block = np.pad(block, [(0, blocksize - len(block)), (0,0)] if block.ndim > 1 else (0, blocksize - len(block)))
                    
                    # Quick diagnostic fast-path: if all EQ and tone sliders are near 0,
                    # skip the heavy per-band processing to see if filters are the cause
                    # of choppiness. This helps isolate CPU-bound issues.
                    if all(abs(float(v)) < 0.01 for v in self.eq_values.values()):
                        gain_db = float(self.eq_values.get('gain', 0.0)) * 0.7
                        gain_linear = 10.0 ** (gain_db / 20.0)
                        out_block = (block * gain_linear).astype('float32')
                        stream.write(out_block)
                        idx += blocksize
                        continue

                    # Process audio with EQ/compression
                    block = self._apply_eq(block)
                    # Ensure float32 contiguous output for sounddevice
                    out_block = np.ascontiguousarray(block.astype('float32'))
                    stream.write(out_block)
                    idx += blocksize
                    
                    # Update UI less frequently
                    current_time = time.time()
                    if current_time - last_update >= update_interval:
                        elapsed = current_time - start_time
                        print(f"[DEBUG] Timer update in playback: elapsed={elapsed:.2f}, duration={duration:.2f}")
                        self.update_timer(elapsed, duration)
                        last_update = current_time

        except Exception as e:
            print(f"[DEBUG] Playback error: {e}")
            messagebox.showerror("Playback Error", str(e))

    def _init_filters(self, fs, channels):
        # define band frequency ranges with more conservative Q values
        band_freqs = [
            (20, 60),      # Sub-bass
            (60, 250),     # Bass
            (250, 500),    # Low-mid
            (500, 2000),   # Mid
            (2000, 4000),  # Upper-mid
            (4000, 6000),  # Presence
            (6000, 20000), # Brilliance
        ]
        self.filters = []
        for fmin, fmax in band_freqs:
            f0 = (fmin * fmax) ** 0.5  # Geometric mean for center frequency
            # Use gentler Q values for more musical results
            Q = 1.0  # Fixed Q for more predictable response
            # initialize per-band filter state
            state = {
                'b': None,
                'a': None,
                'w1': np.zeros(channels, dtype='float32'),
                'w2': np.zeros(channels, dtype='float32'),
                'f0': f0,
                'Q': Q,
            }
            self.filters.append(state)

    def _design_peaking(self, f0, Q, gain_db, fs):
        # RBJ peaking EQ formula
        A = 10.0 ** (gain_db / 40.0)
        omega = 2.0 * np.pi * f0 / fs
        sn = np.sin(omega)
        cs = np.cos(omega)
        alpha = sn / (2.0 * Q)

        b0 = 1.0 + alpha * A
        b1 = -2.0 * cs
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cs
        a2 = 1.0 - alpha / A

        # normalize
        b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype='float64')
        a = np.array([1.0, a1 / a0, a2 / a0], dtype='float64')
        return b, a

    def _apply_biquad(self, x, b, a, state):
        """Block-wise vectorized transposed direct form II biquad for mono or stereo."""
        x_2d = x if x.ndim > 1 else x.reshape(-1, 1)
        n_frames, n_ch = x_2d.shape
        # Ensure state arrays exist and have correct shape
        if state.get('w1') is None or len(state.get('w1')) != n_ch:
            state['w1'] = np.zeros(n_ch, dtype='float32')
            state['w2'] = np.zeros(n_ch, dtype='float32')
        w1 = state['w1'].astype('float64')
        w2 = state['w2'].astype('float64')
        b = np.asarray(b, dtype='float64')
        a = np.asarray(a, dtype='float64')
        y = np.zeros((n_frames, n_ch), dtype='float64')
        # Vectorized block processing for each channel
        for ch in range(n_ch):
            x_ch = x_2d[:, ch]
            y_ch = np.zeros(n_frames, dtype='float64')
            w1_ch = w1[ch]
            w2_ch = w2[ch]
            for i in range(n_frames):
                y_ch[i] = b[0] * x_ch[i] + w1_ch
                w1_new = b[1] * x_ch[i] + w2_ch - a[1] * y_ch[i]
                w2_new = b[2] * x_ch[i] - a[2] * y_ch[i]
                w1_ch = w1_new
                w2_ch = w2_new
            y[:, ch] = y_ch
            w1[ch] = w1_ch
            w2[ch] = w2_ch
        state['w1'] = w1.astype('float32')
        state['w2'] = w2.astype('float32')
        return y[:, 0].astype('float32') if n_ch == 1 else y.astype('float32')

    def _apply_eq(self, block):
        """Optimized EQ application.

        Apply each band peaking filter, then optional bass/treble controls,
        then master gain and soft-knee compression. Ensures processing occurs
        once per block (previous bug applied bass/treble per band).
        """
        fs = getattr(self, 'samplerate', 44100)

        # Ensure block is float32
        block = np.asarray(block, dtype='float32')

        # Apply per-band peaking filters (fully vectorized, per channel)
        band_keys = ['sub_bass', 'bass_band', 'low_mid', 'mid', 'upper_mid', 'presence', 'brilliance']
        for state, key in zip(self.filters, band_keys):
            gain_db = float(self.eq_values.get(key, 0.0))
            b, a = self._design_peaking(state['f0'], state['Q'], gain_db, fs)
            state['b'] = b
            state['a'] = a
            try:
                block = self._apply_biquad(block, b, a, state)
            except Exception:
                # If a per-band filter fails, continue without stopping playback
                pass

        # Handle bass/treble tone controls (apply once per block)
        bass_db = float(self.eq_values.get('bass', 0.0)) * 0.5  # Reduce sensitivity
        treble_db = float(self.eq_values.get('treble', 0.0)) * 0.5  # Reduce sensitivity
        n_ch = block.shape[1] if block.ndim > 1 else 1

        if abs(bass_db) > 0.1:
            f0_bass = 100
            Q_bass = 0.7
            b_bass, a_bass = self._design_peaking(f0_bass, Q_bass, bass_db, fs)
            bass_state = {'w1': np.zeros(n_ch, dtype='float32'), 'w2': np.zeros(n_ch, dtype='float32')}
            block = self._apply_biquad(block, b_bass, a_bass, bass_state)

        if abs(treble_db) > 0.1:
            f0_treble = 8000
            Q_treble = 0.7
            b_treble, a_treble = self._design_peaking(f0_treble, Q_treble, treble_db, fs)
            treble_state = {'w1': np.zeros(n_ch, dtype='float32'), 'w2': np.zeros(n_ch, dtype='float32')}
            block = self._apply_biquad(block, b_treble, a_treble, treble_state)

        # Apply master gain once
        gain_db = float(self.eq_values.get('gain', 0.0)) * 0.7
        gain_linear = 10.0 ** (gain_db / 20.0)
        block = block * gain_linear

        # Soft-knee limiter/compressor to avoid harsh clipping
        threshold_db = -3.0
        ratio = 4.0
        knee_db = 6.0
        attack_ms = 5.0
        release_ms = 50.0

        threshold = 10.0 ** (threshold_db / 20.0)
        knee_start = 10.0 ** ((threshold_db - knee_db/2.0) / 20.0)
        knee_end = 10.0 ** ((threshold_db + knee_db/2.0) / 20.0)

        attack_coef = np.exp(-1.0 / (fs * attack_ms / 1000.0))
        release_coef = np.exp(-1.0 / (fs * release_ms / 1000.0))

        x_peak = np.max(np.abs(block), axis=1) if block.ndim > 1 else np.abs(block)
        gain_reduction = np.ones_like(x_peak)

        mask_below = x_peak < knee_start
        mask_knee = (x_peak >= knee_start) & (x_peak <= knee_end)
        mask_above = x_peak > knee_end

        gain_reduction[mask_below] = 1.0

        if mask_knee.any():
            knee_factor = (x_peak[mask_knee] - knee_start) / (knee_end - knee_start)
            comp_ratio = 1.0 + (ratio - 1.0) * knee_factor
            gain_reduction[mask_knee] = (knee_start / x_peak[mask_knee]) ** (1.0 - 1.0/comp_ratio)

        if mask_above.any():
            gain_reduction[mask_above] = (threshold / x_peak[mask_above]) ** (1.0 - 1.0/ratio)

        # Smooth gain changes
        for i in range(1, len(gain_reduction)):
            coef = attack_coef if gain_reduction[i] < gain_reduction[i-1] else release_coef
            gain_reduction[i] = coef * gain_reduction[i-1] + (1.0 - coef) * gain_reduction[i]

        if block.ndim > 1:
            gain_reduction = gain_reduction[:, None]
        block = block * gain_reduction

        return np.clip(block, -1.0, 1.0).astype('float32')

# -------------------------------------------------
# Main Application
# -------------------------------------------------
class AudioControllerApp(ctk.CTk):
    SUPPORTED_EXT = (".wav", ".aiff", ".flac", ".mp3", ".ogg", ".aac")

    def __init__(self):
        super().__init__()
        self.title("Audio Controller")
        self.geometry("1080x640")
        self.resizable(False, False)

        # colors
        self.accent_color = "#017575"  # dark cyan for buttons
        self.remove_color = "#800000"  # red for remove (X) button

        # Create sounds directory if it doesn't exist
        self.sounds_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
        os.makedirs(self.sounds_dir, exist_ok=True)
        
        # keybinds file and load existing binds
        self.keybinds_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keybinds.json")
        self.load_keybinds()

        self.current_track = None
        self.play_thread = None
        self.stop_event = threading.Event()
        self.selected_device_id = None
        self.track_labels = {}
        self.keybinds = {}

        # EQ values: gains and single frequency cutoff
        self.eq_values = {
            'gain': 0.0,
            'treble': 0.0,
            'bass': 0.0,
            'sub_bass': 0.0,
            'bass_band': 0.0,
            'low_mid': 0.0,
            'mid': 0.0,
            'upper_mid': 0.0,
            'presence': 0.0,
            'brilliance': 0.0,
        }

        self._create_menu()
        self._create_top_bar()
        self._create_left_panel()
        # load any previously copied sounds into the UI
        self._load_existing_sounds()
        self._create_middle_panel_with_sliders()
        self._populate_devices()
        self.bind_all("<KeyPress>", self._handle_keypress)

    def load_keybinds(self):
        try:
            if os.path.exists(self.keybinds_file):
                with open(self.keybinds_file, 'r') as f:
                    self.keybinds = {}
                    data = json.load(f)
                    for key, rel_path in data.items():
                        full_path = os.path.join(self.sounds_dir, rel_path)
                        if os.path.exists(full_path):
                            self.keybinds[key] = full_path
            else:
                self.keybinds = {}
        except Exception:
            self.keybinds = {}

    def save_keybinds(self):
        try:
            keybinds_data = {}
            for key, path in self.keybinds.items():
                if path.startswith(self.sounds_dir):
                    # Store relative path from sounds directory
                    rel_path = os.path.basename(path)
                    keybinds_data[key] = rel_path
            
            with open(self.keybinds_file, 'w') as f:
                json.dump(keybinds_data, f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save keybinds: {str(e)}")

    def restore_keybind(self, path):
        # Find any existing keybind for this path
        for key, bound_path in self.keybinds.items():
            if bound_path == path and path in self.track_labels:
                # show keybind in braces
                self.track_labels[path]['keylabel'].configure(text=f"{{{key}}}")
                break

    def _create_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Audio", command=self._browse_file)
        file_menu.add_command(label="Remove All", command=self._remove_all_audio)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)

    def _create_top_bar(self):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", padx=10, pady=5)
        self.timer_label = ctk.CTkLabel(frame, text="00:00 / 00:00", font=("Arial", 16))
        self.timer_label.pack(side="left", padx=10)

        # Play and Stop buttons on the right side for timer
        ctk.CTkButton(frame, text="▶ Play", command=self._play_audio, fg_color=self.accent_color).pack(side="left", padx=5)
        ctk.CTkButton(frame, text="■ Stop", command=self._stop_audio, fg_color=self.accent_color).pack(side="left", padx=5)

        self.device_var = tk.StringVar(value="Select Output Device")
        self.device_menu = ctk.CTkOptionMenu(frame, variable=self.device_var, values=[], command=self._on_device_selected, fg_color=self.accent_color, button_color=self.accent_color)
        self.device_menu.pack(side="right", padx=10)

    def _create_left_panel(self):
        frame = ctk.CTkFrame(self, width=350)
        frame.pack(side="left", fill="y", padx=10, pady=10)
        frame.pack_propagate(False)  # Prevent frame from shrinking

        self.file_frame = ctk.CTkScrollableFrame(frame, label_text="Audio Files")
        self.file_frame.pack(fill="both", expand=True)

        ctk.CTkButton(frame, text="Add Audio", command=self._browse_file, fg_color=self.accent_color).pack(pady=10)

    def _create_middle_panel_with_sliders(self):
        # Center panel for all controls
        frame = ctk.CTkFrame(self)
        frame.pack(side="left", fill="both", expand=True, padx=5, pady=10)

        self.sliders = {}
        self.value_labels = {}
        # Main controls with reduced range
        for name, range_val in [('Gain', 12), ('Treble', 10), ('Bass', 10)]:
            row = ctk.CTkFrame(frame)
            row.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(row, text=f"{name}", width=80, anchor="e").pack(side="left")
            val_label = ctk.CTkLabel(row, text="0 dB")
            val_label.pack(side="right", padx=5)
            slider = ctk.CTkSlider(row, from_=-range_val, to=range_val, number_of_steps=40, button_color=self.accent_color)
            slider.set(0.0)
            slider.pack(side="left", fill="x", expand=True, padx=5)
            slider.configure(command=lambda val, n=name: self._update_eq(n.lower(), val))
            self.sliders[name.lower()] = slider
            self.value_labels[name.lower()] = val_label

        # Band sliders and value labels
        bands = [
            ("sub_bass", "Sub-bass", 20, 60),
            ("bass_band", "Bass", 60, 250),
            ("low_mid", "Low Midrange", 250, 500),
            ("mid", "Midrange", 500, 2000),
            ("upper_mid", "Upper Midrange", 2000, 4000),
            ("presence", "Presence", 4000, 6000),
            ("brilliance", "Brilliance", 6000, 20000),
        ]
        for key, label, fmin, fmax in bands:
            row = ctk.CTkFrame(frame)
            row.pack(fill="x", pady=(10, 0))
            ctk.CTkLabel(row, text=f"{label} ({fmin}-{fmax} Hz)", width=160, anchor="e").pack(side="left")
            val_label = ctk.CTkLabel(row, text="0 dB")
            val_label.pack(side="right", padx=5)
            slider = ctk.CTkSlider(row, from_=-20.0, to=20.0, number_of_steps=40, button_color=self.accent_color)
            slider.set(0.0)
            slider.pack(side="left", fill="x", expand=True, padx=5)
            slider.configure(command=lambda val, k=key: self._update_eq(k, val))
            self.sliders[key] = slider
            self.value_labels[key] = val_label

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

        # Copy file to sounds directory
        try:
            filename = os.path.basename(path)
            new_path = os.path.join(self.sounds_dir, filename)
            i = 1
            # Handle duplicate filenames
            while os.path.exists(new_path):
                name, ext = os.path.splitext(filename)
                new_path = os.path.join(self.sounds_dir, f"{name}_{i}{ext}")
                i += 1
            with open(path, 'rb') as src, open(new_path, 'wb') as dst:
                dst.write(src.read())
            path = new_path
            print(f"[DEBUG] Added file to sounds dir: {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to copy file: {str(e)}")
            return

        name = os.path.basename(path)
        frame = ctk.CTkFrame(self.file_frame)
        frame.pack(fill="x", pady=3)

        label = ctk.CTkLabel(frame, text=name)
        label.pack(side="left", padx=5)
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))

        # Keybind display label (empty until a key is bound)
        key_label = ctk.CTkLabel(frame, text="", width=60)
        # Add remove button (red)
        remove_btn = ctk.CTkButton(frame, text="X", width=30, fg_color=self.remove_color, hover_color="#ff6666", command=lambda p=path, f=frame: self._remove_audio(p, f))
        remove_btn.pack(side="right", padx=2)
        bind_btn = ctk.CTkButton(frame, text="Bind Key", width=70, fg_color=self.accent_color, command=lambda p=path: self._bind_key_to_audio(p))
        bind_btn.pack(side="right", padx=5)
        key_label.pack(side="right", padx=5)

        # store both labels so we can update the key display later
        self.track_labels[path] = {'label': label, 'keylabel': key_label}
        # If this file has a saved keybind, restore it
        self.restore_keybind(path)
        print(f"[DEBUG] Added file entry to UI: {path}")

    def _add_audio_entry(self, path):
        """Create the UI entry for an existing sound file (no copying)."""
        name = os.path.basename(path)
        frame = ctk.CTkFrame(self.file_frame)
        frame.pack(fill="x", pady=3)

        label = ctk.CTkLabel(frame, text=name)
        label.pack(side="left", padx=5)
        label.bind("<Button-1>", lambda e, p=path: self._select_audio(p))

        key_label = ctk.CTkLabel(frame, text="", width=60)
        remove_btn = ctk.CTkButton(frame, text="X", width=30, fg_color=self.remove_color, hover_color="#ff6666", command=lambda p=path, f=frame: self._remove_audio(p, f))
        remove_btn.pack(side="right", padx=2)
        bind_btn = ctk.CTkButton(frame, text="Bind Key", width=70, fg_color=self.accent_color, command=lambda p=path: self._bind_key_to_audio(p))
        bind_btn.pack(side="right", padx=5)
        key_label.pack(side="right", padx=5)

        self.track_labels[path] = {'label': label, 'keylabel': key_label}
        self.restore_keybind(path)
        print(f"[DEBUG] Loaded file entry to UI: {path}")

    def _load_existing_sounds(self):
        try:
            for fname in sorted(os.listdir(self.sounds_dir)):
                full = os.path.join(self.sounds_dir, fname)
                if os.path.isfile(full) and fname.lower().endswith(self.SUPPORTED_EXT):
                    self._add_audio_entry(full)
        except Exception:
            # ignore errors during load
            pass

    def _remove_audio(self, path, frame):
        # Remove the frame from UI
        frame.destroy()
        
        # Remove from track labels
        if path in self.track_labels:
            del self.track_labels[path]
            
        # Remove from keybinds
        keys_to_remove = [k for k, v in self.keybinds.items() if v == path]
        for key in keys_to_remove:
            del self.keybinds[key]
        
        # Save updated keybinds
        self.save_keybinds()
        
        # Delete the file from sounds directory
        try:
            if os.path.exists(path) and path.startswith(self.sounds_dir):
                os.remove(path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete file: {str(e)}")

    def _remove_all_audio(self):
        if messagebox.askyesno("Remove All", "Are you sure you want to remove all sounds?"):
            # Clear UI
            for widget in self.file_frame.winfo_children():
                widget.destroy()
            
            # Clear tracking dictionaries
            self.track_labels.clear()
            self.keybinds.clear()
            
            # Save empty keybinds
            self.save_keybinds()
            
            # Delete all files in sounds directory
            try:
                for filename in os.listdir(self.sounds_dir):
                    file_path = os.path.join(self.sounds_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete files: {str(e)}")

    def _select_audio(self, path):
        self.current_track = path

    def _bind_key_to_audio(self, path):
        win = ctk.CTkToplevel(self)
        win.title(f"Bind Key - {os.path.basename(path)}")
        win.geometry("250x100")
        ctk.CTkLabel(win, text="Press a key to bind to this track").pack(pady=10)
        win.bind("<KeyPress>", lambda e: self._set_track_keybind(e, path, win))

    def _set_track_keybind(self, event, path, window):
        # Clear any other key that was previously bound to this same path
        old_keys = [k for k, v in list(self.keybinds.items()) if v == path and k != event.keysym]
        for k in old_keys:
            try:
                del self.keybinds[k]
            except KeyError:
                pass

        # If this key was already bound to a different path, clear that path's displayed key
        prev_path = self.keybinds.get(event.keysym)
        if prev_path and prev_path != path:
            prev_labels = self.track_labels.get(prev_path)
            if isinstance(prev_labels, dict):
                kl = prev_labels.get('keylabel')
                if kl:
                    kl.configure(text="")

        # Set the new binding
        self.keybinds[event.keysym] = path

        # Update the UI label for this path to show the bound key
        labels = self.track_labels.get(path)
        if isinstance(labels, dict):
            kl = labels.get('keylabel')
            if kl:
                # display keybind in braces
                kl.configure(text=f"{{{event.keysym}}}")

        # Save keybinds to file
        self.save_keybinds()
        messagebox.showinfo("Key Bound", f"Key '{event.keysym}' bound to {os.path.basename(path)}")
        window.destroy()

    def _update_eq(self, name, value):
        self.eq_values[name.lower()] = float(value)
        # Update value label if present
        if hasattr(self, 'value_labels') and name.lower() in self.value_labels:
            # show as dB on the far right
            self.value_labels[name.lower()].configure(text=f"{int(float(value))} dB")

    def _update_timer_label(self, elapsed, duration):
        print(f"[DEBUG] Timer update: elapsed={elapsed}, duration={duration}")
        try:
            elapsed_str = time.strftime('%M:%S', time.gmtime(elapsed))
            duration_str = time.strftime('%M:%S', time.gmtime(duration))
            self.timer_label.configure(text=f"{elapsed_str} / {duration_str}")
        except Exception as e:
            print(f"[DEBUG] Timer label error: {e}")

    def _handle_keypress(self, event):
        if event.keysym in self.keybinds:
            self.current_track = self.keybinds[event.keysym]
            self._play_audio()
        elif event.keysym == 'Escape':  # Global Stop
            self._stop_audio()

    def _play_audio(self):
        if not self.current_track:
            messagebox.showwarning("No track", "Please select an audio file.")
            return
        # If something is already playing, stop it and wait briefly to avoid overlapping playback
        if self.play_thread and self.play_thread.is_alive():
            # request stop and wait for thread to exit (small timeout to avoid blocking UI too long)
            self._stop_audio(wait=True)

        # Create a fresh stop event for this playback so previous stop signals don't affect new thread
        self.stop_event = threading.Event()
        # Pass the live eq_values dict so sliders affect playback immediately
        self.play_thread = AudioPlayer(self.current_track, self.selected_device_id, self.stop_event, self.eq_values, self._update_timer_label)
        self.play_thread.start()

    def _stop_audio(self, wait: bool = False):
        """Stop playback.

        If wait is True, join the running play thread for up to 1 second to allow it to exit
        before starting a new one. This prevents overlapping output when switching tracks
        via keybinds.
        """
        try:
            # signal the current playback thread to stop
            self.stop_event.set()
        except Exception:
            # if stop_event is missing or invalid, ignore
            pass

        # stop any ongoing output immediately
        try:
            sd.stop()
        except Exception:
            pass

        # optionally wait for the thread to finish to avoid overlap
        if wait and self.play_thread:
            try:
                self.play_thread.join(timeout=1.0)
            except Exception:
                pass

        # clear reference to finished thread
        if self.play_thread and not self.play_thread.is_alive():
            self.play_thread = None

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = AudioControllerApp() 
    app.mainloop()