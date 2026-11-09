# Musikquiz

Ett musikquiz för fester och fredagsfika. Quizmastern visar quizet på storskärmen och lagen svarar A–D från sina mobiler, från vilket nät som helst. Du gör egna quiz med text, bilder och musikvideor direkt i appen och delar dem med andra som en quizfil.

**[Ladda ner senaste versionen →](../../releases/latest)**. Installationen står på nedladdningssidan.

## Så fungerar det

- Appen körs **på din egen dator**. När du dubbelklickar öppnas quizbiblioteket i webbläsaren.
- **Lagen ansluter via en tillfällig internetadress** ([Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)). Den kräver inget konto och visas som QR-kod på storskärmen. Om nätverket blockerar den finns **wifi-läget** som reserv.
- **Musik och video lämnar aldrig datorn.** Mobilerna får bara frågor och alternativ. Därför spelar Cloudflares gräns på 100 MB per anrop ingen roll.
- Quizen sparas i mappen `Musikquiz` i hemkatalogen. Varje quiz är en mapp med `quiz.json` och `media/`.
- **Exportera** ger en `.musikquiz.zip` som någon annan kan **importera**.

Mer finns på hjälpsidan i appen (`/hjalp`).

## Upphovsrätt

Appen innehåller ingen musik, inga videor och inga omslag. Ljudtestets slinga är egengjord (`tools/make_jingle.py`). Det du laddar upp i egna quiz ansvarar du själv för. Spela gärna låtar på en egen quizkväll, men dela inte quizfiler med upphovsrättsskyddat material offentligt.

## Utveckling

Bara Pythons standardbibliotek (3.9 eller senare). Ingen installation behövs för att köra från källkoden:

```bash
python3 src/app.py --dev          # server i förgrunden + webbläsare
MUSIKQUIZ_NO_TUNNEL=1 MUSIKQUIZ_HOME=/tmp/mq python3 src/app.py --dev   # utan internetadress, med testdata
python3 tests/test_server.py      # snabbtest
```

| Fil | Innehåll |
|---|---|
| `src/app.py` | Startprogram. Startar servern i bakgrunden och öppnar webbläsaren. |
| `src/server.py` | HTTP-server: värdporten (127.0.0.1) och den publika porten (bara mobilsidan) |
| `src/library.py` | Quizbibliotek, kontroll, import och export |
| `src/tunnel.py` | Startar och övervakar `cloudflared` |
| `src/static/` | Bibliotek, redigerare, storskärm (`host.html`) och mobilsida (`play.html`) |

### Säkerhet

- **Värdporten** lyssnar bara på `127.0.0.1`. Den kontrollerar `Host`-huvudet (skydd mot DNS-rebinding) och kräver huvudet `X-Musikquiz` på alla ändrande anrop (skydd mot CSRF).
- **Tunneln och wifi-läget** pekar på en separat publik port. Den svarar bara på mobilsidan och lagens API.

## Bygga och släppa en ny version

GitHub Actions bygger Mac- och Windows-versionen när du pushar en tagg:

```bash
git tag v2.0
git push origin v2.0
```

Zip-filerna hamnar under **Releases**. Lokalt bygger du med `build/build_mac.sh` (Mac) eller `build/build_windows.ps1` (Windows).

## Tredjepartskomponenter

Se [THIRD_PARTY.md](THIRD_PARTY.md).
