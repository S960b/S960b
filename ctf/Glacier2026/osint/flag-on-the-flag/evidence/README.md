# Evidence notes

The raw GlacierCTF challenge image was not retained in the material used to prepare this repository.

What *is* retained from the competition conversation:

- challenge: **Flag on the flag**, OSINT/easy, 50 points, author LosFuzzys;
- required format: `LosFuzzys{.*}`;
- first decoded candidate ended with `y0lO` and was rejected;
- reviewing another view of the banner revealed a final byte hidden by a fabric fold;
- `0x63 XOR 0x42 = 0x21`, adding `!`;
- the corrected complete flag was confirmed accepted by the user during the competition.

Public corroboration:

- LosFuzzys, *Introduction to Capture the Flag Events*, Winter Semester 2025:
  https://losfuzzys.net/resources/trainings/2025/intro_to_ctfs.pdf
- The team-presentation page contains a group photo holding the same purple LosFuzzys banner. A long hexadecimal-looking string is visible along the lower edge.

The accepted flag itself is not repeated in this evidence file.
