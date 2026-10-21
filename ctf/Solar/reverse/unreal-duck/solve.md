# Unreal Duck: the path to the solution

## 0. The whole plan

Why this section: so the reader sees the chain down to the details and understands why every next
step is needed.

Given: a Windows client of a game on Unreal Engine 5.7, a folder of content containers, a launcher
and a live multiplayer server. There are no project sources. The game hides logic that hands out a
reward after certain actions.

Goal: understand what has to be done in the game and get the flag.

The plan was this:

1. Record the build composition and file hashes, so every later address refers to a known version.
2. Understand what reads the content containers, and unpack them.
3. Look at the assets and check whether the logic is there.
4. Find the game logic in the executable: network function names and their addresses.
5. Attach to the running client and see which fields change during play.
6. Reconstruct the code check math and verify it against values taken from the live game.
7. Brute-force the code locally.
8. Enter the found codes the normal way, wait for the reward and read the flag.

## 1. What is given

Why this step: to pin down the input files and their checksums, because every address and offset
below belongs to exactly this build.

The archive unpacks with plain `unzip`, then I look at file types and the hash of the main one:

```
$ unzip -l unreal_duck.zip
$ unzip -q unreal_duck.zip -d unreal_duck
$ file unreal_duck/UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe
... PE32+ executable (GUI) x86-64, for MS Windows
$ sha256sum unreal_duck/UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe
d0aff10fbd7e980dc9edcaa8cc95d232a37b11c7eb977ad9e66784cec532b802
```

Build composition:

| Object | Size, bytes | Purpose |
| --- | ---: | --- |
| `UnrealDuckClient.exe` | 167 424 | launcher, also PE64 |
| `Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe` | 143 861 760 | main game code, PE64 x86-64 |
| `Content/Paks/UnrealDuck-WindowsClient.ucas` | 289 910 624 | IoStore data |
| `Content/Paks/UnrealDuck-WindowsClient.utoc` | 217 399 | IoStore index |
| `Content/Paks/UnrealDuck-WindowsClient.pak` | 11 017 197 | separate pak |
| `Content/Paks/global.utoc` | 722 | engine index |
| `Content/Paks/global.ucas` | 2 870 272 | engine data |

The hash helped later: the first attempt to place a hook ended with `access violation`, and
checking the module name, its base and the function bytes removed the uncertainty (section 6).

## 2. Containers: the IoStore index and the pak

Why this step: to learn how many packages are inside, which compression they use, and whether the
pak holds anything, because that decides what to unpack and with which tool.

The file sizes immediately hint at which is which: an index of 217 KB next to 290 MB of data looks
like a table of contents plus content. That is a guess though, so I googled the extensions `.utoc`
and `.ucas`. The search leads to the Epic documentation on IoStore: it is an Unreal container
where `utoc` holds the index (a table of chunks and a list of packages) and `ucas` holds the data,
and the two files only work as a pair. The `global` pair is shared engine data, the
`UnrealDuck-WindowsClient` pair is the game content. A `.pak` file is the older Unreal container,
it sits next to them, and it has to be checked too so that no part of the content is missed.
Ready-made tools exist for both formats: `retoc` for IoStore and `repak` for pak.

I start with the index because it is the table of contents of the container: without it there is no
way to list assets or learn the compression. First I tried to read the index with my own script,
following a description of the IoStore header layout. The script failed on its very first read: at
the expected offsets there were meaningless values. To see what was wrong I dumped the first bytes
of the file:

```
$ head -c 32 UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.utoc | xxd
00000000: 2d3d 3d2d 2d3d 3d2d 2d3d 3d2d 2d3d 3d2d
00000010: 0800 0000 9000 0000 0100 0000 2c00 0000
```

The first sixteen bytes are the text string `-==--==--==--==-`. A container header cannot start
with readable text, so this is not part of it, but a banner someone prepended to the file. The way
to check this is to shift: if I read not from zero but from byte sixteen, then version `08` and
header size `90 00 00 00`, that is 144 bytes, land exactly where they belong, and every following
field agrees. At that offset my parser reads the whole index and returns the package list. It also
shows the limitation: a parser that looks for the header strictly at offset zero will not work on
this file. `retoc` reads it fine because it skips the unexpected leading bytes itself.

Then, instead of parsing the index by hand, I hand it to `retoc`. First the summary:

