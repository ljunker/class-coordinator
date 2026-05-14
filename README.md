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

Beim ersten Start wird ein lokales Admin-Profil angelegt:

```text
Tinyauth-Login: admin
```

Für produktive Nutzung muss dieser Name zu einem Tinyauth-User passen:

```bash
ADMIN_USERNAME=admin uv run app.py
```

Die App verwaltet keine Passwörter mehr. Login und Passwort liegen in Tinyauth.
Class Coordinator speichert nur Anzeigenamen, Rollen und Class-Zugriffe.

Die SQLite-Datenbank liegt standardmäßig in `class_coordinator.sqlite3`.
Alternativ kann ein anderer Pfad gesetzt werden:

```bash
CLASS_COORDINATOR_DB=/pfad/zur/db.sqlite3 uv run app.py
```

## Docker

Tinyauth-User erzeugen:

```bash
docker run -i -t --rm ghcr.io/steveiliop56/tinyauth:v5 user create --interactive
```

Die Ausgabe als `TINYAUTH_AUTH_USERS` in einer `.env` ablegen, zum Beispiel:

```env
TINYAUTH_AUTH_USERS=admin:$2a$10$...
ADMIN_USERNAME=admin
```

Container bauen und starten:

```bash
docker compose up -d --build
```

Class Coordinator lauscht lokal auf `127.0.0.1:41234`. Tinyauth lauscht lokal
auf `127.0.0.1:3000`. Die SQLite-Datenbanken liegen persistent in den Volumes
`class-coordinator-data` und `tinyauth-data`.

## Nginx mit Tinyauth

Tinyauth selbst läuft unter `auth.kryptikk.de`:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name auth.kryptikk.de;

    ssl_certificate /etc/letsencrypt/live/kryptikk.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kryptikk.de/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Die Class-App läuft unter `class.kryptikk.de`. Die öffentlichen Class-Ansichten
`/classes/<id>` und `/classes/<id>/status.json` gehen auch ohne Login. Wenn ein
User schon über Tinyauth angemeldet ist, bekommt Flask dort trotzdem den Header
`remote-user` und kann Schreibrechte anzeigen. Alle anderen Pfade laufen hart
durch `auth_request`.

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name class.kryptikk.de;

    ssl_certificate /etc/letsencrypt/live/kryptikk.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kryptikk.de/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location ~ ^/classes/[0-9]+(/status\.json)?$ {
        auth_request /tinyauth;
        error_page 401 403 = @class_public_read;

        auth_request_set $tinyauth_remote_user $upstream_http_remote_user;
        auth_request_set $tinyauth_remote_name $upstream_http_remote_name;
        auth_request_set $tinyauth_remote_email $upstream_http_remote_email;

        proxy_pass http://127.0.0.1:41234;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header remote-user $tinyauth_remote_user;
        proxy_set_header remote-name $tinyauth_remote_name;
        proxy_set_header remote-email $tinyauth_remote_email;
    }

    location @class_public_read {
        proxy_pass http://127.0.0.1:41234;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        auth_request /tinyauth;
        error_page 401 = @tinyauth_login;
        error_page 403 = @tinyauth_unauthorized;

        auth_request_set $tinyauth_remote_user $upstream_http_remote_user;
        auth_request_set $tinyauth_remote_name $upstream_http_remote_name;
        auth_request_set $tinyauth_remote_email $upstream_http_remote_email;

        proxy_pass http://127.0.0.1:41234;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header remote-user $tinyauth_remote_user;
        proxy_set_header remote-name $tinyauth_remote_name;
        proxy_set_header remote-email $tinyauth_remote_email;
    }

    location = /tinyauth {
        internal;
        proxy_pass http://127.0.0.1:3000/api/auth/nginx;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header X-Forwarded-Uri $request_uri;
    }

    location @tinyauth_login {
        return 302 https://auth.kryptikk.de/login?redirect_uri=$scheme://$http_host$request_uri;
    }

    location @tinyauth_unauthorized {
        return 302 https://auth.kryptikk.de/unauthorized?username=unavailable;
    }
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
