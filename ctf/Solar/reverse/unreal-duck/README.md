# Unreal Duck (reverse)

An Unreal Engine game challenge. Given: a Windows client build, a folder of content containers
and a launcher. The flag is handed out by the game server after certain actions in the game.

What the build contains:

- `UnrealDuckClient.exe` is about 167 KB, a launcher that only starts the main process.
- `UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe` is about 143 MB, this is the
  main game code.
- `UnrealDuck/Content/Paks/` with the containers `global.utoc`, `global.ucas`,
  `UnrealDuck-WindowsClient.utoc`, `UnrealDuck-WindowsClient.ucas` and
  `UnrealDuck-WindowsClient.pak`. The IoStore index starts with a sixteen-byte text banner,
  followed by a normal version 8 header.

Key facts:

- The content is packed into IoStore with Oodle compression: 2126 chunks, 504 packages, a pak
  with 1662 files.
- Packages have `legacy: -9` and `uses_unversioned_properties: True`, so this is UE5 and
  property names are not stored in the files.
- The game logic is in native code: the executable strings include the RPC functions
  `ServerPushCheatLetter`, `ServerRecordFrame` and `ClientReceivePayload`.
- The player controller keeps the code letters array at offset `0x808`, the counter at `0x810`,
  the unlock byte at `0x800`; a zone keeps the route number at offset `0x2c0`.
- Ready code strings are not present in the content, they are checked by the server.

Files:

- `scripts/duck_extract.sh` - unpacks IoStore and pak
- `scripts/duck_map_layout.py` - parses the structure of a classic Unreal package
- `scripts/duck_watch_all.py` - Frida hooks on the RPC functions
- `scripts/duck_capture_payload2.py` - intercepts and saves the payload with the flag
- `scripts/duck_auto_guide.py` - automated playthrough
- `assets/` - container path list, project asset list and the IoStore indexer

Heavy artifacts (the build itself and the unpacked content) are not copied into the repository.

The full solving path is in `solve.md`.