```
$ retoc info UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.utoc
  container_flags: EIoContainerFlags(Compressed | Indexed)
  version: ReplaceIoChunkHashWithIoHash
  mount_point: ../../../
  chunks: 2126
  packages: 504
  compression_methods: [Oodle]
```

The summary answers two questions at once. The package count of 504 means this is not a monolith
but 504 separate assets, so the search will go by name rather than by offset in a file. The
`Oodle` compression means unpacking needs a tool that supports this codec: plain `unzip` gets
nothing here.

To see what those 504 packages consist of, I read the index line by line:

```
$ retoc list UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.utoc | head -3
UnrealDuck-WindowsClient 7aba7a84b764bf1300000001 ExportBundleData
UnrealDuck-WindowsClient 8efe6173efa8192200000001 ExportBundleData
UnrealDuck-WindowsClient 6ff734fd30628e8c00000002 BulkData
```

What this means. A chunk is a storage unit inside IoStore, with an identifier and a type. Type
`ExportBundleData` is asset data, `BulkData` is large nested data such as textures, `ShaderCode`
is shaders. A package is the logical unit corresponding to one asset. The listing was needed to
show that asset names are not in the table of contents: only chunk identifiers are visible here,
and the names have to come from the packages themselves after unpacking.

Now the pak. The number of entries says nothing on its own, so I look not at how many files are
inside but at which ones:

```
$ repak list UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.pak | cut -d/ -f1 | sort | uniq -c
   1652 Engine
     10 UnrealDuck

$ repak list UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.pak | grep '^UnrealDuck/'
UnrealDuck/AssetRegistry.bin
UnrealDuck/Config/DefaultEngine.ini
UnrealDuck/Config/DefaultGame.ini
UnrealDuck/Config/DefaultInput.ini
UnrealDuck/Content/ShaderTypeInfo-Global-PCD3D_SM5-PCD3D_SM5.stinfo
UnrealDuck/Content/ShaderTypeInfo-Global-PCD3D_SM6-PCD3D_SM6.stinfo
UnrealDuck/Content/ShaderTypeInfo-UnrealDuck-PCD3D_SM5-PCD3D_SM5.stinfo
UnrealDuck/Content/ShaderTypeInfo-UnrealDuck-PCD3D_SM6-PCD3D_SM6.stinfo
UnrealDuck/Plugins/UnrealDuck.upluginmanifest
UnrealDuck/UnrealDuck.uproject
```

The conclusion: the pak holds engine configs, a shader cache and `AssetRegistry.bin` (a registry
listing asset paths), but no game assets at all. So the thing to unpack is IoStore, and the pak is
only good for taking the configs and the registry.

A separate find in the config: `DefaultEngine.ini` contains `CoreRedirects`, a rename table where
some property names are replaced with Greek trigrams. For `DuckRouteRecord`, for instance, the
original names look like `N4 I2 E0 P7 X1 K5 D3 V6`. Sorted by the numeric suffix they become
`E0 X1 I2 D3 N4 K5 V6 P7`, that is `EXIDNKVP`. I tried A1Z26, ROT and index sums, but none gave a
provable result, so I stopped that line (section 13).

## 3. Unpacking and what the assets give

Why this step: while the content is in IoStore it cannot be read as ordinary files, and the assets
have to yield object names and level structure.

The order is this. First `retoc to-legacy` converts IoStore back into classic `.uasset` and
`.umap` plus `.uexp`, the form Unreal used before Zen. Then `repak unpack` unpacks the pak. All of
it is wrapped in `scripts/duck_extract.sh`:

```
$ ./scripts/duck_extract.sh UnrealDuck/Content/Paks out
[retoc] UnrealDuck-WindowsClient
[repak] UnrealDuck-WindowsClient
```

After unpacking, the index gives 635 files in the container tree. The script prints them the way
they are stored inside, together with the chunk number:

```
1712 ../../../UnrealDuck/Content/ThirdPerson/Lvl_ThirdPerson.umap
  95 ../../../UnrealDuck/Content/Menu/Lvl_MainMenu.umap
1629 ../../../UnrealDuck/Content/ThirdPerson/Blueprints/BP_ThirdPersonPlayerController.uasset
1827 ../../../UnrealDuck/Content/ThirdPerson/Blueprints/BP_ThirdPersonGameMode.uasset
1649 ../../../UnrealDuck/Content/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.uasset
```

