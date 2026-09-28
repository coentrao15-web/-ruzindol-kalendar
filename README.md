# OŠK Ružindol – automatický kalendár pre iPhone

Tento projekt načíta verejné rozpisy všetkých aktuálnych družstiev z Futbalnetu,
vytvorí `ruzindol.ics` a zverejní ho priamo v tomto repozitári. Kontrola prebieha štyrikrát
denne. iPhone si odoberaný kalendár obnovuje vlastným tempom, preto zmeny nemusia
byť viditeľné okamžite.

Názov udalosti má poradie domáci – hostia a v zátvorke družstvo (Dospelí, U17,
U15, U09). Začiatok je čas Futbalnetu; koniec je **odhad vrátane prestávky**:
Dospelí 105 min, U17 95 min, U15 85 min, U09 65 min. Pri pohárovom predĺžení či
zdržaní sa skutočný koniec môže líšiť. „Nez. družstvo“ sa vynechá.

## Verejná adresa kalendára

Odoberaná adresa:
`https://raw.githubusercontent.com/coentrao15-web/-ruzindol-kalendar/main/public/ruzindol.ics`

Posledná úspešná aktualizácia:
`https://raw.githubusercontent.com/coentrao15-web/-ruzindol-kalendar/main/public/status.json`

Prvé spustenie prebehne automaticky po nahratí programu. Ďalšie aktualizácie
bežia štyrikrát denne. Kalendár nevyžaduje nastavenie GitHub Pages.

## Pridanie do iPhonu

V iPhone otvor **Kalendár → Kalendáre → Pridať kalendár → Pridať odoberaný
kalendár** a vlož HTTPS adresu `ruzindol.ics` uvedenú vyššie. Potvrď **Nájsť**, pri
účte vyber **iCloud**, pomenuj kalendár a potvrď **Hotovo**. Ak túto voľbu
v Kalendári nevidíš, použi
**Nastavenia → Apky → Kalendár → Kalendárové účty → Pridať účet → Iný →
Pridať odoberaný kalendár**. V oboch prípadoch zadaj *adresu*, súbor
neimportuj jednorazovo. V aplikácii Kalendár skontroluj, že je nový kalendár
zaškrtnutý.

## Ako fungujú zmeny

Zápas má trvalé ID z Futbalnetu. Pri zmene začiatku ostane v odoberanom
kalendári tá istá udalosť. Zrušený zápas sa po ďalšom obnovení feedu odstráni.
Ak Futbalnet nie je dostupný alebo zmení formát, nový feed sa nenasadí; zostane
posledný úspešný. Pravidelne sa dá skontrolovať `status.json`.

GitHub plánovaný beh môže meškať. Každá úspešná aktualizácia vytvorí nový commit,
ktorý zároveň udržuje repozitár aktívny. Projekt vyžaduje verejný repozitár
a dostupnosť GitHub Actions, verejných súborov GitHubu a Sportnetu.

Zdroj dát: [OŠK Ružindol na Futbalnete](https://sportnet.sme.sk/futbalnet/k/osk-ruzindol/).
