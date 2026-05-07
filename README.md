# Class Coordinator

Web-App zum Verwalten von Square-Dance-Classes. Caller können pro Class
ankreuzen, welche Figuren geteacht oder wiederholt wurden. Admins verwalten
Classes, Accounts und Caller-Zugriffe.

Die App ist in Python mit Flask gebaut und nutzt Jinja2 für Templates.

## Start

```bash
uv sync
uv run app.py
```

Danach ist die App unter `http://127.0.0.1:41234` erreichbar.

Beim ersten Start wird ein Admin angelegt:

```text
Benutzername: admin
Passwort: admin123
```

Für produktive Nutzung vorher eigene Werte setzen:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD='ein-langes-passwort' uv run app.py
```

Die SQLite-Datenbank liegt standardmäßig in `class_coordinator.sqlite3`.
Alternativ kann ein anderer Pfad gesetzt werden:

```bash
CLASS_COORDINATOR_DB=/pfad/zur/db.sqlite3 uv run app.py
```

Passwörter werden mit bcrypt und Cost-Faktor 13 gespeichert.

## Docker

Container bauen und starten:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD='ein-langes-passwort' docker compose up -d --build
```

Die App lauscht im Container auf Port `41234` und wird durch Compose nur auf
`127.0.0.1:41234` veröffentlicht. Die SQLite-Datenbank liegt persistent im
Volume `class-coordinator-data` unter `/data/class_coordinator.sqlite3`.

Für einen Nginx Reverse Proxy unter `/class` kann die App lokal so angebunden
werden:

```nginx
location = /class {
    return 301 /class/$is_args$args;
}

location ^~ /class/ {
    proxy_pass http://127.0.0.1:41234;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /class;
}
```

Den Container stoppen:

```bash
docker compose down
```

Die Daten bleiben dabei im Docker-Volume erhalten. Zum vollständigen Löschen der
Containerdaten:

```bash
docker compose down -v
```

## PyCharm

Eine Run Configuration liegt unter `.idea/runConfigurations/Class_Coordinator.xml`.
Vor dem ersten Start in PyCharm einmal `uv sync` ausführen und den Interpreter
`$PROJECT_DIR$/.venv/bin/python` verwenden.

## Programme erweitern

Programme werden aus JSON-Dateien in `data/programs/` geladen. Ein neues
Programm wie Plus oder A1 kann später als weitere Datei mit diesem Schema
hinzugefügt werden:

```json
{
  "key": "plus-2026",
  "name": "Plus",
  "effective_date": "2026-09-01",
  "source_name": "CALLERLAB Plus Program",
  "source_url": "https://example.invalid/source",
  "families": [
    {
      "number": "1",
      "name": "Example Family",
      "calls": ["Example Call"]
    }
  ]
}
```

Beim nächsten Start wird das Programm importiert. Bestehende Tracking-Einträge
bleiben an ihrem Programm hängen.

## Datenquelle

Die vorinstallierte Liste ist die neue CALLERLAB Mainstream-Liste, effective
September 1, 2026.

## Lizenz

Dieses Projekt steht unter der GNU General Public License v3.0 or later
(`GPL-3.0-or-later`). Details stehen in `LICENSE`.