In Unreal editor terms the project root `UnrealDuck/Content` corresponds to `/Game`, so below I
call these objects shortly, for example `/Game/ThirdPerson/Lvl_ThirdPerson`. The full list is in
`assets/unrealduck_paths.txt`, the project-only list in `assets/unrealduck_project_assets.txt`.

There are two maps in the build: the main menu and the training ground. There is no separate map
for a secret level. The executable strings confirm it: they describe the single public level.

```
Current public level. Additional routes may appear later
without exposing their map paths in the menu.
```

Next to it I found the level list initialisation at `VA 0x144380A20`: it creates one entry
`training_ground`, sets `bVisibleInMenu=1` and points to `/Game/ThirdPerson/Lvl_ThirdPerson`. Hence
an important negative result: a menu system with a visibility field does not mean a second
unlockable level exists. I did not develop the hidden map hypothesis any further.

The unpacked files can already be opened, but this is a binary format, so I googled its
description. A classic Unreal package starts with `FPackageFileSummary`, which holds the name
table `NameMap` and the export map `ExportMap`. Following that description I wrote the parser in
`scripts/duck_map_layout.py`. The script reads the magic `0x9e2a83c1`, checks the package version,
extracts names and exports, and writes `layout.json` and `names.txt`:

```
$ python3 scripts/duck_map_layout.py out/legacy_assets/.../Lvl_ThirdPerson.umap --out dkl
$ head -5 dkl/names.txt
```

The summary shows `legacy: -9`, `cooked: True` and `uses_unversioned_properties: True`. A negative
legacy version means UE5, and `unversioned` means property names are not written into the file, so
they cannot be read as plain text without class descriptions.

The content tree keeps producing files with the `BP_` prefix, and they deserve a stop. A blueprint
is the way to write game logic in Unreal without C++: you build a graph of nodes in the editor, and
the result is stored as an ordinary asset with the `BP_` prefix. I learned this when I googled what
the files like `BP_ThirdPersonPlayerController.uasset` in `Content/ThirdPerson/Blueprints` are, and
why there is no program text inside such a file.

The blueprints look the same as the map: `BP_ThirdPersonPlayerController` has
`UnrealDuckPlayerController` in its name table, and `BP_ThirdPersonGameMode` has
`UnrealDuckGameMode`. So a blueprint is a thin wrapper over a native C++ class, and the logic lives
in native code.

A side check: can the answer be extracted from the level geometry. In `Lvl_ThirdPerson.umap` and
`.uexp` I recovered the export list and about 55 `StaticMeshComponent` components, 53 of them game
blocks: roughly 20 cubes, 12 ramps, 9 cylinders and 12 quarter cylinders. Coordinates are in the
`XY ±2000` range, heights mostly `0`, `-100` and `200`, rotations multiples of 90 degrees. To read
the serialised transforms I matched four component layouts, judging them by whether the values made
sense:

| Component size, bytes | RelativeLocation | RelativeRotation | RelativeScale |
| ---: | ---: | ---: | ---: |
| 113 | +21 | +45 | +69 |
| 102 | +10 | +34 | +58 |
| 91 | +23 | default | +47 |
| 80 | +12 | default | +36 |

That confirmed the arena is read correctly, but there is no plain flag text in the arrangement of
the blocks. The scripts for it stayed in `scripts/duck_smc_decode.py`,
`scripts/duck_umap_probe.py` and `scripts/duck_umap_fixmap.py` as supporting material.

## 4. Network function strings and their addresses

Why this step: to find the code that sends data during play, one has to find function names first
and then turn the names into addresses.

A string search across the main executable gives the game controller function names:

```
$ strings -a UnrealDuckClient-Win64-Shipping.exe | grep -E 'Server|Client'
ServerPushCheatLetter
ServerRecordFrame
ServerApplyLocalProfile
ServerSendChat
ClientReceivePayload
ClientResetChallengeAfterDeath
```

In Unreal such functions are declared as `UFunction` and called through the reflection system and
`ProcessEvent`. That leads to an easy mistake: a function name in `.rdata` is not the address of
its handler. To get the code I looked for registration tables of the form "name pointer, `exec...`
wrapper pointer" and for the virtual table of the game `PlayerController`.

The resulting addresses for this build (virtual addresses, image base `0x140000000`):

