# Referenční hlasy

Higgs je klonovací model: hlas není nastavení, ale **vstup**. Ke každému
požadavku se posílá krátká nahrávka (`ref_audio`) a její přepis (`ref_text`),
a model tenhle hlas napodobí.

**Bez reference si model losuje mluvčího náhodně, a to u každého požadavku
zvlášť.** Jedna kapitola pak může mít několik různých vypravěčů. Binder proto
referenci posílá vždy — pole `voice` (třeba `"af_heart"`) je z jiného modelu
a Higgs ho mlčky ignoruje.

## Hotové hlasy

Všechny jsou **syntetické** — vygeneroval je tenhle model. Nejsou to nahrávky
žádného skutečného člověka.

| soubor | popis |
|---|---|
| `vypravec-2-expresivni.wav` | **doporučený.** Mužský, klidný, ověřený na dlouhé próze. |
| `vypravec-1-expresivni.wav` | mužský, záloha |
| `vypravec-3-rychly.wav` | mužský, svižnější tempo |
| `vypravec-3.wav` | tentýž hlas v běžném tempu |
| `vypravecka-1-expresivni.wav` | ženský |
| `vypravecka-2-expresivni.wav` | ženský |

Soubory `.mp3` vedle nich jsou jen na poslech — jako reference se posílá `.wav`.

### Přepis, který k nim patří

Ke všem `*-expresivni` variantám se posílá tenhle `ref_text`:

> To je ale nesmysl! Haha, tomu přece nemůžeš věřit. Ale dobře, poslouchám dál,
> protože mě to upřímně baví.

Přepis musí odpovídat nahrávce. Když nesedí, kvalita znatelně spadne.

## Vlastní hlas

Potřebuješ **20 až 30 vteřin** nahrávky:

- 24 kHz mono WAV
- klidné plynulé čtení, žádná hudba ani šum
- **bez mluvených popisků** typu „Smích." nebo „Radostně." — model si je
  naklonuje jako součást projevu a bude je pak číst nahlas
- ideálně ta samá věta, kterou pak uvedeš jako `ref_text`

Ulož do téhle složky a vyber v Binderu.

Převod existující nahrávky do správného formátu:

```sh
ffmpeg -i moje-nahravka.m4a -ar 24000 -ac 1 voices/muj-hlas.wav
```

## Proč jsou reference „expresivní"

Tohle stojí za vysvětlení, protože to není intuitivní.

Klidná, neutrální reference zní na první poslech nejlíp — ale **dusí emoce**.
Model pak čte i vypjaté scény jednotvárně a řídicí tagy (`<|emotion:...|>`)
skoro nefungují.

Reference, která sama nese expresivní otisk, si barvu hlasu udrží a přitom
emoce propustí. Vznikly proto dvoustupňově: klidný hlas vygeneroval jednu
expresivní větu, a **ta** se pak používá jako reference.

Ještě jedna zvláštnost: expresivní referenci **nelze vyrobit bez reference**.
Model tíhne k ženskému hlasu (v testu 6 kandidátů z 6). Mužskou expresivní
referenci je nutné bootstrapovat z mužské.

## Teplota

Ověřená hodnota je `temperature: 0.9`. Při ní byl doporučený hlas schválen.

Má to ale háček: při stejném nastavení vyšel jeden render „velmi dobře" a jiný
špatně. Expresivita a rozptyl jsou nejspíš tentýž knoflík. Kdyby kapitola
obsahovala moc špatných bloků, první věc ke zkoušení je `0.7` — je klidnější
a předvídatelnější.
