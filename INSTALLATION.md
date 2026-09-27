# Installation auf zusätzlichen Hosts — Anleitung

> Stand: 2026-09-27 · Multi-Host-Deployment von Proxy + Monitor

Diese Anleitung beschreibt, wie **Proxy** (`deepseek-proxy/`) und **Monitor**
(`deepseek_rates_latest.py`) auf einem neuen Host installiert werden. Als
Verteilweg dient **GitHub** (Repository `Kletternaut/DeepSeek`, Branch
`realtime-cost-monitor`) — kein `rsync`/`scp`.

Bereits installiert (Stand 2026-09-27):

| Host | Hostname | IP | Influx-`host`-Tag |
|---|---|---|---|
| raspi5 (Quelle) | raspi5 | 192.168.0.30 | raspi5 |
| raspi6 | raspi6 | 192.168.0.72 | raspi6 |
| raspi7 | raspi7 | 192.168.0.87 | raspi7 |

Zentrale InfluxDB: `http://192.168.0.32:8086` (Org `Jolly`, Bucket `deepseek-usage`).

---

## 1. Voraussetzungen

- SSH-Zugang als `admin` (Passwort bleibt beim User).
- Auf dem Zielhost: Python 3.10+ mit `venv` und `tkinter`, `git`, systemd.
  Prüfen:

  ```bash
  python3 --version
  python3 -m venv --help >/dev/null && echo "venv OK"
  python3 -c "import tkinter; print('tkinter OK')"
  which git
  ```

- **Achtung pyenv:** Auf einigen Hosts zeigt `python3` auf einen
  **pyenv-Shim** (`/home/admin/.pyenv/shims/python3`). Das funktioniert im
  Terminal, kann aber im Desktop-Kontext Probleme machen (siehe Abschnitt 3).

---

## 2. Installation

### 2.1 Altes Verzeichnis sichern + frisch klonen

```bash
cd ~
if [ -d deepseek ] && [ ! -d deepseek/.git ]; then
    mv deepseek deepseek.old-$(date +%Y%m%d%H%M%S)
fi
git clone --branch realtime-cost-monitor https://github.com/Kletternaut/DeepSeek.git deepseek
```

### 2.2 Proxy: venv + Abhängigkeiten

```bash
cd ~/deepseek/deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "import fastapi, uvicorn, httpx, influxdb_client; print('Proxy-Deps OK')"
```

### 2.3 Proxy: `influx.env` anlegen (Secret, 0600)

`influx.env` ist per `.gitignore` ausgeschlossen und wird **nicht** über GitHub
verteilt — auf jedem Host manuell anlegen:

```bash
cd ~/deepseek/deepseek-proxy
umask 077
cat > influx.env <<'EOF'
DEEPSEEK_INFLUX_URL=http://192.168.0.32:8086
DEEPSEEK_INFLUX_TOKEN=<TOKEN>
DEEPSEEK_INFLUX_ORG=Jolly
DEEPSEEK_INFLUX_BUCKET=deepseek-usage
EOF
chmod 600 influx.env
```

Optional `DEEPSEEK_INFLUX_HOST_NAME=<hostname>` setzen, sonst wird
`socket.gethostname()` verwendet (→ automatisch `raspi6`, `raspi7`, …).

### 2.4 Proxy: systemd-Service aktivieren

```bash
cd ~/deepseek/deepseek-proxy
sudo cp deepseek-proxy.service /etc/systemd/system/deepseek-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
systemctl is-active deepseek-proxy          # → active
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000/   # → HTTP 200
```

Verifikation des Influx-Sinks:

```bash
journalctl -u deepseek-proxy -n 20 | grep "InfluxDB-Sink"
```

### 2.5 VS Code: Custom Endpoint auf lokalen Proxy umbiegen

Datei `~/.config/Code/User/chatLanguageModels.json` bearbeiten. Das **Secret**
(`${input:chat.lm.secret.*}`) **unverändert lassen**, nur die Modell-URLs vom
DeepSeek-Direkt-Endpoint auf den lokalen Proxy umbiegen:

```bash
cd ~/.config/Code/User
cp chatLanguageModels.json chatLanguageModels.json.bak-$(date +%Y%m%d%H%M%S)
sed -i 's|https://api.deepseek.com/anthropic|http://127.0.0.1:8000/anthropic|g' chatLanguageModels.json
```

> Jeder Host hat sein **eigenes Secret** — nicht von einem anderen Host
> übernehmen. VS Code nach der Änderung neu laden.

