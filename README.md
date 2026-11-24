# Eya Buddy

A small floating robot with a warm, casual, slightly witty best-friend personality. Pillow draws the character. With no cloud API key configured, chat automatically uses a local Ollama model at `http://localhost:11434/v1`; if Ollama is unavailable, the built-in offline replies still work. API requests run in a worker so the robot animation stays responsive.

## Install and run the app

In PowerShell:

```powershell
cd D:\eya_buddy
py -m pip install --user -r requirements.txt
py .\eya_buddy.py
```

Python 3.10+ with Tkinter is required. Pillow is the only Python package dependency.

## Install Ollama for local AI chat

1. Install Ollama from PowerShell:

   ```powershell
   winget install -e --id Ollama.Ollama
   ```

2. Finish any installer prompt. A PowerShell window that was already open during installation may have an old PATH. In the current window, use Ollama's full path or refresh PATH for that session:

   ```powershell
   $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
   & $ollamaExe --version
   $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
   ollama --version
   Invoke-RestMethod http://localhost:11434/api/tags
   ```

   The Windows Ollama app normally starts its local service. If it is not running, launch Ollama from the Start menu, or run `ollama serve` in another terminal.

3. Pull a compact model. Eya defaults to the lightweight `gemma3:1b` model:

   ```powershell
   ollama pull gemma3:1b
   ```

   If this terminal still cannot find `ollama`, run `& $ollamaExe pull gemma3:1b` instead.

   If you prefer the 3B Llama model and have more memory available, use `ollama pull llama3.2:3b`, then select it for Eya with `$env:OLLAMA_MODEL = "llama3.2:3b"` before starting the app.

4. Check that Ollama can chat locally:

   ```powershell
   ollama run gemma3:1b
   ```

   Type `Hi Eya, I had a long day. Can you cheer me up?` and press Enter. Use `/bye` to exit the model prompt.

5. Start Eya Buddy:

   ```powershell
   cd D:\eya_buddy
   py .\eya_buddy.py
   ```

   With no cloud key configured, Eya automatically uses `gemma3:1b` through Ollama's local OpenAI-compatible endpoint with a dummy local credential. After its first successful reply, the status under the chat says **Live · Ollama**. The first reply may take longer while the model loads into memory.

## Verify fallback behavior

If Ollama is stopped or no model is installed, Eya falls back to the built-in local friend replies. The chat remains usable and the bottom status says **Offline · local replies**. With Ollama installed, set `$env:EYA_BUDDY_OFFLINE = "1"` before launching to force that fallback; remove the override afterward:

```powershell
Remove-Item Env:EYA_BUDDY_OFFLINE -ErrorAction SilentlyContinue
```

## Optional cloud APIs

Cloud API keys are optional. If `GEMINI_API_KEY`, `OPENAI_API_KEY`, or the generic `EYA_BUDDY_API_KEY` is set, Eya uses that configured provider before Ollama. To keep chat local, remove those variables from the current PowerShell session before launching:

```powershell
Remove-Item Env:GEMINI_API_KEY,Env:OPENAI_API_KEY,Env:EYA_BUDDY_API_KEY -ErrorAction SilentlyContinue
```

Set `EYA_BUDDY_PROVIDER=ollama` to explicitly choose Ollama, `OLLAMA_MODEL` to change its model, or `OLLAMA_BASE_URL` to change the local server URL. Keys are never stored in project files. API status appears in the small footer, not in the chat bubbles. Recent conversation is held in memory for the current app session only.
