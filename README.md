# WristTales Voice Server

Lokální hlasový server pro **[Binder](https://apps.apple.com/app/wristtales-binder)** —
namlouvá knihy českým hlasem přímo na tvém Macu.

Nic se nikam neposílá. Text ani hotový zvuk neopustí počítač, není potřeba
žádný účet ani API klíč, a nic se neplatí za minutu. Server je zdarma a jeho
zdrojový kód je celý tady.

---

## Proč se to instaluje zvlášť

Binder je v App Storu, a aplikace z App Storu **nesmí stahovat a spouštět
cizí kód** (pravidlo Applu 2.5.2) ani sahat mimo svoji izolovanou složku.
Nemůže si tedy tenhle server nainstalovat sám, i kdyby chtěl.

Je to jednorázová věc: jednou vložíš příkaz do Terminálu a máš hotovo. Binder
si pak server najde sám a dál už o něm nemusíš vědět.

## Co je potřeba

| | |
|---|---|
| Mac | s čipem Apple (M1 a novější) — na Intelu to nepoběží |
| Paměť | aspoň 16 GB, doporučeno 24 GB a víc |
| Místo na disku | 15 GB (model 8,7 GB + knihovny) |
| macOS | 14 a novější |

Instalátor si tohle sám zkontroluje a rovnou řekne, co chybí.

## Instalace

Otevři **Terminál** (najdeš ho přes Spotlight — ⌘ mezerník, napiš „Terminál")
a vlož tenhle jeden řádek:

```sh
git clone https://github.com/PavelStancik/wristtales-voice-server.git ~/wristtales-voice-server && ~/wristtales-voice-server/install.sh
```

Poběží to zhruba **10 až 30 minut** podle rychlosti připojení — stahuje se
model o velikosti 8,7 GB. Můžeš u toho dělat něco jiného.

Chceš se jen podívat, jestli ti to na Macu vůbec poběží, a nic neinstalovat?

```sh
~/wristtales-voice-server/install.sh --check
```

## Spuštění

```sh
~/wristtales-voice-server/voice-server.sh
```

Server naskočí za pár vteřin a poslouchá na `http://127.0.0.1:8000`. Pak už jen
v Binderu vyber kapitolu a dej **Namluvit…** — server si najde sám.

Další užitečné přepínače:

```sh
voice-server.sh --check        # běží? nebo neběží?
voice-server.sh --stop         # zastavit
voice-server.sh --keep-awake   # nenechá Mac usnout, dokud server běží
```

`--keep-awake` se hodí u dlouhých knih. Displej se uspat smí, počítač ne.

Skript je **idempotentní**: když server už běží, druhé spuštění neudělá nic
a hlavně ho neshodí. Rozdělaná kapitola tak nepřijde vniveč.

## Jak dlouho namlouvání trvá

Tohle si přečti dřív, než pustíš první knihu, ať tě to nepřekvapí.

Model je **pomalejší než skutečný čas** — na M2 Pro zhruba 1,3násobek. Deset
hodin poslechu tedy znamená **kolem patnácti hodin počítání**.

Není to chyba, je to daň za to, že běží u tebe a ne na cizím serveru. Počítej
s tím jako s prací na noc: pusť to večer s `--keep-awake` a ráno máš hotovo.
Namlouvání se dá kdykoli přerušit a pokračovat později.

## Hlasy

Higgs je **klonovací** model. Nevybíráš z hotové sady hlasů — dáváš mu
ukázku a on ji napodobí. To má jeden zásadní důsledek:

> **Bez referenční nahrávky si model losuje mluvčího náhodně** — a to u každého
> požadavku zvlášť. Kapitola pak střídá vypravěče uprostřed věty.

Binder proto referenci posílá vždy. Ve složce `voices/` najdeš připravené
hlasy, které tímhle prošly:

| soubor | |
|---|---|
| `vypravec-2-expresivni.wav` | **doporučený** — mužský, klidný, ověřený na dlouhé próze |
| `vypravec-1-expresivni.wav` | mužský, záloha |
| `vypravec-3-rychly.wav` | mužský, svižnější tempo |
| `vypravecka-1-expresivni.wav` | ženský |
| `vypravecka-2-expresivni.wav` | ženský |

Všechny jsou **syntetické** — vygeneroval je tenhle model, nejsou to nahrávky
žádného skutečného člověka.

Chceš vlastní hlas? Stačí nahrávka **20 až 30 vteřin** čistého klidného
čtení, 24 kHz mono WAV, bez hudby a bez šumu. Ulož ji do `voices/` a v Binderu
ji vyber. Podrobnosti jsou v [`voices/README.md`](voices/README.md).

## Aktualizace

```sh
~/wristtales-voice-server/install.sh --update
```

Stáhne novou verzi a doinstaluje, co přibylo. Model se znovu nestahuje.

## Když něco nefunguje

**„Nenašel jsem Python"** — nainstaluj ho: `brew install python@3.13`.
Homebrew samotný je na [brew.sh](https://brew.sh).

**Binder píše, že server neběží** — ověř `voice-server.sh --check`. Server se
sám nespouští po restartu Macu; je to úmyslné, aby ti nežral paměť, když
zrovna nenamlouváš.

**Server chvíli neodpovídá** — to je v pořádku. Požadavky zpracovává jeden po
druhém, takže když zrovna počítá blok, neodpoví ani na dotaz na stav. Klidně
desítky vteřin. Nezabíjej ho.

**Namlouvání spadlo uprostřed knihy** — pusť ho znovu, naváže. Log najdeš
v `server.log`.

**Čeština zní divně u krátkých vět** — u bloků kratších než dvě vteřiny model
občas netrefí jazyk. Delší odstavce vycházejí spolehlivěji.

---

## English

A local text-to-speech server for **Binder**, the macOS audiobook binder.
It narrates books on your own Mac — nothing is uploaded, no account, no API
key, no per-minute charge.

It installs separately because Binder ships through the App Store, and App
Store apps may not download or execute external code (guideline 2.5.2).

**Requirements:** Apple Silicon Mac, 16 GB RAM (24 GB+ recommended), 15 GB free
disk, macOS 14+.

**Install** — paste into Terminal:

```sh
git clone https://github.com/PavelStancik/wristtales-voice-server.git ~/wristtales-voice-server && ~/wristtales-voice-server/install.sh
```

**Run:**

```sh
~/wristtales-voice-server/voice-server.sh
```

It listens on `http://127.0.0.1:8000` and speaks the OpenAI-compatible
`POST /v1/audio/speech` API, so it also works with anything else that targets
that endpoint.

**Expect it to be slower than real time** — roughly 1.3× on an M2 Pro, so a
10-hour book takes about 15 hours to narrate. Run it overnight with
`--keep-awake`.

**Voices:** Higgs is a *cloning* model. Without a reference recording it picks
a random speaker on every single request. Ready-made synthetic references are
in `voices/`; add your own as a 20–30 second 24 kHz mono WAV.

Licence: MIT for the code and the bundled voices. The model weights
(`bosonai/higgs-audio-v3-tts-4b`) carry Boson AI's own terms.