| Purpose | VA |
| --- | ---: |
| `ServerPushCheatLetter`, `exec` wrapper | `0x144383940` |
| `ServerRecordFrame`, `exec` wrapper | `0x1443839C0` |
| `ServerApplyLocalProfile`, `exec` wrapper | `0x144383860` |
| `ClientReceivePayload`, `exec` wrapper | `0x144382C50` |
| client side send of `ServerPushCheatLetter` | `0x144381D30` |
| client side send of `ServerRecordFrame` | `0x144381D70` |
| client side send of `ServerSendChat` | `0x144381E30` |
| letter key handling | `0x14438F5F0` |
| route activation zone entry | `0x144391670` |
| route completion zone entry | `0x144391890` |
| route check value computation | `0x14438B8A0` |
| overall completion check | `0x144395F10` |
| `ClientReceivePayload` implementation | `0x144394600` |
| received message UI | `0x144393210` |
| menu level list creation | `0x144380A20` |

I verify the addresses with a disassembler so I am not working from random offsets:

```
$ objdump -d -M intel --start-address=0x14438B8A0 --stop-address=0x14438C000 UnrealDuckClient-Win64-Shipping.exe
$ objdump -d -M intel --start-address=0x144395F10 --stop-address=0x144396080 UnrealDuckClient-Win64-Shipping.exe
$ objdump -d -M intel --start-address=0x144394600 --stop-address=0x144394780 UnrealDuckClient-Win64-Shipping.exe
```

Then I tried the most direct route: the server functions exist, so reverse them and read the check.
That did not work, and here is why. In the virtual table starting at `VA 0x147118AA0` the slots look
like this: offset `+0xF98` holds `0x141128770`, `+0xFA0` holds `0x141128770` too, `+0xFB0` holds
`0x1443937F0`, and `+0xFC8` holds `0x14438B400`, which leads to `0x144394600`. The value
`0x141128770` is an empty handler of a single `ret`.

That is expected: server RPCs are empty in the client build, the check runs in a separate server
build that is not in the archive. But the client computes the route check value itself and shows
its own progress state, so the logic can be studied locally without touching the server.

## 5. UDP: why that direction did not help

Why this section: to explain why writing a custom network client was not worth it.

The server address is in the config, and it answers UDP `PING` with short binary packets starting
with `f0 81 01`. But I could not confidently match those replies to the game RPCs, and sending
ASCII strings does not replace the stock `NetDriver` anyway: that needs a handshake, actor channels
and `ProcessEvent`. On top of that, the ordinary Windows client already connected to the shared
arena, so checking events in a live client is simpler than reimplementing the protocol.

## 6. Frida: attaching and the first error

Why this step: the disassembler shows the functions exist, but not which controller fields they
read and in which order they are called. That is faster to see at runtime.

I looked for a way to intercept calls in someone else's process by reading what tools people
usually use for it. That is how I found Frida, which attaches to a running process and places
hooks by address. The hook has to go on `UnrealDuckClient-Win64-Shipping.exe`, not on the
launcher.

```
py -m pip install frida
py scripts/duck_frida_guarded.py --exe "C:\path\to\UnrealDuckClient-Win64-Shipping.exe" --out duck_payloads
```

The first version of the hook failed immediately with a memory read error:

```
access violation accessing 0x7fff6db44600
```

The cause was that the address was computed without the real module base. I added diagnostics:
`Process.enumerateModules()`, module lookup by exact name, a check that `RVA < module.size`,
`Process.findRangeByAddress`, an executable page check and a machine code signature comparison.
For the reward receiver it came out like this:

```
ClientReceivePayload_Impl VA = 0x144394600
RVA = 0x04394600
signature = 40 55 56 57 48 83 EC 40 F6 81 48 03 00 00 02 49 8B F0 48 8B EA 48 8B F9
```

After that Frida confirmed the module and the signature were right and the hook was in place. But
during a normal walk around the arena not a single `ClientReceivePayload` call arrived, so the
reward does not come for free.

## 7. Ordinary walking is recorded as letter input

Why this step: to understand what the game counts as code input, otherwise there is no way to
explain where the accumulated characters come from.

The log showed three different send functions, not one shared chat command:

