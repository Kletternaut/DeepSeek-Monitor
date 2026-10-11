# DeepSeek-Monitor

**Token-Usage-Analyse, API-Kostentracking und Echtzeit-Tarifmonitoring für DeepSeek — für BYOK-Workflows und darüber hinaus.**

Wer DeepSeek mit einem eigenen API-Schlüssel (Bring Your Own Key, BYOK) über Entwicklungswerkzeuge wie VS Code mit GitHub Copilot Chat nutzt, möchte nachvollziehen können, wie viele Tokens verbraucht werden und welche API-Kosten daraus entstehen. Diese Informationen sind im jeweiligen Client nicht immer zentral oder ausreichend detailliert sichtbar.

**DeepSeek-Monitor macht Nutzung und Kosten nachvollziehbar.** Das Projekt verbindet einen Echtzeit-Tarifmonitor mit einer detaillierten Analyse erfasster Token-Nutzung. Ein API-Proxy stellt dafür die von DeepSeek gemeldeten Usage-Daten bereit.

Das Projekt ist unabhängig und Open Source. Es ist nicht mit DeepSeek (深度求索) verbunden und wird weder von DeepSeek entwickelt noch gesponsert, unterstützt oder betrieben.

## Funktionen im Überblick

- **Echtzeit-Tarifmonitor:** Zeigt die aktuelle DeepSeek-Tarifzone, den zugehörigen Multiplikator und die verbleibende Zeit bis zum nächsten Zonenwechsel.
- **Token- und Kostenanalyse:** Zeigt erfasste Token-Nutzung, berechnete API-Kosten, Tagesübersichten und Details einzelner Requests.
- **API-Proxy:** Leitet kompatible Client-Anfragen an die DeepSeek API weiter und erfasst Usage-Daten aus den API-Antworten.
- **Flexible Datenspeicherung:** SQLite speichert Usage-Daten lokal; optional kann InfluxDB 2.x zusätzlich für eine zentrale, hostübergreifende Auswertung verwendet werden.
- **Kontostandsüberwachung:** Ein separates Desktop-Fenster überwacht den DeepSeek-API-Kontostand.

Die Kostenwerte werden aus den von der API gemeldeten Usage-Daten und der konfigurierten Preistabelle berechnet. Prüfe die Preistabelle und beachte die Hinweise zu historischen Preisen sowie zum jährlich zu aktualisierenden chinesischen Feiertagskalender, bevor du die Werte als Finanznachweis verwendest.

## Echtzeit-Tarifmonitor

Der Desktop-Monitor zeigt den aktuellen Tarifstatus und den zeitlichen Verlauf der Tarifzonen. Zu den Funktionen gehören:

- aktuelle Tarifzone und deren Multiplikator
- Countdown bis zum nächsten Tarifwechsel
- zeitliche Übersicht der Tarifzonen
- konfigurierbare Zeitzone für die Anzeige
- optionale akustische Warnung vor einem Tarifwechsel
- Überwachung des DeepSeek-API-Kontostands

Die Tarifberechnung und die Anzeigezeitzone sind getrennte Dinge: Die Tarifregeln werden anhand der dafür vorgesehenen UTC-Zeitfenster sowie des Datums in der Zeitzone **Asia/Shanghai** ausgewertet. Wochenenden und die im Code hinterlegten chinesischen gesetzlichen Feiertage werden als Off-Peak behandelt. Der Feiertagskalender ist derzeit für 2026 hinterlegt und muss für Folgejahre aktualisiert werden.

## Token-Nutzung und API-Kosten

Der Proxy erfasst Usage-Felder, die DeepSeek in den API-Antworten meldet, einschließlich gestreamter Server-Sent Events (SSE). Die erfassten Daten werden zu Request-Datensätzen zusammengeführt und anhand der konfigurierten Preistabelle bepreist.

Die Analyse kann unter anderem Folgendes darstellen:

- Input- und Output-Tokens
- gemeldete Cache-Hit- und Cache-Miss-Tokens
- berechnete Kosten pro Request und für ausgewählte Zeiträume
- Tarifzone und angewendete Preiskategorie
- Request-Details und Tagesübersichten
- optionale zentrale Auswertung mehrerer Hosts über InfluxDB 2.x

