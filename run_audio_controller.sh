#!/usr/bin/env bash
# -------------------------------------------------
#  Create/activate virtual env and launch the app
# -------------------------------------------------

# ---- create venv if missing -------------------------
if [ ! -d "venv" ]; then
    python3 -m venv venv-audio
fi

# ---- activate venv ----------------------------------
source venv-audio/bin/activate

# ---- install / upgrade dependencies ------------------
pip install -r requirements.txt

# ---- run the program --------------------------------
python "$(dirname "$0")/audio_controller.py"