| RPC | Client wrapper VA | Caller |
| --- | ---: | ---: |
| `ServerPushCheatLetter` | `0x144381D30` | `0x14438F77A` |
| `ServerRecordFrame` | `0x144381D70` | `0x14438BA6A` |
| `ServerSendChat` | `0x144381E30` | `0x144394F49` |

`ServerPushCheatLetter` takes a number from 0 to 25: key `A` gives 0, `B` gives 1 and so on. The
game listens to Latin keys not only while the chat is open, so the log was full of `0`, `3`, `18`,
`22`. That is ordinary `WASD` movement: `A` gives 0, `D` gives 3, `S` gives 18, `W` gives 22.

With an active route the same events went into the accumulated letters array. If you drive through
the start and finish zones without typing anything on purpose, the log shows:

```
RouteActivation routeId=0/1/2
RouteCompletion routeId=0/1/2
RouteRPC routeId=0 codesCount=0
```

A separate experiment showed that letters really are recorded:

```
codesCount 1 -> 2 -> 5
codes = [D, W, D, D, F]
locked = 1
RouteRPC routeId=0 codesCount=5
```

No reward reply came: the arbitrary set failed the check. But the important part became clear: the
set is limited to five letters, after the fifth input is locked, and ordinary walking interferes
because it mixes in its own characters.

## 8. Player controller state

Why this step: to get exact field offsets, so the state can be read and understood without
decoding every call by hand.

The first argument of these functions is the controller object itself, so the hooks show both the
values and the offsets they live at. For this build the picture is:

| Offset | Purpose |
| --- | --- |
| `PC+0x7A8` | pointer to `DuckRouteProfileAsset` |
| `PC+0x7B0` | packed state of the five characters (`packed`) |
| `PC+0x7C0` | processed route counter (`progress`) |
| `PC+0x7C8` | current `RouteId`, `255` means no active route |
| `PC+0x7E0` | internal mixed state (`mixed`) |
| `PC+0x7E4` | local full completion flag (`localComplete`) |
| `PC+0x7E8` | pointer to the array of computed 32-bit results |
| `PC+0x7F0` | number of elements in that array |
| `PC+0x800` | input lock after five characters (`locked`) |
| `PC+0x804` | rolling state |
| `PC+0x808` | pointer to the accumulated letters array |
| `PC+0x810` | accumulated letter count (`Codes.Num`) |

A route is turned on by a game trigger, its handler at `VA 0x144391670` sets `RouteId` and clears
the accumulation. The finish trigger at `VA 0x144391890` calls the computation at
`VA 0x14438B8A0` and sends `ServerRecordFrame(RouteId, Codes)`.

How the trigger gets the controller is visible in the same handlers. The third handler argument
(`r8` in the Win64 calling convention) is the pawn that crossed the zone, and the controller
pointer sits in that pawn at offset `0x2E8`. Both handlers use the very same instruction:

```
$ objdump -d -M intel --start-address=0x1443916b4 --stop-address=0x1443916c0 UnrealDuckClient-Win64-Shipping.exe
   1443916b4: 48 8b 9b e8 02 00 00   mov rbx,QWORD PTR [rbx+0x2e8]

$ objdump -d -M intel --start-address=0x1443918d4 --stop-address=0x1443918e0 UnrealDuckClient-Win64-Shipping.exe
   1443918d4: 48 8b 9b e8 02 00 00   mov rbx,QWORD PTR [rbx+0x2e8]
```

A class check follows immediately: a call to `0x14437bac0` and a comparison against the slot at
`+0x48`, which is the ordinary Unreal cast. So `0x2E8` is the pawn `Controller` field, and it is
not a guess: the offset is read in the code twice, on route activation and on route finish.

The full completion condition at `VA 0x144395F10` looks like this after reversing:

```cpp
if (progress == profile_record_count) {
    uint32_t bits = 0;
    for (uint32_t x : computed) bits |= x;
    if (bits == 0) localComplete = 1;
}
```

It is easy to fool yourself here: zeros in the cells of routes not yet processed do not mean
success. For example `computed=[0, 0xF2392DF9, 0]` with `progress=1` means one record was checked,
not two solved routes. I made exactly this mistake at first and decided one route was already done.

## 9. Route profile and the check math

Why this step: without the check function there is no way to understand the code or to search it.

The `DuckRouteProfileAsset` profile is created by native code, it does not lie in the content as a
separate asset under its own name. I looked for that place by the record constants: they are
written as immediate operands in the function starting at `VA 0x144380750`.

