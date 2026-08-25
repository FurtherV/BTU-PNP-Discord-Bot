# BTU PnP Discord Bot

Der Bot veröffentlicht jeden Monat eine Anmeldeabfrage für die letzte vollständig im Monat liegende Montag-bis-Sonntag-Woche. Mitglieder melden sich als Spieler, DM oder für beide Rollen an und wählen ihre verfügbaren Tage. Orga-Mitglieder können Antworten direkt in Discord prüfen und als Excel-Datei exportieren.

Aktuelle Version: **1.0.0**

## Funktionen

- automatischer Planungsstart am Monatsersten um 09:00 Uhr (`Europe/Berlin`)
- automatisches Planungsende eine Woche vor Beginn der Eventwoche um 23:59 Uhr
- persistente Discord-Buttons „Anmelden / Bearbeiten“ und äquivalente Slash-Commands
- bearbeitbare Anmeldung mit Rolle, Verfügbarkeit und Anmerkung
- private Adminübersichten, Einzelansichten und XLSX-Export
- manuell auslösbarer Produktionsstart und -abschluss als Ausfallsicherung
- isolierter Debugmodus mit eigener SQLite-Datenbank
- individuelle Zustellverfolgung für Admin-DMs
- automatische Wiederherstellung bestehender Buttons nach einem Neustart

## Discord-Anwendung vorbereiten

