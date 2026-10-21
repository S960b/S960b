# Unreal Duck (reverse)

An Unreal Engine 5.7 game challenge. Given: a Windows client build, a folder of content
containers, a launcher and a live multiplayer server. There are no project sources. The flag
arrives as a reward after a correct playthrough of the in-game routes.

What the build contains:

- `UnrealDuckClient.exe` 167 424 bytes, a launcher that only starts the main process.
- `UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe` 143 861 760 bytes, this is the
  main game code. Its SHA-256 is
  `d0aff10fbd7e980dc9edcaa8cc95d232a37b11c7eb977ad9e66784cec532b802`, and every address in the
  writeup belongs to exactly this build.
- `UnrealDuck/Content/Paks/` with the containers `global.utoc`, `global.ucas`,
  `UnrealDuck-WindowsClient.utoc`, `UnrealDuck-WindowsClient.ucas` and
  `UnrealDuck-WindowsClient.pak`. The IoStore index starts with a sixteen-byte text banner,
  followed by a normal version 8 header.

Key facts:

- The content is packed into IoStore with Oodle compression: 2126 chunks, 504 packages. The pak has
  1662 entries but no game assets: only engine configs, a shader cache and `AssetRegistry.bin`.
- Packages have `legacy: -9` and `uses_unversioned_properties: True`, so this is UE5 and property
  names are not stored in the files.
- The build has one game map, the training ground `Lvl_ThirdPerson`. The second map file,
  `Lvl_MainMenu`, is the menu screen, and a menu is not a level.
- The logic is in native code. The executable strings include the RPC functions
  `ServerPushCheatLetter`, `ServerRecordFrame`, `ClientReceivePayload` and a few more. Server RPCs
  are empty in the client build, the check runs in a server build that is not in the archive, but
  the client computes the route check value itself.
- Player controller state: `PC+0x7B0` packed characters, `PC+0x7C0` processed route counter,
  `PC+0x7C8` current route number, `PC+0x7E0` and `PC+0x804` intermediate states, `PC+0x808` the
  letters array, `PC+0x810` its length, `PC+0x800` the input lock.
- The check is local: three profile records of 28 bytes each plus a function over them. A `26^5`
  search gives the only solutions `ILOVE`, `DUCKS`, `ALOTT`, that is `I LOVE DUCKS A LOT`.
- The codes are typed with genuine keyboard events, Frida only monitors the state. The reward
  arrives as an image through `ClientReceivePayload`.

Files:

- `scripts/duck_extract.sh` - unpacks IoStore and pak
- `scripts/unrealduck_utoc_index.py` - reads the IoStore index and the path list
- `scripts/duck_map_layout.py` - parses the structure of a classic Unreal package
- `scripts/duck_scan.py` - searches for interesting strings in the content and the executable
- `scripts/duck_smc_decode.py` - decodes level block transforms
- `scripts/duck_umap_probe.py`, `scripts/duck_umap_fixmap.py` - `.umap` structure research
- `scripts/duck_frida_guarded.py`, `scripts/duck_frida_guarded.js` - guarded hook of the reward
  receiver
- `scripts/duck_capture_payload.py` - simpler payload interceptor
- `scripts/duck_solve_corrected.cpp` - local code search
- `scripts/duck_auto_guide.py` - automatic code input with genuine keyboard events
- `scripts/duck_dump_profile.py` - dumps the route profile from memory and checks the constants
- `assets/` - container path list, project asset list, search output

Heavy artifacts (the build itself and the unpacked content) are not copied into the repository.

The full solving path is in `solve.md`.