```
$ objdump -d -M intel --start-address=0x1443808ef --stop-address=0x144380910 UnrealDuckClient-Win64-Shipping.exe
   1443808ef: b9 1c 00 00 00        mov    ecx,0x1c
   1443808f4: c7 46 50 9a b0 6c 2f  mov    DWORD PTR [rsi+0x50],0x2f6cb09a
   1443808fb: 41 b8 03 00 00 00     mov    r8d,0x3
   144380901: c7 46 54 e2 ee d2 2f  mov    DWORD PTR [rsi+0x54],0x2fd2eee2
   144380908: c7 46 38 58 e9 72 29  mov    DWORD PTR [rsi+0x38],0x2972e958
```

That shows the profile layout. The record size `0x1c` is exactly those 28 bytes, the record count
is three, and offsets `+0x38`, `+0x50` and `+0x54` hold three profile constants. Some record values
come not from code but from a constant pool in `.rdata`: at `0x14711a990` lie `0x45d39389`,
`0xb2dcbf59`, `0x6f4b4eda` and `0x17d98198`, and at `0x14711aaf0` lie `0x39c19d54`, `0x158b4d73`,
`0x583a99d9` and `0xb01e4f4d`. The record array is a `TArray` at offset `+0x40`: the data pointer
first, then the count at `+0x48`.

Hence the working layout of one record: `+0x00` is `a`, `+0x04` the route number, `+0x08` is `b`,
`+0x0C` is `c`, `+0x10` is `d`, `+0x14` is `e`, `+0x18` is `f`. The route number inside a record is
one more check: it equals the record index, so records cannot be mixed up.

The profile can be dumped from the live game with `scripts/duck_dump_profile.py`. It hooks route
activation and the `ServerRecordFrame` sender, takes the controller from `Pawn+0x2E8`, reads the
profile pointer from `PC+0x7A8` and prints the header and every record:

```
$ py -m pip install frida
$ py scripts/duck_dump_profile.py --pid 1234 --out profile_dump.json
attached, module base 0x7ff6a1c40000
dump source=activation_pawn controller=0x... profile=0x... MATCH
```

`MATCH` means the dumped values equal the expected constants of this build, which are hardcoded in
the script for comparison. The mixer key in the default argument `0x2f6cb09a` equals
`profile+0x50`, one more confirmation that the reconstructed formula belongs to this profile.

The three records of 28 bytes (`0x1C`) each look like this:

| Record | `a` | `b` | `c` | `d` | `e` | `f` |
| ---: | --- | --- | --- | --- | --- | --- |
| 0 | `0x6f37aa0b` | `0x112bb232` | `0x29b1711a` | `0x45d39389` | `0xb2dcbf59` | `0x6f4b4eda` |
| 1 | `0x17d98198` | `0x42413596` | `0x73257244` | `0x57d52250` | `0x2523ab39` | `0x203585fc` |
| 2 | `0x2f48611f` | `0x39c19d54` | `0x158b4d73` | `0x583a99d9` | `0xb01e4f4d` | `0x2b74299a` |

The next question was what the computation does. The reconstructed model turns a character `c` into
a 6-bit value:

```cpp
uint32_t symbol(char c, uint32_t key = 0x2f6cb09a) {
    uint32_t v = (uint32_t(1 - int(c))*27u + (key ^ 0xffffffe5u)) & 63u;
    return (((v >> 3) ^ v) * 13 + 17) & 63u;
}
```

So 0..25 are only the `LetterCode` values that go into the RPC, while the mixer adds another step.
The five values accumulate into three states, all starting at zero:

```cpp
uint32_t R2 = ((R << 6) | v) & 0x3fffffffu;
uint32_t S2 = rotr(((v + 1) * 0x54e31f9bu) ^ S, 25)
            + 0x2c8b91efu;
uint32_t E2 = rotr((S2 ^ R2) + 0x7b642d11u, 23)
            ^ (R2 * 0x1f5d3b79u);
R = R2;
E = E2;
S = S2;
```

Those three states are exactly the `packed`, `mixed` and `rolling` values observed in Frida. The
computation for one route with a record `p = {a,b,c,d,e,f}`:

```cpp
uint32_t t = rotr(E + p.b, 21) ^ rotr(p.f + R, 27);
t += p.d ^ S;
t ^= p.a;
t = rotr(t, 25);
t += rotr(p.b, 29) ^ p.f;
t ^= rotr(R + 0x36d5e22bu + p.d, 19);
return t ^ p.e;
```

