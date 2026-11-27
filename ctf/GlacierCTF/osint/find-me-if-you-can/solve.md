# find me if you can — identifying the accepted GVA–VIE flight and coordinates

[Русская версия](solve.ru.md)

## Challenge

The organizer supplied `2024.JPG` and the GVA → VIE route. The required format was:

```text
gctf{Marketed flight/Flightnumber_ATC callsign_LAT.xx_LONG.xx}
```

The task was to connect the aircraft photo to an actual 2024 flight and express the marketed flight, its radio callsign and the approximate location in the prescribed two-decimal format.

## Aircraft and route investigation

The historical investigation identified an **Austrian Airlines Embraer E195**, with no useful EXIF/GPS coordinates. It compared 2024 Geneva–Vienna schedules and photographs of Alpine terrain. Contenders included Austrian flight numbers **OS572, OS574, OS576**, possible codeshare listings **LX3562, LX3564, LX3566**, and different ATC callsign possibilities.

An early OS15x hypothesis (**OS154/OS152**) was discarded because those numbers pertain to later scheduling changes in 2025, rather than a 2024 photo. One aircraft-tracking page was found to display a flight table for dates/routes different from its title, and was also discarded.

The later investigation narrowed the location to the **Wilder Kaiser / Aschau im Chiemgau** area and selected **OS572** with ATC callsign **AUA57M**. A historical location estimate in the conversation was approximately **47.7504° N, 12.3564° E**. This location was a research estimate, not a newly verified track in the current publication.

## Two submissions: the decisive correction

The two proposed strings differed only in longitude:

| Attempt | Candidate | Platform result |
| --- | --- | --- |
| First | `gctf{OS572_AUA57M_47.75_12.36}` | **Rejected** |
| Second | `gctf{OS572_AUA57M_47.75_12.35}` | **Accepted** |

**Accepted flag:**

```text
gctf{OS572_AUA57M_47.75_12.35}
```

The first candidate used `12.36` as a rounded value from approximately `12.3564`. The second used `12.35`, the two-decimal truncation of the same estimate. The participant explicitly confirms that **the second proposed flag was accepted**; this should not be reversed or described as two successful alternatives.

The flag's latitude and longitude are **the accepted submission values**, not a claim to centimetre-accurate physical coordinates.

## Reliability and provenance

The submitted flag and which attempt succeeded are grounded in the participant's follow-up identifying the **second** string. Historical assumptions about aircraft type, terrain and a possible flight date arise from the prior research discussion; we do **not** claim to have independently replayed ADS-B records or the platform grader while updating this write-up.

The original high-resolution photograph and the full historical tracking log are not embedded in this public repository. Those omissions limit independent reconstruction of every OSINT lookup, but **do not make the accepted flag unknown**.
