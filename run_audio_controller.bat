@echo off
rem -------------------------------------------------
rem  Create/activate virtual env and launch the app
rem -------------------------------------------------

rem ---- create venv if it doesn't exist -----------------
if not exist venv (
    python -m venv venv-audio
)

rem ---- activate venv ------------------------------------
call venv-audio\Scripts\activate

rem ---- install / upgrade dependencies --------------------
python -m pip install customtkinter sounddevice soundfile pydub numpy

rem ---- run the program ----------------------------------
python "%~dp0audio_controller.py"

pause