**Wichtig:** DeepSeek-Monitor tokenisiert Prompt- oder Antworttexte nicht selbst. Die Usage-Erfassung basiert auf den Token-Angaben, die der API-Stream tatsächlich liefert. Wenn die API bestimmte Felder nicht meldet, können diese Werte nicht unabhängig aus dem Text rekonstruiert werden.

Die Kostenberechnung hängt von den erfassten Usage-Feldern und der im Proxy konfigurierten Preistabelle ab. Die Preistabelle kann aktualisiert werden; historische Requests sind daher nicht automatisch ein revisionssicherer Nachweis der zu einem früheren Zeitpunkt geltenden Preise.

### Speicherung

- **SQLite:** lokale Speicherung der Proxy-Usage-Daten; immer aktiv.
- **InfluxDB 2.x (optional):** zusätzliche zentrale Speicherung für hostübergreifende Auswertungen.
- **API-Key-Fingerabdruck:** In der Usage-Datenbank wird ein Fingerabdruck zum Gruppieren von Requests gespeichert, nicht der Klartext des vom Client übermittelten API-Schlüssels.

Der Desktop-Monitor kann lokale Proxy-/SQLite-Daten oder eine konfigurierte InfluxDB-Datenquelle verwenden. Die Überwachung des Kontostands bleibt eine separate Funktion und ist nicht mit der requestbasierten Kostenanalyse gleichzusetzen.

## API-Proxy und Client-Kompatibilität

Der Proxy ist die technische Grundlage für die detaillierte Usage-Erfassung. Er leitet kompatible Anthropic-Messages-Anfragen an **DeepSeek** weiter und streamt die Antworten zum Client zurück.

Standardmäßig lauscht der Proxy lokal unter:

- Dashboard: `http://127.0.0.1:8000/`
- Anthropic-Messages-Basis-URL: `http://127.0.0.1:8000/anthropic`
- Endpunkte: `/anthropic/v1/messages` und `/v1/messages` (Alias)

Ein Client muss einen benutzerdefinierten Anthropic-Messages-kompatiblen Endpunkt unterstützen. Beispiele und Konfigurationshinweise findest du in der [Installationsanleitung](docs/INSTALLATION.md).

Mögliche Einsatzszenarien sind:

- **VS Code mit GitHub Copilot Chat:** benutzerdefinierter Modell-Endpunkt, sofern die verwendete VS-Code-Version und Konfiguration das gewünschte Modellprotokoll unterstützen.
- **GitHub Copilot CLI:** experimentelle BYOK-Konfiguration über den Anthropic-Provider. GitHubs dokumentierte Provider-Liste nennt Anthropic, nicht DeepSeek; Streaming und Tool-Aufrufe müssen mit der eingesetzten CLI-Version geprüft werden.
- **Claude Code:** experimentelle Konfiguration eines benutzerdefinierten Anthropic-Messages-Endpunkts. Anthropic unterstützt Claude Code offiziell nicht für die Verwendung mit Nicht-Claude-Modellen; der Upstream in dieser Konfiguration ist DeepSeek.

Dies sind Kompatibilitätskonfigurationen, keine pauschale Garantie für alle Versionen, Modelle oder Funktionen. Insbesondere Tool-Aufrufe, Reasoning-/Thinking-Funktionen und weitere protokollspezifische Features hängen vom Client und vom jeweiligen DeepSeek-Modell ab.

**Abgrenzung:** Der Upstream-Provider ist DeepSeek. Der Proxy ist kein generischer Multi-Provider-Router und leitet Anfragen nicht an Anthropics gehostete Claude-Modelle weiter.

Der Proxy leitet Request- und Response-Bodies ohne Dekodierung und erneute Kodierung weiter. HTTP-Transport-Header werden bei Bedarf gefiltert oder neu erzeugt; daher ist der Proxy nicht auf Ebene der vollständigen HTTP-Nachricht bytegenau transparent.

## Komponenten

| Komponente | Pfad | Zweck |
|---|---|---|
| API-Proxy | `deepseek-proxy/` | DeepSeek-Upstream, Streaming-Usage-Erfassung, Dashboard und Speicherung |
| Desktop-Monitor | `deepseek_rates.py` | Tkinter-Oberfläche für Tarifmonitor, Kontostand und Kosten-/Usage-Analyse |
| Monitor-Logik | `deepseek_monitor/` | Tarifberechnung, Konfiguration, Persistenz und Analyse |

