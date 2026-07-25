@echo off
title MyAI VTuber - Installer v2
color 0A

echo ============================================================
echo           MyAI VTuber v2 - One-Click Installer
echo ============================================================
echo.

:: Check Python
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found! Download: https://www.python.org/downloads/
    pause & exit /b 1
)
python --version
echo.

:: Install pip requirements
echo [2/5] Installing Python dependencies...
pip install -r requirements.txt
echo.

:: Groq API
echo ============================================================
echo  [3/5] GROQ API SETUP (FREE - no GPU needed for LLM!)
echo ============================================================
echo.
echo  1. Go to: https://console.groq.com
echo  2. Sign up for free
echo  3. Click "API Keys" then "Create API Key"
echo  4. Copy your key into config.yaml under groq.api_key
echo.
echo  Opening Groq console...
start https://console.groq.com
echo.

:: Piper Voice
echo [4/5] Piper Voice Model...
echo Download from: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium
echo Place en_US-lessac-medium.onnx AND .onnx.json in voices/ folder
echo.

:: Live2D + External Software
echo [5/5] Additional Setup:
echo.
echo   Live2D Model:
echo     https://www.live2d.com/en/learn/sample/
echo     Place model folder in live2d_viewer/models/
echo.
echo   VB-Audio Virtual Cable (free): https://vb-audio.com/Cable/
echo   OBS Studio (free): https://obsproject.com/
echo.
echo ============================================================
echo  Installation complete!
echo.
echo  Next steps:
echo    1. Paste your Groq API key in config.yaml
echo    2. Edit config.yaml (Twitch channel + OAuth token)
echo    3. Place Piper voice files in voices/
echo    4. Place Live2D model in live2d_viewer/models/
echo    5. Run: python main.py
echo    6. Dashboard: http://localhost:5000
echo ============================================================
pause
