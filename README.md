# ✦ Aiko VTuber — Autonomous AI Streamer System

> **Autonomous · Interactive · Live2D · Multi-Platform · Local Synthesis**

Aiko is an autonomous AI-powered VTuber designed for live streaming on Twitch and TikTok. Unlike standard conversational bots or assistants, Aiko is engineered with a streamer mindset: she engages in spontaneous monologues, shifts moods dynamically, interacts with chat probabilistically, plays interactive mini-games (e.g. Chess), and controls her Live2D model expressions in real time.

---

## ✨ Key Features

- 🧠 **Autonomous Streamer Brain (`Prompter`)**: Generates spontaneous monologues, switches activities, and decides dynamically when to reply to chat or react to events.
- 🎭 **Dynamic Emotion & Mood Engine**: Shifting moods (*hyped, chill, bored, gremlin, flustered, focused*) that adjust response tone, Live2D animations, and voice synthesis parameters.
- 🗣️ **Flexible Voice Synthesis**: Supports **GPT-SoVITS**, **Edge-TTS**, and **Piper TTS** with **Applio RVC** pitch-shifting for ultra-natural voice conversion.
- 🖼️ **Live2D Web Viewer**: WebSocket-driven HTML/JS Live2D renderer for OBS overlay integration with real-time lip-sync (RMS audio tracking).
- 💬 **Multi-Platform Chat Integration**: Dual support for Twitch IRC (`twitchAPI`) and TikTok Live chat (`TikTokLive`).
- ♟️ **Interactive Chess Engine**: Integrated Lichess bridge allowing viewers to challenge Aiko in live chess matches with ASCII board renders & real-time commentary.
- 🌐 **Web Intelligence & Autonomous Browsing**: Capable of searching the web and browsing favorite social media platforms during streams.
- 📊 **Sleek Control Dashboard**: Web-based control panel running at `http://localhost:5000` for system monitoring, live chat feed, emotion tracking, and manual actions.

---

## 🛠️ Architecture Overview

```
                        ┌────────────────────────┐
                        │      Twitch / TikTok   │
                        └───────────┬────────────┘
                                    │ (Chat & Events)
                                    ▼
┌───────────────────┐    ┌────────────────────────┐    ┌───────────────────┐
│ Live2D / OBS      │◄───┤    Core Stack / LLM    ├───►│ Web Dashboard     │
│ (ws://localhost)  │    │  (llama-3.3-70b-versatile)│    │ (http://localhost)│
└───────────────────┘    └───────────┬────────────┘    └───────────────────┘
                                     │ (TTS / RVC)
                                     ▼
                        ┌────────────────────────┐
                        │ Voice Engine / Audio   │
                        │ (GPT-SoVITS / EdgeTTS) │
                        └────────────────────────┘
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Python 3.10** or higher
- **Groq API Key** (Free tier available at [console.groq.com](https://console.groq.com))
- **Twitch OAuth Token** (Obtain from [twitchapps.com/tmi](https://twitchapps.com/tmi/))
- **VB-Audio Virtual Cable** (Optional, for routing audio into OBS)

### 2. Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/my-ai-vtuber.git
   cd my-ai-vtuber
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure credentials:**
   If `config.yaml` does not exist, copy `config.example.yaml` to `config.yaml`:
   ```bash
   cp config.example.yaml config.yaml
   ```
   Open `config.yaml` in a text editor and enter your Groq API key and Twitch token:
   ```yaml
   groq:
     api_key: "gsk_YourActualGroqApiKeyHere"

   twitch:
     channel: "your_twitch_channel"
     token: "oauth:your_twitch_oauth_token"
   ```

### 3. Running Aiko

Run the main orchestrator script:
```bash
python main.py
```
Or launch the master launcher interface:
```bash
python aiko.py
```

- **Live2D Viewer**: Open `http://localhost:8180/index.html` in browser or OBS Browser Source.
- **Control Dashboard**: Access `http://localhost:5000` to inspect live state and controls.

---

## 🎮 Dashboard

The built-in dashboard running on port 5000 provides a sleek, dark-mode control interface:

- **System Module Status**: Real-time status indicators for LLM, TTS, Live2D, Twitch IRC, STT, Stream Events, and Chess.
- **Direct Controls**: Trigger spontaneous monologues on demand or toggle voice mute.
- **Live Feeds**: View live chat messages and recent AI speech outputs.
- **Interactive Chess Monitor**: Control active Lichess games, view ASCII board representations, and manage top player leaderboards.

---

## 🤝 Funding & Support

This project is actively being developed as an open source / source-available project. If you would like to support future features (such as multi-modal vision integration, expanded game support, or dedicated GPU cloud hosting), consider contributing or sponsoring!

- **GitHub Sponsors**: [Sponsor Project](https://github.com/sponsors)
- **Ko-fi / Patreon**: Contact maintainer for sponsorship links.

---

## 📜 License

This project is licensed under the **PolyForm NonCommercial 1.0.0 License**.

- **Allowed**: You are free to view, download, fork, modify, and run this code for personal, educational, and non-commercial community purposes.
- **Restricted**: Commercial use, selling binaries/executables, or distributing closed-source derivative products for monetary gain requires explicit written permission from the author.

See the [LICENSE](LICENSE) file for details.
