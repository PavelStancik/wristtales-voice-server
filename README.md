**English** · [Česky](#česky)

# WristTales Voice Server

A local text-to-speech server for **[Binder](https://apps.apple.com/app/wristtales-binder)**, the macOS audiobook binder. It narrates books in a natural voice on your own Mac.

Nothing is uploaded: your text and the finished audio never leave the computer, there is no account, no API key and no per-minute charge. The server is free and all of its source is here.

## Quick start

Open **Terminal** (Spotlight → "Terminal") and paste these two commands:

```sh
curl -fsSL https://raw.githubusercontent.com/PavelStancik/wristtales-voice-server/main/install.sh | zsh
~/wristtales-voice-server/voice-server.sh
```

The first one clones this repository into `~/wristtales-voice-server` and installs everything (about **10–30 minutes**; it downloads roughly 12 GB, so you can do something else meanwhile). The second starts the server on `http://127.0.0.1:8000`. In Binder pick a chapter and choose **Narrate…**; Binder finds the server on its own.

Prefer to read the script before running it, or to put it elsewhere?

```sh
git clone https://github.com/PavelStancik/wristtales-voice-server.git ~/wristtales-voice-server
~/wristtales-voice-server/install.sh
```

Just checking whether your Mac qualifies, without installing anything: `~/wristtales-voice-server/install.sh --check`.

**Why it installs separately.** Binder ships through the App Store, and App Store apps may not download or run external code (guideline 2.5.2) or reach outside their sandbox. So Binder cannot install this server itself. It is a one-time step; afterwards you can forget about it.

## Requirements

What `install.sh` actually checks, and stops on if it is not met:

| | |
|---|---|
| Mac | Apple Silicon (M1 or newer). The model runs on Metal; Intel is refused. |
| Memory | at least **16 GB** (refused below that). 24 GB or more is recommended; between 16 and 24 you get a warning. |
| Disk | at least **15 GB free** on the volume with your home folder, checked before anything is downloaded. A full install (Higgs 8.7 GB, Whisper 1.5 GB, libraries ~1.2 GB, optional 8-bit convert 4.4 GB) takes about 16 GB. |
| Python | **3.11 or newer**. The installer tries `python3.14`, `3.13`, `3.12`, `3.11`, then `python3`. The pinned libraries were verified on 3.14. If none is found: `brew install python@3.13` ([Homebrew](https://brew.sh)). |
| `git` | only for the clone (`xcode-select --install` provides it). |
| macOS | 14 or newer is what MLX expects. The installer does not check this itself. |

## Daily use

```sh
~/wristtales-voice-server/voice-server.sh               # start (does nothing if it already runs)
~/wristtales-voice-server/voice-server.sh --check       # running? which version? is Whisper there?
~/wristtales-voice-server/voice-server.sh --stop        # stop
~/wristtales-voice-server/voice-server.sh --keep-awake  # don't let the Mac sleep while it runs
```

`--check` also prints a capabilities line when the server is up (see [Whisper](#whisper-and-the-capabilities-endpoint)). The script is **idempotent**: if the server already runs, starting it again does nothing and never kills it, so a chapter in progress is not lost. Logs go to `server.log` next to the script.

The server does **not** start by itself after a reboot, so it does not eat memory while you are not narrating. If you want it to, use the autostart recipe below.

### How Binder finds it

Binder looks for the server at **`http://127.0.0.1:8000`**, with no setup. Only if you run it on another port or on another Mac, enter its address as **Speech Server URL** in Binder's narration settings. To use another port: `VOICE_SERVER_PORT=8011 voice-server.sh` (and the same variable for `--check` and `--stop`).

### Autostart at login (optional)

macOS can launch it for you at login through a LaunchAgent. Paste into Terminal (adjust the path if you installed elsewhere):

```sh
cat > ~/Library/LaunchAgents/cz.wristtales.voice-server.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cz.wristtales.voice-server</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string><string>$HOME/wristtales-voice-server/voice-server.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
</dict></plist>
PLIST
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cz.wristtales.voice-server.plist
```

`AbandonProcessGroup` matters: the script starts the server in the background and exits, and without it launchd kills the server together with the script. To turn autostart off (a server that is already running keeps running; stop it with `--stop`):

```sh
launchctl bootout gui/$(id -u)/cz.wristtales.voice-server && rm ~/Library/LaunchAgents/cz.wristtales.voice-server.plist
```

### One server for several Macs

`voice-server.sh --lan` listens on `0.0.0.0` instead of `127.0.0.1`, so other Macs on the same network can use it; combine it with `--keep-awake` for a shared, long-running server (`--lan --keep-awake`). On start it prints the address (read via `ipconfig getifaddr en0`, falling back to `en1`), for example `http://192.168.1.23:8000`; enter it as **Speech Server URL** in Binder on the other Mac. `--check`, `--stop` and the idempotence guarantee behave the same with `--lan`.

> **The server has no authentication.** Anyone on the same network can reach and use it, `/wristtales/capabilities` included. Only use `--lan` on a network you trust (home, office), never on public or guest Wi-Fi.

## Whisper and the capabilities endpoint

Besides the Higgs voice model, `install.sh` downloads a second, smaller one: `mlx-community/whisper-large-v3-turbo-asr-fp16` (1.5 GB). It has its own step in the installer (6/7) and its own line in the final summary (`Whisper: ready` or `Whisper: missing`). What it gives you:

- **Narration check.** Higgs sometimes stops before the end of a block and the rest of the sentence is silently missing. Audio length does not reveal it (an audit of a whole book found 74 truncated blocks out of 5,231, and a length check passed all of them). Binder transcribes every narrated block through mlx_audio's own `POST /v1/audio/transcriptions` and re-narrates blocks whose ending is missing. It costs about 1–2 s per block, 5–9 % extra time per book.
- **Voice-sample transcription.** The same endpoint transcribes a voice-reference clip you add, so Binder can supply its transcript for you.

**Without Whisper** the server works, but Binder can only check audio length (truncated blocks slip through) and cannot transcribe a voice sample for you (supply the transcript by hand, see [Voices](#voices)). `install.sh` is idempotent: run it again to fetch the missing model. The model is loaded into memory on first use (about a minute) and then stays resident next to Higgs.

Binder is sandboxed and cannot look at the server's disk, so the server tells it what it has. **`GET /wristtales/capabilities`** (0.7.0+; no authentication, answers in milliseconds, loads no model):

```sh
curl -s http://127.0.0.1:8000/wristtales/capabilities
```
```json
{"server":"wristtales-voice-server","version":"0.7.0",
 "tts":{"models":["higgs-v3-bf16","higgs-v3-8bit"]},
 "transcription":{"available":true,"model":"mlx-community/whisper-large-v3-turbo-asr-fp16"}}
```

- `tts.models` lists only the models actually served (`higgs-v3-8bit` only if the convert exists).
- `transcription.available` is `true` when a complete Whisper snapshot is in the HuggingFace cache (`HF_HUB_CACHE`, `HF_HOME` and `XDG_CACHE_HOME` are honoured). It is a file check, not a trial load.
- An older server answers `404` here. That is how a client tells "server too old" from "Whisper missing".

`GET /v1/models` is deliberately unchanged: it lists the Higgs names and, once Whisper has been used, mlx_audio adds that resident model to the list as well. Do not infer Whisper's presence from it; use the capabilities endpoint.

## Models: bf16 and 8-bit

The server offers two variants of the same Higgs under **stable names**, so you never type a file path in Binder:

| name in `/v1/models` | what it is | size | speed |
|---|---|---|---|
| `higgs-v3-bf16` | the original weights from Boson | 8.7 GB | RTF 1.85 / **0.85** batched |
| `higgs-v3-8bit` | half-size convert | 4.4 GB | RTF 1.10 / **0.66** batched |

**Quality is indistinguishable**: on twelve blocks of Czech prose the WER is 0.023 for both, no rejects, and you cannot hear a difference. 8-bit is simply faster.

`install.sh` builds the 8-bit convert on your Mac (step 7/7, a few minutes; skip it with `SKIP_8BIT=1`) instead of downloading it, because Higgs is licensed Research/Non-Commercial and we do not redistribute derived weights. By hand:

```sh
~/wristtales-voice-server/venv/bin/python ~/wristtales-voice-server/quantize-8bit.py \
    --source bosonai/higgs-audio-v3-tts-4b --dest ~/wristtales-voice-server/models/higgs-v3-8bit
```

If the convert does not exist the server does not offer `higgs-v3-8bit` at all, and Binder says so up front instead of failing halfway through a long narration. **Do not try 4-bit**: it loses the end-of-speech marker and generates up to the token cap (a constant 47.8 s of audio whatever the input).

## How long narration takes

Read this before starting your first book. The model is **slower than real time**: roughly 1.3× on an M2 Pro when narrating one block at a time, so ten hours of listening is about **fifteen hours of computing**. With batching and the 8-bit model the same book takes about **seven hours**. That is the price of running on your Mac instead of someone else's server. Treat it as overnight work: start it in the evening with `--keep-awake` and it is done in the morning. Narration can be interrupted and resumed at any time.

## API notes

`POST /v1/audio/speech` (one block per request) is mlx_audio's own and **unchanged**; an older Binder works against it exactly as before. The server adds:

**Batch synthesis, `POST /v1/audio/speech/batch`.** Narrates several blocks in one request with one shared narrator reference. Measured on an M2 Pro (32 GB): a batch of 8 gives RTF 0.85 vs 1.85 serial, a **2.17×** speedup at identical quality; on the 8-bit model 1.67× (RTF 0.66 vs 1.10), because both levers attack the same bottleneck, memory bandwidth. The fastest combination, 8-bit batched, is **2.8×** faster than bf16 one block at a time. Batch size is `VOICE_SERVER_MAX_BATCH` (default `8`). **Do not raise it without measuring**: the memory ceiling above 8 is unknown (`ru_maxrss` says nothing about MLX unified memory, and an oversized batch of another model once thrashed into 44 GB of pageouts for 7.4 s of audio, 1 h 44 min). One failed item (e.g. empty text) returns an error for that item only; the rest of the batch arrives normally.

**Reproducible batches (0.5.0+).** The request accepts an optional `seed` (integer `0`–`2147483647`), forwarded once to `batch_generate()` for the whole batch and echoed as `"seed"` on every successful `results[]` item. Items produced by the serial fallback (after `batch_generate` raised), error items, and responses from servers older than 0.5.0 echo `"seed": null`. Omitting `seed` behaves as before.

**Inline reference audio (0.6.0+).** Binder is sandboxed and its voice clips live inside its container; this server can `stat()` them but not `open()` them (`EPERM`), so mlx_audio used to die with `miniaudio.DecodeError` *after* it had already answered `200` on the stream. Both speech routes now accept an optional **`ref_audio_base64`**: standard base64 of the complete WAV file. When present it is used and `ref_audio` (a path) is ignored entirely, including the file-exists check; when absent nothing changes. Invalid base64 or an empty payload returns `400`, more than 32 MB decoded returns `413`, always as JSON `detail` before any streaming starts. The clip is stored at `<tmp>/wristtales-voice-server-ref/<sha256>.wav` (mode `0600`, identical clips reused); files older than 24 h are deleted at startup. The payload is never logged.

**Capabilities (0.7.0+).** `GET /wristtales/capabilities`, described [above](#whisper-and-the-capabilities-endpoint).

## Voices

Higgs is a *cloning* model. You do not pick from a fixed set of voices: you give it a sample and it imitates it. Consequence:

> **Without a reference recording the model picks a random speaker**, on every single request. A chapter then changes narrator mid-sentence.

So Binder always sends a reference. Ready-made ones are in `voices/`:

| file | |
|---|---|
| `vypravec-2-expresivni.wav` | **recommended**: male, calm, proven on long prose |
| `vypravec-1-expresivni.wav` | male, backup |
| `vypravec-3-rychly.wav` | male, brisker tempo |
| `vypravecka-1-expresivni.wav`, `vypravecka-2-expresivni.wav` | female |
| `woman-en.wav`, `man-en.wav` | English |
| `woman-de.wav`, `man-de.wav` | German |

The reference's language governs the **accent, not the content**: an English reference reads Czech text too, just with an English accent. Choose by the language of the book. All of them are **synthetic**, generated by this model; none is a recording of a real person.

Each recording has a `.txt` of the same name with its transcript, sent as `ref_text` next to the audio:

```
voices/
  vypravec-2-expresivni.wav     <- the reference
  vypravec-2-expresivni.txt     <- its transcript
```

A missing `.txt` is not an error; narration works without it. **A mismatched transcript is worse than none**, because it actively misleads the clone: replace the recording, replace the transcript. For your own voice you need **20–30 seconds** of clean, calm reading, 24 kHz mono WAV, no music and no noise. Put it in `voices/` with its `.txt` and pick it in Binder. Details in [`voices/README.md`](voices/README.md).

## Update and uninstall

Update (pulls the new version and installs what was added; models are not downloaded again):

```sh
~/wristtales-voice-server/install.sh --update
```

A running server keeps the old code until you restart it: `voice-server.sh --stop && voice-server.sh` (not in the middle of a narration).

Uninstall: stop the server, turn autostart off (above), delete the folder and, if you want the disk space back, the downloaded models. Nothing else is installed on your system.

```sh
~/wristtales-voice-server/voice-server.sh --stop
rm -rf ~/wristtales-voice-server
cd ~/.cache/huggingface/hub && rm -rf models--bosonai--higgs-audio-v3-tts-4b \
  models--mlx-community--whisper-large-v3-turbo-asr-fp16 \
  models--mlx-community--S3TokenizerV2 models--mlx-community--snac_24khz
```

If you set `HF_HOME` or `HF_HUB_CACHE`, the models are there instead.

## When something does not work

**"Nenašel jsem Python" (no Python found):** `brew install python@3.13`, then run the installer again.

**Binder says the server is not running:** check `voice-server.sh --check`. It does not start after a reboot unless you set up autostart.

**The server stops answering for a while:** that is normal. Requests are processed one at a time, so while it computes a block it does not even answer a status query, sometimes for tens of seconds. Do not kill it.

**Narration died in the middle of a book:** start the server again and resume. The log is in `server.log`.

**Czech sounds odd on short sentences:** under about two seconds the model sometimes guesses the language wrong. Longer paragraphs come out reliably.

**License:** MIT for the code and the bundled voices. The model weights (`bosonai/higgs-audio-v3-tts-4b`) carry Boson AI's own terms (Research/Non-Commercial).

---

# Česky

[⬆ English](#wristtales-voice-server)

**WristTales Voice Server** je lokální hlasový server pro **[Binder](https://apps.apple.com/app/wristtales-binder)** — namlouvá knihy přirozeným hlasem přímo na tvém Macu.

Nic se nikam neposílá: text ani hotový zvuk neopustí počítač, není potřeba žádný účet ani API klíč a nic se neplatí za minutu. Server je zdarma a jeho zdrojový kód je celý tady.

## Rychlý start

Otevři **Terminál** (Spotlight — ⌘ mezerník, napiš „Terminál") a vlož tyhle dva příkazy:

```sh
curl -fsSL https://raw.githubusercontent.com/PavelStancik/wristtales-voice-server/main/install.sh | zsh
~/wristtales-voice-server/voice-server.sh
```

První naklonuje tenhle repozitář do `~/wristtales-voice-server` a nainstaluje všechno (zhruba **10 až 30 minut**; stahuje se kolem 12 GB, můžeš u toho dělat něco jiného). Druhý spustí server na `http://127.0.0.1:8000`. V Binderu vyber kapitolu a dej **Namluvit…** — server si najde sám.

Raději si skript přečteš, než ho pustíš, nebo ho chceš jinam?

```sh
git clone https://github.com/PavelStancik/wristtales-voice-server.git ~/wristtales-voice-server
~/wristtales-voice-server/install.sh
```

Chceš se jen podívat, jestli ti to na Macu vůbec poběží, a nic neinstalovat: `~/wristtales-voice-server/install.sh --check`.

**Proč se to instaluje zvlášť.** Binder je v App Storu a aplikace z App Storu **nesmí stahovat a spouštět cizí kód** (pravidlo Applu 2.5.2) ani sahat mimo svoji izolovanou složku. Nemůže si tedy tenhle server nainstalovat sám. Je to jednorázová věc; pak už o něm nemusíš vědět.

## Co je potřeba

Co `install.sh` opravdu kontroluje a na čem se zastaví, když to nesedí:

| | |
|---|---|
| Mac | s čipem Apple (M1 a novější). Model počítá přes Metal; na Intelu instalátor skončí. |
| Paměť | aspoň **16 GB** (méně instalátor odmítne). Doporučeno 24 GB a víc; mezi 16 a 24 GB dostaneš varování. |
| Disk | aspoň **15 GB volných** na svazku s domovskou složkou, kontroluje se před stahováním. Plná instalace (Higgs 8,7 GB, Whisper 1,5 GB, knihovny ~1,2 GB, volitelný 8bit konvert 4,4 GB) zabere asi 16 GB. |
| Python | **3.11 nebo novější**. Instalátor zkouší `python3.14`, `3.13`, `3.12`, `3.11` a nakonec `python3`. Připnuté knihovny jsou ověřené na 3.14. Když žádný nenajde: `brew install python@3.13` ([Homebrew](https://brew.sh)). |
| `git` | jen na klonování (dodá ho `xcode-select --install`). |
| macOS | 14 a novější — to vyžaduje MLX. Instalátor to sám nekontroluje. |

## Každodenní používání

```sh
~/wristtales-voice-server/voice-server.sh               # spustit (když už běží, neudělá nic)
~/wristtales-voice-server/voice-server.sh --check       # běží? jaká verze? je tu Whisper?
~/wristtales-voice-server/voice-server.sh --stop        # zastavit
~/wristtales-voice-server/voice-server.sh --keep-awake  # nenechá Mac usnout, dokud server běží
```

`--check` navíc vypíše řádek se schopnostmi, když server běží (viz [Whisper](#whisper-a-endpoint-schopností)). Skript je **idempotentní**: když server už běží, druhé spuštění neudělá nic a hlavně ho neshodí, takže rozdělaná kapitola nepřijde vniveč. Log je v `server.log` vedle skriptu.

Po restartu Macu se server **sám nespouští**, aby ti nežral paměť, když zrovna nenamlouváš. Chceš to? Použij recept na automatické spuštění níže.

### Jak ho Binder najde

Binder hledá server na **`http://127.0.0.1:8000`**, bez jakéhokoli nastavení. Jen když ho pustíš na jiném portu nebo na jiném Macu, zadej jeho adresu jako **Speech Server URL** v nastavení namlouvání v Binderu. Jiný port: `VOICE_SERVER_PORT=8011 voice-server.sh` (stejná proměnná i u `--check` a `--stop`).

### Automatické spuštění po přihlášení (volitelné)

macOS ho umí spouštět za tebe přes LaunchAgent. Vlož do Terminálu (cestu uprav, pokud jsi instaloval jinam):

```sh
cat > ~/Library/LaunchAgents/cz.wristtales.voice-server.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cz.wristtales.voice-server</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string><string>$HOME/wristtales-voice-server/voice-server.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
</dict></plist>
PLIST
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cz.wristtales.voice-server.plist
```

`AbandonProcessGroup` je důležité: skript spustí server na pozadí a skončí, a bez toho launchd server zabije spolu se skriptem. Vypnutí automatického spuštění (už běžící server běží dál; zastav ho přes `--stop`):

```sh
launchctl bootout gui/$(id -u)/cz.wristtales.voice-server && rm ~/Library/LaunchAgents/cz.wristtales.voice-server.plist
```

### Jeden server pro víc Maců

`voice-server.sh --lan` poslouchá na `0.0.0.0` místo `127.0.0.1`, takže ho použijí i další Macy v téže síti; pro sdílený, dlouho běžící server přidej `--keep-awake` (`--lan --keep-awake`). Po startu vypíše adresu (zjištěnou přes `ipconfig getifaddr en0`, případně `en1`), např. `http://192.168.1.23:8000`; v Binderu na druhém Macu ji zadej jako **Speech Server URL**. `--check`, `--stop` i záruka idempotence fungují u `--lan` stejně.

> **Server nemá žádné přihlašování.** Kdokoli v téže síti se k němu dostane a může ho použít, včetně `/wristtales/capabilities`. Používej `--lan` jen v síti, které důvěřuješ (domácí, kancelářská), nikdy na veřejné nebo hostovské Wi-Fi.

## Whisper a endpoint schopností

Vedle hlasového modelu Higgs stáhne `install.sh` ještě druhý, menší: `mlx-community/whisper-large-v3-turbo-asr-fp16` (1,5 GB). Má v instalátoru vlastní krok (6/7) i vlastní řádek v závěrečném shrnutí (`Whisper: ready` nebo `Whisper: missing`). K čemu je:

- **Kontrola namluveného textu.** Higgs občas přestane mluvit dřív, než dojde na konec bloku, a zbytek věty tiše chybí. Z délky zvuku se to poznat nedá (audit celé knihy našel 74 uříznutých bloků z 5 231 a délková kontrola propustila všechny). Binder proto každý namluvený blok nechá přepsat přes vlastní `POST /v1/audio/transcriptions` od mlx_audio a bloky s chybějícím koncem namluví znovu. Stojí to zhruba 1–2 s na blok, u celé knihy 5–9 % času navíc.
- **Přepis hlasového vzorku.** Stejný endpoint přepíše nahrávku hlasu, kterou přidáš, takže Binder umí doplnit její přepis za tebe.

**Bez Whisperu** server funguje, ale Binder umí kontrolovat jen délku zvuku (uříznuté bloky projdou) a nepřepíše ti hlasový vzorek (přepis dodej ručně, viz [Hlasy](#hlasy)). `install.sh` je idempotentní: pusť ho znovu a chybějící model doplní. Model se do paměti načítá při prvním použití (asi minutu) a pak zůstane vedle Higgse.

Binder je v sandboxu a na disk serveru nevidí, proto mu server řekne, co má. **`GET /wristtales/capabilities`** (od 0.7.0; bez přihlášení, odpoví za milisekundy, nenačítá žádný model):

```sh
curl -s http://127.0.0.1:8000/wristtales/capabilities
```
```json
{"server":"wristtales-voice-server","version":"0.7.0",
 "tts":{"models":["higgs-v3-bf16","higgs-v3-8bit"]},
 "transcription":{"available":true,"model":"mlx-community/whisper-large-v3-turbo-asr-fp16"}}
```

- `tts.models` obsahuje jen modely, které server opravdu nabízí (`higgs-v3-8bit` jen když konvert existuje).
- `transcription.available` je `true`, když je v cache HuggingFace kompletní snapshot Whisperu (respektují se `HF_HUB_CACHE`, `HF_HOME` i `XDG_CACHE_HOME`). Je to kontrola souborů, ne zkušební načtení.
- Starší server tady vrací `404`. Podle toho klient pozná „server je starý“ od „Whisper chybí“.

`GET /v1/models` zůstává záměrně beze změny: vypisuje jména Higgse a poté, co se Whisper použije, mlx_audio přidá do seznamu i ten načtený model. Podle toho přítomnost Whisperu nepoznávej, použij endpoint schopností.

## Modely: bf16 a 8bit

Server nabízí dvě varianty téhož Higgse pod **stabilními jmény**, takže v Binderu nikdy nepíšeš cestu k souboru:

| jméno v `/v1/models` | co to je | velikost | rychlost |
|---|---|---|---|
| `higgs-v3-bf16` | původní váhy od Bosonu | 8,7 GB | RTF 1,85 / **0,85** v dávce |
| `higgs-v3-8bit` | poloviční konvert | 4,4 GB | RTF 1,10 / **0,66** v dávce |

**Kvalita je neodlišitelná**: na dvanácti blocích české prózy je WER 0,023 u obou, žádný zmetek a ani poslechem rozdíl nenajdeš. 8bit je prostě rychlejší.

8bit konvert vyrábí `install.sh` u tebe na Macu (krok 7/7, pár minut; přeskočit jde přes `SKIP_8BIT=1`) a nestahuje ho hotový, protože licence Higgse je Research/Non-Commercial a odvozené váhy nepřerozdělujeme. Ručně:

```sh
~/wristtales-voice-server/venv/bin/python ~/wristtales-voice-server/quantize-8bit.py \
    --source bosonai/higgs-audio-v3-tts-4b --dest ~/wristtales-voice-server/models/higgs-v3-8bit
```

Když konvert neexistuje, server jméno `higgs-v3-8bit` **vůbec nenabídne** a Binder to řekne hned, místo aby selhal uprostřed dlouhé narace. **4bit nezkoušej**: ztrácí značku konce a generuje až do stropu tokenů (konstantních 47,8 s zvuku bez ohledu na vstup).

## Jak dlouho namlouvání trvá

Přečti si to dřív, než pustíš první knihu. Model je **pomalejší než skutečný čas**: na M2 Pro zhruba 1,3násobek při namlouvání po jednom bloku, takže deset hodin poslechu znamená **kolem patnácti hodin počítání**. S dávkovým namlouváním a 8bit modelem jde stejná kniha za **zhruba sedm hodin**. Je to daň za to, že běží u tebe a ne na cizím serveru. Počítej s tím jako s prací na noc: pusť to večer s `--keep-awake` a ráno máš hotovo. Namlouvání se dá kdykoli přerušit a pokračovat později.

## Poznámky k API

`POST /v1/audio/speech` (jeden blok na požadavek) je přímo od mlx_audio a **beze změny**; starší Binder proti němu funguje úplně stejně jako dřív. Server přidává:

**Dávkové namlouvání, `POST /v1/audio/speech/batch`.** Namluví víc bloků jedním požadavkem se společnou referencí vypravěče. Naměřeno na M2 Pro (32 GB): dávka po osmi dává RTF 0,85 místo 1,85 po jednom, tedy zrychlení **2,17×** při shodné kvalitě; na 8bit modelu je to 1,67× (RTF 0,66 místo 1,10), protože obě páky útočí na totéž úzké hrdlo, propustnost paměti. Nejrychlejší kombinace, 8bit v dávce, je **2,8×** rychlejší než bf16 po jednom. Velikost dávky nastavuje `VOICE_SERVER_MAX_BATCH` (výchozí `8`). **Nezvyšuj ji bez měření**: paměťový strop nad dávkou 8 je neznámý, `ru_maxrss` u MLX (unified memory) nic neříká a příliš velká dávka jiného modelu na tomhle Macu jednou skončila v thrashingu (44 GB pageoutů, 1 h 44 min na 7,4 s zvuku). Selhání jednoho bloku (např. prázdný text) vrátí chybu jen u něj, ostatní bloky dorazí normálně.

**Reprodukovatelné dávky (0.5.0+).** Požadavek bere volitelný `seed` (celé číslo `0`–`2147483647`), který se jednou předá do `batch_generate()` pro celou dávku a vrátí se jako `"seed"` u každé úspěšné položky `results[]`. Položky ze sériového záložního běhu (po pádu `batch_generate`), položky s chybou i odpovědi serveru staršího než 0.5.0 vrací `"seed": null`. Bez `seed` se vše chová jako dřív.

**Reference vložená do požadavku (0.6.0+).** Binder běží v sandboxu a jeho nahrávky hlasů leží uvnitř jeho kontejneru; server je vidí (`stat` projde), ale neotevře je (`open` dá `EPERM`), takže mlx_audio padal na `miniaudio.DecodeError` až PO odeslání odpovědi `200`. Obě cesty pro řeč proto berou volitelné pole **`ref_audio_base64`**: standardní base64 celého WAV souboru. Je-li přítomné, použije se a `ref_audio` (cesta) se úplně ignoruje, včetně kontroly existence souboru; bez něj se nic nemění. Neplatné base64 nebo prázdný obsah vrátí `400`, víc než 32 MB po dekódování `413`, vždy jako JSON `detail` dřív, než začne streamování. Nahrávka se uloží do `<tmp>/wristtales-voice-server-ref/<sha256>.wav` (práva `0600`, stejná nahrávka se použije znovu); soubory starší než 24 h se mažou při startu. Obsah se nikdy neloguje.

**Schopnosti (0.7.0+).** `GET /wristtales/capabilities`, popsáno [výše](#whisper-a-endpoint-schopností).

## Hlasy

Higgs je **klonovací** model. Nevybíráš z hotové sady hlasů — dáváš mu ukázku a on ji napodobí. Důsledek:

> **Bez referenční nahrávky si model losuje mluvčího náhodně**, a to u každého požadavku zvlášť. Kapitola pak střídá vypravěče uprostřed věty.

Binder proto referenci posílá vždy. Hotové jsou ve složce `voices/`:

| soubor | |
|---|---|
| `vypravec-2-expresivni.wav` | **doporučený**: mužský, klidný, ověřený na dlouhé próze |
| `vypravec-1-expresivni.wav` | mužský, záloha |
| `vypravec-3-rychly.wav` | mužský, svižnější tempo |
| `vypravecka-1-expresivni.wav`, `vypravecka-2-expresivni.wav` | ženský |
| `woman-en.wav`, `man-en.wav` | anglicky |
| `woman-de.wav`, `man-de.wav` | německy |

Jazyk reference řídí **přízvuk, ne obsah**: anglická reference přečte i český text, jen s anglickým přízvukem. Vybírej podle jazyka knihy. Všechny jsou **syntetické**, vygeneroval je tenhle model, nejsou to nahrávky žádného skutečného člověka.

Ke každé nahrávce leží `.txt` téhož jména s jejím přepisem, který se posílá jako `ref_text` vedle zvuku:

```
voices/
  vypravec-2-expresivni.wav     <- reference
  vypravec-2-expresivni.txt     <- její přepis
```

Chybějící `.txt` není chyba, narace poběží i bez něj. **Nesedící přepis ale je horší než žádný**, protože klon aktivně mate: když vyměníš nahrávku, vyměň i přepis. Na vlastní hlas stačí **20 až 30 vteřin** čistého klidného čtení, 24 kHz mono WAV, bez hudby a bez šumu. Ulož ji do `voices/`, přidej `.txt` s přepisem a v Binderu ji vyber. Podrobnosti v [`voices/README.md`](voices/README.md).

## Aktualizace a odinstalace

Aktualizace (stáhne novou verzi a doinstaluje, co přibylo; modely se znovu nestahují):

```sh
~/wristtales-voice-server/install.sh --update
```

Běžící server drží starý kód, dokud ho nerestartuješ: `voice-server.sh --stop && voice-server.sh` (ne uprostřed namlouvání).

Odinstalace: zastav server, vypni automatické spuštění (viz výše), smaž složku a, chceš-li zpět místo na disku, i stažené modely. Nic dalšího se do systému neinstaluje.

```sh
~/wristtales-voice-server/voice-server.sh --stop
rm -rf ~/wristtales-voice-server
cd ~/.cache/huggingface/hub && rm -rf models--bosonai--higgs-audio-v3-tts-4b \
  models--mlx-community--whisper-large-v3-turbo-asr-fp16 \
  models--mlx-community--S3TokenizerV2 models--mlx-community--snac_24khz
```

Pokud sis nastavil `HF_HOME` nebo `HF_HUB_CACHE`, jsou modely tam.

## Když něco nefunguje

**„Nenašel jsem Python"**: `brew install python@3.13` a spusť instalátor znovu.

**Binder píše, že server neběží**: ověř `voice-server.sh --check`. Po restartu Macu se sám nespustí, pokud si nenastavíš automatické spuštění.

**Server chvíli neodpovídá**: to je v pořádku. Požadavky zpracovává jeden po druhém, takže když zrovna počítá blok, neodpoví ani na dotaz na stav, klidně desítky vteřin. Nezabíjej ho.

**Namlouvání spadlo uprostřed knihy**: pusť server znovu a naváže. Log najdeš v `server.log`.

**Čeština zní divně u krátkých vět**: u bloků kratších než asi dvě vteřiny model občas netrefí jazyk. Delší odstavce vycházejí spolehlivě.

**Licence:** MIT pro kód i přibalené hlasy. Váhy modelu (`bosonai/higgs-audio-v3-tts-4b`) mají vlastní podmínky Boson AI (Research/Non-Commercial).
