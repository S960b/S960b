# find me if you can — GlacierCTF 2026

[Русская версия](README.ru.md) · [Full write-up](solve.md)

**Category:** OSINT · **Points:** 50 · **Status:** Solved — accepted flag confirmed by participant.

**Challenge:** Given the photograph `2024.JPG` and route **GVA → VIE**, identify the marketed flight, ATC callsign and geographic coordinates.

## Accepted answer

```text
gctf{OS572_AUA57M_47.75_12.35}
```

The first proposed submission, `gctf{OS572_AUA57M_47.75_12.36}`, **was not accepted**. The **second** proposal, with longitude `12.35`, **was accepted**. The key distinction is the second decimal place of longitude; it is not a change of flight or callsign.

**Flight:** Austrian Airlines OS572 · **ATC callsign:** AUA57M · **Flag coordinates:** 47.75° N, 12.35° E.

The earlier OSINT investigation discussed an Austrian Embraer E195, 2024 GVA–VIE schedules and an Alpine location near Wilder Kaiser / Aschau im Chiemgau. Those are contextual findings; the submitted flag and success report, rather than an independently rechecked ADS-B track, ground the accepted answer.

See [solve.md](solve.md) for the historical research, discarded alternatives and the two submitted candidates.