### 2.6 Monitor: Desktop-Entry anlegen

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/deepseek-monitor.desktop <<'EOF'
[Desktop Entry]
Version=1.5
Type=Application
Name=DeepSeek Monitor
Comment=DeepSeek API Kosten- & Verbrauchs-Monitor
Icon=/home/admin/deepseek/deepseek_logo.png
Exec=env DISPLAY=:10.0 python3 /home/admin/deepseek/deepseek_rates_latest.py
Terminal=false
Categories=Utility;
EOF
chmod +x ~/.local/share/applications/deepseek-monitor.desktop
```

### 2.7 Monitor: bestehendes Desktop-Icon aktualisieren

Achtung: Auf bereits eingerichteten Hosts liegt oft noch ein **altes Icon auf dem
Schreibtisch** (`~/Desktop/deepseek.desktop`), das auf die alte Datei
`deepseek_rates.py` und das alte SVG-Icon zeigt. Diese Dateien existieren nach
dem Clone/Aufräumen **nicht mehr** → das Icon tut nichts. Dieses Icon ist das,
was der User tatsächlich anklickt (nicht der Menü-Eintrag aus 2.6).

```bash
cp ~/Desktop/deepseek.desktop ~/Desktop/deepseek.desktop.bak-$(date +%Y%m%d%H%M%S)
cat > ~/Desktop/deepseek.desktop <<'EOF'
[Desktop Entry]
Version=1.5
Type=Application
Name=DeepSeek
Comment=DeepSeek API Kosten- & Verbrauchs-Monitor
Icon=/home/admin/deepseek/deepseek_logo.png
Exec=env DISPLAY=:10.0 python3 /home/admin/deepseek/deepseek_rates_latest.py
Terminal=false
Categories=Utility;
EOF
chmod +x ~/Desktop/deepseek.desktop
```

Referenzen prüfen (müssen existieren):

```bash
ls -la /home/admin/deepseek/deepseek_rates_latest.py   # Symlink -> v2.4.0
ls -la /home/admin/deepseek/deepseek_logo.png
```

---

## 3. WICHTIG: `DISPLAY` beim Desktop-Start (xrdp)

Der Monitor ist eine Tkinter-App und braucht ein gültiges `$DISPLAY`. Auf den
Raspberry-Pi-Hosts läuft der Desktop jedoch über **xrdp auf Display `:10`**
(`Xorg :10`), nicht auf dem Standard-Display `:0`. Im Desktop-Startkontext ist
`$DISPLAY` deshalb **leer** → der Monitor bricht mit „no display name" ab und
das Icon scheint „nichts zu tun".

**Fix:** Im Desktop-Entry `Exec` mit `env DISPLAY=:10.0` starten (siehe 2.6).

Prüfen, welches Display aktiv ist:

```bash
ps aux | grep -E "Xorg|Xvnc|Xvfb" | grep -v grep
ls -la /tmp/.X11-unix/     # vorhandene X-Sockets, z. B. X0, X10
```

Schnelltest des Monitors (startet 5 s, dann Timeout):

```bash
DISPLAY=:10.0 timeout 5 python3 ~/deepseek/deepseek_rates_latest.py
```

Läuft er ohne Fehlermeldung, ist der Display-Wert korrekt.

---

## 4. Abschluss / Verifikation

1. Proxy: `systemctl is-active deepseek-proxy` → `active`.
2. Influx-Sink: `journalctl -u deepseek-proxy | grep "InfluxDB-Sink"` →
   zeigt URL + `host=<hostname>`.
3. Monitor: Desktop-Icon anklicken → Fenster öffnet sich.
4. VS Code neu laden → Modell-Requests laufen über `127.0.0.1:8000`.
5. Datenfluss prüfen: Im Monitor „Kosten & Verbrauch" mit Datenquelle
   „InfluxDB (zentral)" → Events aus allen Hosts sichtbar (Host-Filter).

---

## 5. Troubleshooting

| Symptom | Ursache | Lösung |
|---|---|---|
| Icon startet nichts | `$DISPLAY` fehlt (xrdp) | `env DISPLAY=:10.0` in `Exec` (Abschnitt 3) |
| Icon startet nichts (trotz `DISPLAY`) | altes Desktop-Icon zeigt auf tote Dateien (`deepseek_rates.py`, `DeepSeek.svg` nach Clone/Aufräumen weg) | Icon auf `deepseek_rates_latest.py` + `deepseek_logo.png` umbiegen (Abschnitt 2.7) |
| `python3` nicht gefunden im Desktop | pyenv-Shim nicht initialisiert | `python3` durch `/usr/bin/python3` ersetzen oder Login-Shell nutzen |
| Proxy läuft, aber keine Influx-Daten | `influx.env` fehlt / falsch | Datei prüfen, `chmod 600`, `systemctl restart deepseek-proxy` |
| VS Code nutzt noch Direkt-Endpoint | Config gecacht | VS Code neu laden, `chatLanguageModels.json` prüfen |
| Token in Log sichtbar | — | Token auf der InfluxDB rotieren (Secret-Hygiene) |