The arithmetic is 32-bit with wraparound modulo `2^32`, `rotr` is a cyclic shift right.

There was a mistake here that made the first brute force find nothing: two expressions need field
`p.d` (record offset `+0x10`) and I used `p.c` (`+0x0C`). So instead of trusting the disassembly, I
took a live control vector. With an active `RouteId=1` I typed five letters `JJJJJ` and Frida
showed:

```
routeId=1
Codes.Num=5
packed  = 0x3AEBAEBA
mixed   = 0x9E9C5582
rolling = 0x821D8DB8
computed[1] = 0xF2392DF9
```

The corrected local C++ implementation reproduced all four values exactly. That confirmed both the
key processing order and the `p.d` field. The set `JJJJJ` itself is not the code of the first
route.

## 10. Local brute force

Why this step: to get the codes that zero the check value without touching the server.

Once the function was reconstructed, the search became an arithmetic task: alphabet of 26 letters,
code length 5, that is `26^5 = 11 881 376` variants per route. That runs locally without effort:

```
$ g++ -O3 -std=c++17 scripts/duck_solve_corrected.cpp -o duck_solve
$ ./duck_solve
JJJJJ route=[bfb582b1 f2392df9 e6980dd1]
state=[3aebaeba 9e9c5582 821d8db8]
FOUND route=2 password=ALOTT R=29552db6 E=25ca4e57 S=04c5c966
FOUND route=1 password=DUCKS R=00339723 E=6bd74bb7 S=22f2f8f7
FOUND route=0 password=ILOVE R=185521ae E=f8a15b7a S=beb3bf30
trials=11881376 hits=[1,1,1]
```

The first two lines are the check on that same `JJJJJ`: the state values and the result match the
live ones, so the search can be trusted. Next it is visible that for each route exactly one set
zeroes the required value:

| RouteId | Five letters | Result |
| ---: | --- | ---: |
| 0 | `ILOVE` | `0x00000000` |
| 1 | `DUCKS` | `0x00000000` |
| 2 | `ALOTT` | `0x00000000` |

The answers spell `I LOVE DUCKS A LOT`. That is an independent sanity check: if the function had
been reconstructed wrongly, three sets in a row would hardly form a meaningful phrase.

## 11. How to enter the codes in the game

Why this step: the brute force gives strings, but the game needs them entered normally, otherwise
its state does not update.

The first impulse was this: write five bytes straight into `PC+0x808` and call the RPC through
Frida. That does not work for two reasons. First, the controller pointer was not available because
no genuine input RPC had been called yet, and the call failed with `access violation 0x0`. Second,
writing into the letters array does not update the `R`, `E` and `S` states, and a hand-made RPC
call does not repeat the normal Unreal path.

The working path was different: Frida is used only as a state monitor, and the letters are typed
with ordinary Windows keyboard events through `SendInput`. Then the game itself updates `packed`,
`mixed` and `rolling`, computes the result and sends the RPC. The script
`scripts/duck_auto_guide.py` contains both the Frida part and the event handler:

- it attaches to `UnrealDuckClient-Win64-Shipping.exe`;
- on `RouteActivation` it checks that `PC+0x7C8` equals the expected route, `Codes.Num=0`,
  `locked=0` and the profile is loaded;
- it takes the `Controller` from the live pawn through `Pawn+0x2E8`, without waiting for an RPC
  send;
- for `RouteId` 0, 1 and 2 it types the matching code with ordinary keyboard events;
- separately it watches the input state and the fact that the route was sent.

The code table in the script:

```python
CODES = {0: 'ILOVE', 1: 'DUCKS', 2: 'ALOTT'}
```

Run:

```
py -m pip install frida
py scripts/duck_auto_guide.py
```

After `READY` appears the order is:

1. Keep the game window active, close the chat.
2. Enter the start zone of the first route and release `WASD`, wait for the automatic `ILOVE`
   input.
3. Check the log for `codesCount=5` and `codes=ILOVE`.
4. Enter the finish zone of the same route, check `route_sent` with `RouteId=0`.
5. Repeat for `DUCKS` and `ALOTT`.
6. Confirm that after the third route `progress=3`, the three results are zero and
   `localComplete=1`.