1. Im [Discord Developer Portal](https://discord.com/developers/applications) eine Anwendung und einen Bot erstellen.
2. Unter **Bot → Privileged Gateway Intents** den **Server Members Intent** aktivieren. Der Message Content Intent wird nicht benötigt.
3. Den Bot mit den Scopes `bot` und `applications.commands` einladen.
4. Im Ankündigungskanal folgende Bot-Berechtigungen erteilen:
   - Kanal ansehen
   - Nachrichten senden
   - Links einbetten
   - Nachrichtenverlauf anzeigen
   - Dateien anhängen
5. Eine Orga-Rolle anlegen. Mitglieder dieser Rolle und Server-Administratoren dürfen Adminbefehle verwenden; Erinnerungs-DMs gehen an die Mitglieder dieser Rolle.

Bei einer privaten Discord-Anwendung bleibt **Installation → Installations-Link** auf `Keine`. Der Bot wird dann über **OAuth2 → URL Generator** mit den Scopes `bot` und `applications.commands` eingeladen. Nur der Eigentümer der privaten Anwendung kann diese Installation durchführen.

Der Entwicklungsserver sollte ausschließlich für Tests verwendet werden. Slash-Commands werden für die konfigurierte Guild synchronisiert, wenn sich ihre Struktur geändert hat, und erscheinen dadurch üblicherweise sofort. Unveränderte Neustarts lösen keine erneute Discord-Synchronisierung aus.

## Windows-Entwicklung

Voraussetzung ist Python 3.10 oder neuer.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Anschließend die IDs und den Bot-Token in `.env` eintragen und starten:

```powershell
python main.py
```

Tests:

```powershell
python -m pytest
```

Discord-IDs lassen sich nach Aktivierung des Entwicklermodus über das Kontextmenü von Server, Kanal und Rolle kopieren.

## Befehle

Mitglieder:

- `/anmelden` – aktuelle Anmeldung anlegen oder bearbeiten
- `/abmelden` – aktuelle Anmeldung nach Bestätigung löschen

Orga/Admin:

- `/anmeldungen uebersicht [monat] [modus]`
- `/anmeldungen einzeln [monat] [mitglied] [modus]`
- `/anmeldungen export [monat] [modus]`
- `/monatsabfrage status [monat]`
- `/monatsabfrage planungsstart [monat]` – fehlende produktive Planung erstellen, einen gelöschten Post ersetzen oder einen bestehenden Post aktualisieren
- `/monatsabfrage planungsende [monat]` – Anmeldung nach Bestätigung schließen und noch nicht zugestellte DMs senden

`monat` verwendet das Format `YYYY-MM`. Ohne Angabe wird der aktuelle Monat verwendet. Wiederholte Produktionsaufrufe erzeugen weder doppelte Monatsdatensätze noch doppelte DMs an bereits erreichte Empfänger.

Debug, nur bei `DEBUG_ENABLED=true`:

- `/debug planungsstart [monat]`
- `/debug planungsende [monat]`
- `/debug status [monat]`
- `/debug diagnose` – Verbindung, Kanalrechte und persistente Buttons prüfen
- `/debug buttontest` – minimale Discord-Komponenteninteraktion prüfen
- `/debug zuruecksetzen [monat]`

Debug-Posts und -DMs sind echte Discord-Nachrichten und deutlich gekennzeichnet. Anmeldungen über einen Debug-Post landen ausschließlich in `DEBUG_DATABASE_PATH`. Der Scheduler greift nie auf diese Datenbank zu. Bei Adminansicht und Export muss `modus=debug` ausdrücklich gewählt werden.

`DEBUG_ENABLED=true` aktiviert die Debugbefehle, deaktiviert aber nicht die produktive Monatsautomatisierung.

## Konfiguration

| Variable | Bedeutung |
|---|---|
| `DISCORD_TOKEN` | geheimer Bot-Token |
| `DISCORD_GUILD_ID` | ID des einzigen unterstützten Servers |
| `ANNOUNCEMENT_CHANNEL_ID` | Zielkanal für Produktions- und Debugposts |
| `ORGANIZER_ROLE_ID` | Orga-Rolle für Adminzugriff und Erinnerungen |
| `TIMEZONE` | standardmäßig `Europe/Berlin` |
| `DATABASE_PATH` | produktive SQLite-Datei |
| `DEBUG_DATABASE_PATH` | strikt getrennte Debug-SQLite-Datei |
| `DEBUG_ENABLED` | aktiviert die `/debug`-Befehle |
| `LOG_LEVEL` | beispielsweise `INFO` oder `DEBUG` |

Produktions- und Debugpfad dürfen nicht identisch sein. `.env` und SQLite-Dateien sind durch `.gitignore` ausgeschlossen.

## Ubuntu-Deployment

Beispiel mit einem eigenen Systembenutzer und persistenten Daten unter `/var/lib/btu-pnp-bot`:

```bash
sudo useradd --system --home /opt/btu-pnp-bot --shell /usr/sbin/nologin pnpbot
sudo mkdir -p /opt/btu-pnp-bot /var/lib/btu-pnp-bot
sudo chown -R pnpbot:pnpbot /opt/btu-pnp-bot /var/lib/btu-pnp-bot
sudo -u pnpbot python3 -m venv /opt/btu-pnp-bot/.venv
sudo -u pnpbot /opt/btu-pnp-bot/.venv/bin/pip install -r /opt/btu-pnp-bot/requirements.txt
```

Die Produktionskonfiguration wird als `/etc/btu-pnp-bot.env` angelegt. Empfohlene Pfade:

```dotenv
DATABASE_PATH=/var/lib/btu-pnp-bot/registrations.sqlite3
DEBUG_DATABASE_PATH=/var/lib/btu-pnp-bot/debug-registrations.sqlite3
DEBUG_ENABLED=false
```

Danach die mitgelieferte Service-Datei installieren und an die tatsächlichen Pfade anpassen:

```bash
sudo cp deploy/pnp-bot.service /etc/systemd/system/pnp-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now pnp-bot
sudo systemctl status pnp-bot
sudo journalctl -u pnp-bot -f
```

Die Produktionsdatenbank sollte regelmäßig gesichert werden. Für ein konsistentes Online-Backup eignet sich beispielsweise `sqlite3 /var/lib/btu-pnp-bot/registrations.sqlite3 ".backup '/backup/registrations.sqlite3'"`. Der Bot-Prozess benötigt Schreibzugriff auf das Datenverzeichnis, nicht jedoch auf Quellcode oder Systemverzeichnisse.

## Verhalten bei Ausfällen

- Der Scheduler läuft minütlich und verwendet idempotente Operationen.
- Wird der Bot nach dem geplanten Start hochgefahren, erstellt er die noch offene Monatsabfrage nachträglich.
- Vorhandene Buttons offener Monatsabfragen werden beim Start anhand ihrer Discord-Message-ID erneut registriert.
- Gelöschte Ankündigungen werden nicht als wiederhergestellt gemeldet und bei Bedarf durch den Scheduler ersetzt.
- Nach Fristende wird eine offene Anmeldung geschlossen. Nicht vollständig zugestellte Erinnerungen werden frühestens nach sechs Stunden erneut versucht.
- `/monatsabfrage planungsstart` und `/monatsabfrage planungsende` lösen dieselben Produktionspfade manuell aus.
- Ein erneuter Abschluss sendet nur an Empfänger, deren DM noch nicht erfolgreich zugestellt wurde.
- Fehler erscheinen im Prozesslog; Discord-Fehler löschen oder überschreiben keine Anmeldedaten.
