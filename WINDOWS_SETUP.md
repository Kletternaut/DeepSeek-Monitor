# Windows-Installation & Test-Anleitung (Win11 / 25H2)

Diese Anleitung beschreibt, wie der Windows-Port gebaut und getestet wird.

---

## 0. Voraussetzungen

- Windows 11 (25H2)
- Python **3.11 oder 3.12** von <https://www.python.org/downloads/>
  - **Häkchen bei „Add python.exe to PATH"** setzen
  - **tcl/tk** muss installiert sein (standardmäßig aktiv) — der Monitor ist eine Tkinter-App
- PowerShell (vorinstalliert)
- Git (um den Branch zu klonen)

---

## 1. Repository klonen

```powershell
git clone https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor
```

Der Windows-Port ist bereits in `main` integriert — kein separater Branch nötig.

---

## 2. Eigenständige .exe bauen (PyInstaller)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

Das Skript:
1. installiert `pyinstaller`, die Proxy-Dependencies aus `requirements.txt` und `tzdata`
2. baut `dist\DeepSeek-Monitor.exe` (GUI, `--windowed`)
3. baut `dist\DeepSeek-Proxy.exe` (Server + Web-Dashboard, `--console`)

**Erwartetes Ergebnis:** beide `.exe`-Dateien entstehen ohne Fehler.

---

## 3. Proxy testen

```powershell
# Server + Dashboard auf Port 8000 starten:
.\dist\DeepSeek-Proxy.exe --port 8000
```

**Prüfungen:**
- [ ] Konsole zeigt `Proxy ready at http://127.0.0.1:8000`
- [ ] Dashboard im Browser: <http://127.0.0.1:8000/> lädt (HTML/JS-Seite)
- [ ] API-Endpunkt antwortet: `http://127.0.0.1:8000/api/summary` (JSON)
- [ ] Kein `ZoneInfoNotFoundError` in der Konsole
- [ ] Beenden mit **Ctrl+C** führt zu sauberem Shutdown (`Proxy stopped`)

**Datenpfad:** Die SQLite-DB liegt unter `%LOCALAPPDATA%\deepseek\usage.db`.

---

## 4. Monitor testen

```powershell
.\dist\DeepSeek-Monitor.exe
```

**Prüfungen:**
- [ ] GUI-Fenster öffnet sich ohne Crash
- [ ] Kein `ZoneInfoNotFoundError` (Zeitzone `Europe/Berlin` / `Asia/Shanghai` funktioniert)
- [ ] Sprache: GUI startet auf Deutsch (bei deutschem Windows) — sonst per Menü umstellbar
- [ ] API-Key hinterlegen: Balance wird abgefragt (API `https://api.deepseek.com/user/balance`)
- [ ] Datenquelle umschaltbar (Lokal/SQLite vs. InfluxDB)

**Wichtig — geteilter API-Key:**
Der Monitor speichert den Key unter `%APPDATA%\deepseek\api_key`.
Der **Proxy liest diesen Key nicht** — er leitet nur den `x-api-key`-Header des
Clients weiter. Der Key im `%APPDATA%` wird allein vom Monitor genutzt.

---

## 5. Optional: Proxy als Autostart-Task

```powershell
# Start bei Benutzer-Anmeldung (empfohlen):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1

# Start beim Boot als SYSTEM (nur wenn nötig):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1 -AtStartup

# Entfernen:
Unregister-ScheduledTask -TaskName "DeepSeek-Proxy" -Confirm:$false
```

> Hinweis: Bei `-AtStartup` läuft der Proxy als SYSTEM und schreibt die DB in das
> SYSTEM-Profil, nicht in `%LOCALAPPDATA%` des Benutzers. Für den Normalbetrieb
> den Standard (Anmeldung) verwenden.

---

## 6. VS Code als Client durch den Proxy (optional)

`~/.config/Code/User/chatLanguageModels.json` (Windows: `%APPDATA%\Code\User\chatLanguageModels.json`)
siehe Abschnitt 6 der `INSTALLATION.md` — der Proxy lauscht auf
`http://127.0.0.1:8000/anthropic`.

---

## 7. Testergebnis zurückmelden

Bitte für jeden Prüfpunkt angeben: **OK** oder **Fehlermeldung** (vollständiger
Stacktrace bei Fehlern). Besonders wichtig:

1. Build läuft durch (beide `.exe`)?
2. Proxy startet + Dashboard lädt?
3. Monitor startet, Balance-Abfrage funktioniert, Zeitzone korrekt?
4. Irgendein `ZoneInfoNotFoundError` / `ModuleNotFoundError` / `ImportError`?
5. Datenpfade korrekt (`%APPDATA%\deepseek`, `%LOCALAPPDATA%\deepseek`)?