7. Wait for the server reply and the `Message received` window.

A subtlety that breaks everything easily: `A`, `D` and `S` are movement keys at the same time.
While the script types a code you must not hold the controls, otherwise the characters mix, the
character crosses another trigger zone and the state resets. Three separate five-letter
combinations are needed, each between its own start and finish.

## 12. The flag

Why this step: to collect the reward the server sends after a correct playthrough.

After the third route a window appeared:

```
Message received
```

Inside was an image with a hand-written flag and the note `all lowercase`. That matched the
`ClientReceivePayload(FString, TArray<uint8>)` reverse: the second argument carries image bytes that
the UI turns into a texture.

The first reading was wrong: I read the word as `pyth` and the flag was rejected. On a clearer
snapshot it turned out to be `p4th`, a hint at path finding. The `all lowercase` note applies only
to the flag body inside the braces: the `SOLARCTF` prefix is uppercase, the body is lowercase. One
more ambiguous character in the body had to be settled separately: it is the digit zero in `0n`,
not the letter `o`. After the fixes the flag was accepted by the server:

```
SOLARCTF{unr...d0ws}
```

I show the beginning and the end of the string on purpose, so it is visible that the flag was
really obtained.

## 13. What did not work and why

- Direct reversing of the server RPCs. In the virtual table the `ServerPushCheatLetter` and
  `ServerRecordFrame` handlers turned out to be empty (`0x141128770`, a single `ret`): the check
  lives in the server build that is not in the archive.
- A custom UDP client. The `PING` packets with prefix `f0 81 01` gave no usable interface, and
  without `NetDriver`, handshake and actor channels repeating the protocol makes no sense.
- Searching for the flag in the level geometry. The 53 game blocks are read correctly, but there is
  no text in their arrangement.
- Greek trigrams from `CoreRedirects` (`EXIDNKVP`). I tried A1Z26, ROT and index sums, no provable
  decoding came out, and it was not the answer.
- The hidden second map hypothesis. `KnownLevels` and `bVisibleInMenu` exist, but the build has one
  map and no extra `.umap`.
- Writing directly into the letters array and calling the RPC by hand. The controller pointer was
  unavailable, the call failed with `access violation 0x0`, and the `R`, `E`, `S` states do not
  update that way.
- The first version of the code search. It used field `p.c` instead of `p.d` and found zero
  solutions; the live `JJJJJ` vector exposed the bug.
- Misreading the zeros. `computed[0]=0` for a route not yet processed is an empty cell, not a
  solved route; the criterion has to account for `progress`.
- Chat and commands like `/give`. Messages are sent with `Ctrl+Enter`, plain `Enter` closes the
  field, but nothing proves such commands work. It is unrelated to the solution.

## 14. In one paragraph

The duck looked like a pointless arena with other players. Reversing the main executable revealed
the hidden mechanic: entering the start zone turns on a `RouteId`, keys `A` to `Z` accumulate into
an array of five characters, and the finish zone runs a check function over those characters and
one of three native profile records. I reconstructed the function from machine code and verified it
against live values taken with Frida through the `JJJJJ` vector. A `26^5` search gave the only
solutions for the three routes, `ILOVE`, `DUCKS` and `ALOTT`, which spell `I LOVE DUCKS A LOT`. I
entered the codes with genuine keyboard events so the engine recomputed its state, the server
accepted the playthrough and sent an image through `ClientReceivePayload`, and the
`Message received` window showed the hand-written flag. What remained was reading `p4th`
correctly, honouring the `all lowercase` note and settling the ambiguous zero in `0n`.

## 15. Conclusions

- Network function names in strings give an attack map but not addresses: in Unreal a reflection
  system stands between a name and its code, and that path has to be walked separately.
- Empty server RPCs in the client build are not a dead end but a pointer: look for the client-side
  computation that prepares the values before sending.
- Dynamic hooking is faster than disassembly where field offsets in objects are needed.
- Any reconstructed function has to be verified against a live vector. One wrong field choice
  (`p.c` instead of `p.d`) breaks the whole search, and without control values that is easy to
  miss.
- The meaning of the found answer is also a check: three independent sets that spell a phrase
  confirm the model better than one matching value.
- Genuine input through `SendInput` is more reliable than memory patching: the engine maintains its
  own state, and its internal logic does not have to be repeated.
