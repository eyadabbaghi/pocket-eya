# Pocket Eya

**Pocket Eya** is a tiny floating desktop buddy for Windows: a pastel mini robot that keeps you company, reacts to your mood, and chats with you using a local Ollama model.

> A little cozy corner on your desktop. No cloud API key needed for local chat.

## Features

- **Floating desktop widget** — compact, borderless, always-on-top robot that you can drag around your screen.
- **Animated mood expressions** — blinking, happy, sleepy, focused, stressed, curious, and playful faces with gentle idle movement.
- **Quick mood check-ins** — tap Happy, Tired, Focused, or Stressed to get a personal response.
- **Mini chat drawer** — a friendly greeting, readable conversation bubbles, and a compact message input.
- **Local AI chat** — uses Ollama on `localhost` with `gemma3:1b` by default. Recent messages are included during the current session.
- **Responsive replies** — AI requests run in the background, so the robot keeps moving while it thinks.
- **Offline fallback** — built-in friendly replies keep the chat available if Ollama is stopped or unreachable.
- **Optional cloud providers** — Gemini and OpenAI-compatible APIs are supported through environment variables. Ollama needs no API key.

## Quick start

### 1. Requirements

- Windows 10 or 11
- Python 3.10+ with Tkinter
- Ollama for local AI chat
- Pillow (installed in the next step)

### 2. Install Ollama and download a model

Install Ollama from PowerShell:

```powershell
winget install -e --id Ollama.Ollama
```

After the installer finishes, open a **new** PowerShell window so the Ollama command is available. If you want to use the current window instead, refresh its PATH:

```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
```

Check Ollama and download the default compact model:

```powershell
ollama --version
ollama pull gemma3:1b
```

Ollama normally runs its local service from the Windows tray app. If the service is stopped, open Ollama from the Start menu or run `ollama serve` in another terminal.

### 3. Install Python dependencies and run Pocket Eya

```powershell
cd D:\eya_buddy
py -m pip install --user -r requirements.txt
py .\eya_buddy.py
```

Pillow is the only Python package dependency. With no cloud API key configured, Pocket Eya automatically uses `gemma3:1b` through Ollama. After a successful AI reply, the footer shows **Live · Ollama**. The first reply can take a little longer while the model loads.

## Try it

1. Click **Yes** on the hello bubble to open the chat.
2. Choose a mood or type a note, for example: `I have a long day ahead. Help me get started.`
3. Look for **Thinking...** while the reply is being generated. The robot should keep animating.
4. After Ollama replies, check for **Live · Ollama** below the conversation.
5. Send a follow-up to continue the same conversation. Chat history lasts for the current app session and clears when the app exits.

If Ollama cannot reply, Pocket Eya uses its built-in offline responses and shows **Offline · local replies**. You can force this mode for a quick check:

```powershell
$env:EYA_BUDDY_OFFLINE = "1"
py .\eya_buddy.py
Remove-Item Env:EYA_BUDDY_OFFLINE -ErrorAction SilentlyContinue
```

## Choose a different local model

For a larger model, pull it first and select it before launching the app:

```powershell
ollama pull llama3.2:3b
$env:OLLAMA_MODEL = "llama3.2:3b"
py .\eya_buddy.py
```

To explicitly use Ollama even if a cloud key is present in the environment:

```powershell
$env:EYA_BUDDY_PROVIDER = "ollama"
py .\eya_buddy.py
```

## Start Pocket Eya automatically when you sign in

1. Find the windowed Python executable (`pythonw.exe`) that matches your Python installation:

   ```powershell
   py -c "import pathlib, sys; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))"
   ```

2. Press **Win + R**, enter `shell:startup`, and press Enter to open your personal Windows Startup folder.
3. In that folder, create a shortcut. Set **Target** to the path printed in step 1, followed by the script path in quotes. For example:

   ```text
   "C:\Users\Eya\AppData\Local\Programs\Python\Python312\pythonw.exe" "D:\eya_buddy\eya_buddy.py"
   ```

4. Set the shortcut's **Start in** folder to `D:\eya_buddy`, then save it.
5. Make sure Ollama is running at sign-in too. Open Ollama from the Windows Start menu; if you use a different Ollama startup setting, enable its start-on-login option.

Remove the shortcut from the Startup folder whenever you no longer want Pocket Eya to launch automatically.

## Optional API configuration

Ollama is the default when no cloud key is set. If you configure `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `EYA_BUDDY_API_KEY`, the selected cloud provider can be used instead. Set keys only in the environment; never put secrets in source files. For an OpenAI-compatible provider, `EYA_BUDDY_BASE_URL` and `EYA_BUDDY_MODEL` configure the endpoint and model.

To keep this app fully local when cloud keys are present, select Ollama explicitly:

```powershell
$env:EYA_BUDDY_PROVIDER = "ollama"
```

## Project files

| File | Purpose |
| --- | --- |
| `eya_buddy.py` | Floating window, robot drawing and animation, mood UI, and chat drawer |
| `eya_buddy_chat.py` | Ollama/cloud API integration and offline friend-style replies |
| `requirements.txt` | Python dependency list |

## License

No license has been added yet. Add a `LICENSE` file before redistributing the project if you want others to reuse it under specific terms.
