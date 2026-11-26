# find me if you can — historical flight and photo geolocation research

[Русская версия](solve.ru.md)

## Challenge goal

Using organizer image `2024.JPG` and the route **GVA → VIE**, identify the marketed flight and flight number, actual ATC callsign and the approximate airplane position. The flag uses `gctf{Marketed flight/Flightnumber_ATC callsign_LAT.xx_LONG.xx}`, so mixing a codeshare marketing label with a live radio callsign will make a plausible-looking answer fail.

## Previous research and correction

The initial exploration established an **Austrian Airlines Embraer E195** in the photograph. EXIF and GPS metadata were absent, so the flight and coordinate identification had to use historical schedules, route tracking and ground landmarks rather than a metadata shortcut.

The 2024 route had flight-number candidates **OS572**, **OS574**, **OS576**; potential Swiss codeshare marketing references included **LX3562**, **LX3564**, **LX3566**. Their ATC callsigns must be independently correlated with the specific dated flight and the airplane's position: a marketed number does not necessarily determine a callsign.

An early proposed approach incorrectly used **OS154** / **OS152**, numbers relevant to later changes around October 2025 rather than the photographed **2024** flight. One linked aircraft-history page was also found to display different routes/dates than its apparent title. These were discarded, demonstrating the importance of checking the actual displayed flight tables.

A later hypothesis examined an Alpine view around **Wilder Kaiser / Achensee** and the OS572 route. A proposed but **not independently verified** trial candidate was `OS572/AUA57M` near **47.75°N 12.36°E**; another earlier candidate was AUA57C. The prior ChatGPT discussion explicitly had **no platform-confirmed match** for either of those candidate strings. The participant's later solved-task list establishes successful submission, but does not supply the final coordinates and callsign.

## How to complete reproducibly from original evidence

To finish an exact historical write-up, correlate the organizer's original high-resolution 2024.JPG (terrain, camera orientation, wing/aircraft features) with time-matched 2024 ADS-B flight tracks GVA→VIE, record the observed flight identity, separate the marketing flight number from the ATC callsign, and verify the plane's coordinates at the photographed moment. Only report a literal `gctf{...}` string when the accepted submitted value is retrieved; none of the partial guesses above are presented as the actual accepted flag.

**Provenance:** this research was recovered from earlier user/assistant conversations; the original full-resolution photograph, accepted flag and final track log were not available in the current working attachments. No fresh flight-map identity was invented for publication.
