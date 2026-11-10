# Signera Mac-appen (valfritt)

Om Mac-appen signeras och notariseras hos Apple försvinner varningen vid första starten. Då behöver kollegorna inte gå via **Integritet och säkerhet**. Signeringen kräver ett **Apple Developer-konto**, som kostar 99 USD per år och skaffas på [developer.apple.com](https://developer.apple.com/programs/).

Bygget är redan förberett. När hemligheterna nedan finns i GitHub signeras och notariseras appen automatiskt vid nästa release. Utan dem byggs appen precis som i dag.

> Den här delen är inte testad, eftersom inget certifikat fanns när den skrevs. Kontrollera första bygget efteråt (se sist i dokumentet).

## 1. Skapa certifikatet
1. Logga in på developer.apple.com → **Certificates** → **+** → **Developer ID Application**.
2. Följ stegen (Nyckelhanterare → *Begär ett certifikat från en certifikatutfärdare*) och ladda ner certifikatet. Dubbelklicka för att lägga in det i Nyckelhanteraren.
3. I Nyckelhanteraren: högerklicka på certifikatet *Developer ID Application: Ditt Namn (TEAMID)* → **Exportera** → spara som `cert.p12` med ett lösenord.

## 2. Skapa ett appspecifikt lösenord
Gå till [account.apple.com](https://account.apple.com) → **Inloggning och säkerhet** → **Appspecifika lösenord**. Skapa ett lösenord som heter *Musikquiz-notarisering*.

## 3. Lägg in hemligheterna i GitHub
Gå till repot → **Settings → Secrets and variables → Actions → New repository secret**:

| Namn | Värde |
|---|---|
| `MACOS_CERT_P12` | innehållet i `cert.p12` som base64: `base64 -i cert.p12 \| pbcopy` och klistra in |
| `MACOS_CERT_PASSWORD` | lösenordet du valde vid exporten |
| `MACOS_SIGN_IDENTITY` | hela namnet, t.ex. `Developer ID Application: Ditt Namn (AB12CD34EF)` |
| `APPLE_ID` | e-postadressen för ditt Apple-konto |
| `APPLE_TEAM_ID` | team-id:t (de tio tecknen inom parentes ovan) |
| `APPLE_APP_PASSWORD` | det appspecifika lösenordet |

Radera `cert.p12` från datorn när du är klar.

## 4. Släpp en ny version
Skapa en ny versionstagg (t.ex. `v2.4`). Kontrollera sedan Mac-filen:

```bash
spctl -a -vv /Applications/Musikquiz.app
```

Om allt fungerat står det `accepted` och `source=Notarized Developer ID`.

Windows-appen kan också signeras, men certifikaten för det är dyrare och kräver oftast ett registrerat företag. Tills vidare visar installationsguiden hur man går förbi varningen i Windows.
