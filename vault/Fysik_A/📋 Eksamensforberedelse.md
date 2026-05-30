# 📋 Eksamensforberedelse — Fysik A

> [[_Index|← Fysik A]] · [[../Home|🏠 Hjem]]
> Sæt `status` i hvert emnes frontmatter: `not-started` → `in-progress` → `ready`

---

## 🎯 Emnestatus

| Emne | Status |
|------|--------|
| [[Kort introduktion til faget\|Kort introduktion til faget]] | ⬜ Ikke startet |
| [[Fagets materialesamling\|Fagets materialesamling]] | ⬜ Ikke startet |
| [[Energi\|Energi]] | ⬜ Ikke startet |
| [[Bølger (Lyd og Lys)\|Bølger (Lyd og Lys)]] | ⬜ Ikke startet |
| [[Atomfysik\|Atomfysik]] | ⬜ Ikke startet |
| [[Radioaktivitet - valgemne\|Radioaktivitet - valgemne]] | ⬜ Ikke startet |
| [[El-lære\|El-lære]] | ⬜ Ikke startet |
| [[Strømkilder og SO4\|Strømkilder og SO4]] | ⬜ Ikke startet |
| [[Kinematik\|Kinematik]] | ⬜ Ikke startet |
| [[Evlauering\|Evlauering]] | ⬜ Ikke startet |
| [[Dynamik (kræfter)\|Dynamik (kræfter)]] | ⬜ Ikke startet |
| [[Arbejde og mekanisk energi\|Arbejde og mekanisk energi]] | ⬜ Ikke startet |
| [[Eksamenstræning\|Eksamenstræning]] | ⬜ Ikke startet |
| [[Det skrå kast\|Det skrå kast]] | ⬜ Ikke startet |

> 💡 Har du **Dataview**-pluginet? Se automatisk opdateret tabel nederst.

---

## 📝 Afleveringer & opgaver

*Sæt kryds efterhånden som du gennemgår dem.*

- [ ] [[Journal Specifik varmekapacitet|Journal Specifik varmekapacitet]] — _Energi_
- [ ] [[Nyttevirkning - Journal|Nyttevirkning - Journal]] — _Energi_
- [ ] [[Aflevering af opsamlingsspørgsmål|Aflevering af opsamlingsspørgsmål]] — _Atomfysik_
- [ ] [[Aflevering_radioaktivitet|Aflevering_radioaktivitet]] — _Radioaktivitet - valgemne_
- [ ] [[Fysik-journal - (U,I)-karakteristikker|Fysik-journal - (U,I)-karakteristikker]] — _El-lære_
- [ ] [[Tirsdag d. 23. september 2025|Tirsdag d. 23. september 2025]] — _El-lære_
- [ ] [[Forsøgs logbog resistivitet|Forsøgs logbog resistivitet]] — _El-lære_
- [ ] [[7 - Mandag 15 12-25|7 - Mandag 15/12-25]] — _Kinematik_
- [ ] [[4 - Onsdag 10 12-25|4 - Onsdag 10/12-25]] — _Kinematik_
- [ ] [[3 - Fredag 05 12-25|3 - Fredag 05/12-25]] — _Kinematik_
- [ ] [[Journal Fjederkonstant|Journal Fjederkonstant]] — _Dynamik (kræfter)_
- [ ] [[Journal - Friktionskoefficient|Journal - Friktionskoefficient]] — _Dynamik (kræfter)_
- [ ] [[FRIVILIG afleveringsmappe - så man er sikker på man har gemt sit dokument|FRIVILIG afleveringsmappe - så man er sikker på man har gemt sit dokument]] — _Arbejde og mekanisk energi_
- [ ] [[Aflevering - eksamenssæt|Aflevering - eksamenssæt]] — _Eksamenstræning_

---

## 📄 Formelsamlinger & referencer

- [[formelsamlig_orbitB.pdf|formelsamlig_orbitB]]

---

## 🃏 Flashcards

*Kør `python sde_downloader.py --subject "Fysik A" --flashcards` for at generere AI-flashcards per emne.*

---

## 🔧 Dataview — automatisk emnestatus

> Kræver [Dataview-pluginet](https://github.com/blacksmithgu/obsidian-dataview).

```dataview
TABLE status AS "Status", topic AS "Emne"
FROM "Fysik_A"
WHERE contains(tags, "fysik")
SORT topic ASC
```