## Architektur

```text
Kompatibler Client
(VS Code / Copilot CLI / Claude Code)
          |
          | Anthropic Messages API
          v
DeepSeek API Proxy  ------->  DeepSeek API
      |      |
      |      +--> Usage-Erfassung aus dem SSE-Stream
      |
      +--> SQLite (lokal, immer aktiv)
      |
      +--> InfluxDB 2.x (optional, mehrere Hosts)
                    ^
                    |
             Desktop-Monitor
             / Dashboard
```

- SQLite ist für Proxy-Usage-Datensätze immer aktiv.
- InfluxDB ist optional und erhält Usage-Daten zusätzlich zu SQLite.
- Der Monitor kann lokale Proxy-Daten oder eine zentrale InfluxDB-Datenquelle verwenden.
- Der Proxy speichert den vom Client übermittelten API-Schlüssel nicht im Klartext in der Usage-Datenbank.

## Screenshots

| Hauptmonitor | Kosten und Nutzung | Einstellungen |
|---|---|---|
| ![Hauptmonitor](screenshots/main.png) | ![Kosten und Nutzung](screenshots/costs-usage.png) | ![Einstellungen](screenshots/settings.png) |

![Alle Monitorfenster](screenshots/all.png)

Das Web-Dashboard des Proxys zeigt erfasste Token-Nutzung und die daraus berechneten Kosten:

![Proxy-Dashboard](screenshots/proxy.png)

## Schnellstart

Beispiel für Linux. Für vollständige Installationsschritte, Client-Konfiguration, systemd, Windows und InfluxDB siehe die [Installationsanleitung](docs/INSTALLATION.md).

### 1. Repository klonen und Proxy installieren

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Das Proxy-Dashboard ist anschließend standardmäßig unter `http://127.0.0.1:8000/` erreichbar.

### 2. Desktop-Monitor starten

In einem zweiten Terminal im Repository-Hauptverzeichnis:

```bash
python3 deepseek_rates.py
```

Proxy und Desktop-Monitor sind separate Komponenten. Für die detaillierte Kosten-/Usage-Ansicht muss der Proxy laufen und Usage-Daten erfassen. Weitere Details stehen in [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Sicherheit

Proxy und Dashboard besitzen **keine integrierte Authentifizierung oder Autorisierung**. Standardmäßig binden sie an `127.0.0.1`. Setze den Dienst nicht ungeschützt einem LAN oder dem Internet aus. Für einen Fernzugriff sind eine geeignete Zugriffskontrolle, TLS und eine restriktive Netzwerkfreigabe erforderlich.

Der Proxy leitet den vom Client bereitgestellten API-Schlüssel an DeepSeek weiter. Er benötigt dafür keinen separaten API-Schlüssel. Die Kontostandsabfrage des Desktop-Monitors kann unabhängig davon einen API-Schlüssel speichern; schütze dessen Konfiguration und Backups. Der Proxy beabsichtigt nicht, Prompt- oder Antwortinhalte dauerhaft zu speichern, aber Usage-Datensätze enthalten Metadaten wie Zeitstempel, Modell, Tokenzahlen, Request-ID und API-Key-Fingerabdruck. Schütze die SQLite-Datei und gegebenenfalls die InfluxDB.

Weitere Hinweise: [docs/SECURITY.md](docs/SECURITY.md).

## Dokumentation

| Datei | Inhalt |
|---|---|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Installation, Client-Konfiguration, systemd und Windows |
| [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) | Windows-Build und Tests für `.exe`-Dateien |
| [docs/INFLUXDB.md](docs/INFLUXDB.md) | InfluxDB-Integration und hostübergreifende Auswertung |
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | Verzeichnis- und Dateireferenz |
| [docs/changelog.md](docs/changelog.md) | Änderungshistorie |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Hinweise zur Mitarbeit |
| [packaging/README.md](packaging/README.md) | Windows-Paketierung |

## Lizenz

MIT — siehe [LICENSE](LICENSE).
