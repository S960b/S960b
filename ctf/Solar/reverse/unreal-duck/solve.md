# Unreal Duck: solving path

## 0. The whole plan

Why this section: so the reader sees the chain before the details and understands why each
step is needed.

Given: a Windows client of an Unreal Engine game, a folder of content containers, and a
launcher. The content hides logic that hands out a flag after certain actions in the game.

Goal: find out what has to be done in the game and get the flag.

The plan was:

1. Understand what the build consists of: which files, which containers, what reads them.
2. Unpack the content into plain formats and look inside.
3. Find where the game logic lives: in blueprints or in native code.
4. Reverse the native part: which functions handle code entry and the reward.
5. Understand the conditions: what the code is, where it is written, how it is checked.
6. Automate the playthrough and intercept the reply that carries the flag.

## 1. What is given and which tools read it

Why this step: to determine the file formats, otherwise it is unclear what to unpack with.

First I look at the folder structure and file types with `file`:

```
$ file UnrealDuckClient.exe
UnrealDuckClient.exe: PE32+ executable (GUI) x86-64, for MS Windows

$ ls UnrealDuck/Content/Paks/
global.ucas  global.utoc  UnrealDuck-WindowsClient.pak
UnrealDuck-WindowsClient.ucas  UnrealDuck-WindowsClient.utoc

$ file UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe
... PE32+ executable (GUI) x86-64, for MS Windows
```

The launcher `UnrealDuckClient.exe` is about 167 KB, far too small for a game, it only starts
the main process. The real code is in
`UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe`, about 143 MB.

The file sizes immediately hint at which is which:

```
$ ls -l --block-size=K UnrealDuck/Content/Paks
 2803K global.ucas
    1K global.utoc
10759K UnrealDuck-WindowsClient.pak
283116K UnrealDuck-WindowsClient.ucas
   213K UnrealDuck-WindowsClient.utoc
```

An index of 213 KB next to 283 MB of data looks like a table of contents plus content, but that
is a guess, not knowledge. To check it I googled the extensions `.utoc` and `.ucas` themselves.
The search leads to the Epic documentation on IoStore: it is an Unreal container where `utoc`
holds the index (a table of chunks and a list of packages) and `ucas` holds the data, and the two
files only work as a pair. The pair `global.utoc` and `global.ucas` is shared engine data, the
pair `UnrealDuck-WindowsClient` is the game content. A `.pak` file is the older Unreal container,
it sits next to them in this build, and it has to be checked too so that no part of the content
is missed. Ready-made tools exist for both formats: `retoc` for IoStore and `repak` for pak, and
those are what I use.

## 2. Container structure

Why this step: to learn how many packages are inside, which compression they use, and whether
the pak holds anything, because that decides what to unpack and with which tool.

I start with the index because it is the table of contents of the container: without it there is
no way to list assets or learn the compression. First I tried to read the index with my own
script, following a description of the IoStore header layout. The script failed on its very
first read: at the expected offsets there were meaningless values. To see what was wrong I
dumped the first bytes of the file:

```
$ head -c 32 UnrealDuck/Content/Paks/UnrealDuck-WindowsClient.utoc | xxd
00000000: 2d3d 3d2d 2d3d 3d2d 2d3d 3d2d 2d3d 3d2d
00000010: 0800 0000 9000 0000 0100 0000 2c00 0000
```

The first sixteen bytes are the text string `-==--==--==--==-`. A container header cannot start
with readable text, so this is not part of it, but a banner someone prepended to the file. The
way to check this is to shift: if I read not from zero but from byte sixteen, then version `08`
and header size `90 00 00 00`, that is 144 bytes, land exactly where they belong, and every
following field agrees. At that offset my parser reads the whole index and returns the package
list. It also shows the limitation: a parser that looks for the header strictly at offset zero
will not work on this file. `retoc` reads it fine because it skips the unexpected leading bytes
itself.

Then, instead of parsing the index by hand, I hand it to `retoc`. First the summary: how many
chunks and packages, which compression they use, where the content mounts.

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
`ExportBundleData` is asset data, `BulkData` is large nested data such as textures,
`ShaderCode` is shaders. A package is the logical unit corresponding to one asset. The listing
was needed to show that asset names are not in the table of contents: only chunk identifiers are
visible here, and the names have to come from the packages themselves after unpacking.

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
listing asset paths), but no game assets at all. So the whole content under study is in IoStore,
the file to work with is `UnrealDuck-WindowsClient.utoc`, and the pak is only good for taking the
configs and the registry. That check is exactly why `repak info` was needed.

## 3. Unpacking into plain formats

Why this step: while the content is in IoStore it cannot be read as ordinary files, and we need
object names and level structure.

The order is this. First `retoc to-legacy` converts IoStore back into classic `.uasset` and
`.umap` plus `.uexp`, the form Unreal used before Zen. Then `repak unpack` unpacks the pak. All
of it is wrapped in `scripts/duck_extract.sh`:

```
$ ./scripts/duck_extract.sh UnrealDuck/Content/Paks out
[retoc] UnrealDuck-WindowsClient
[repak] UnrealDuck-WindowsClient
```

After that `out/legacy_assets` contains a tree like
`UnrealDuck/Content/ThirdPerson/Blueprints/BP_ThirdPersonPlayerController.uasset`. These files
are what the analysis works with.

## 4. Parsing levels and assets

Why this step: to pull object names out of the assets, so we know which entities the game has
and how they are named.

The unpacked files can already be opened, but this is a binary format, so I googled its
description. A classic Unreal package starts with `FPackageFileSummary`, which holds the name
table `NameMap` and the export map `ExportMap`. Following that description I wrote the parser in
`scripts/duck_map_layout.py`. The script reads the magic `0x9e2a83c1`, checks the package
version, extracts names and exports, and writes `layout.json` and `names.txt`.

Run and result for the level map:

```
$ python3 scripts/duck_map_layout.py out/legacy_assets/.../Lvl_ThirdPerson.umap --out dkl
$ head -5 dkl/names.txt
```

The summary shows `legacy: -9`, `cooked: True` and `uses_unversioned_properties: True`. A
negative legacy version means UE5, and `unversioned` means property names are not written into
the file, so they cannot be read as plain text without class descriptions.

The content tree keeps producing files with the `BP_` prefix, and they deserve a stop. A blueprint
is the way to write game logic in Unreal without C++: you build a graph of nodes in the editor,
and the result is stored as an ordinary asset with the `BP_` prefix. I learned this when I googled
what the files like `BP_ThirdPersonPlayerController.uasset` in `Content/ThirdPerson/Blueprints`
are, and why there is no program text inside such a file.

The blueprints look the same as the map. For example, `BP_ThirdPersonPlayerController` has
`UnrealDuckPlayerController` in its name table, and `BP_ThirdPersonGameMode` has
`UnrealDuckGameMode`. So a blueprint is a thin wrapper over a native C++ class, and the logic
lives in native code, not in the graph.

## 5. Where the logic actually is

Why this step: if the logic is in native code, the executable has to be reversed, not the
assets.

I check this by searching the strings of the main executable:

```
$ strings -a UnrealDuck/Binaries/Win64/UnrealDuckClient-Win64-Shipping.exe \
    | grep -E 'ServerPushCheatLetter|ServerRecordFrame|ClientReceivePayload'
ClientReceivePayload
ServerPushCheatLetter
ServerRecordFrame
```

These three names are RPC functions, that is calls the client sends to the server and receives
back. The names speak for themselves: `ServerPushCheatLetter` sends one letter of the code,
`ServerRecordFrame` records the frame or zone completion state, `ClientReceivePayload` receives
a payload from the server, that is a message and an image.

Then I located the addresses of these functions. To avoid guessing, I check them against the PE
header: the file has a section table, which maps an RVA to a file offset, and at that offset a
function really starts. Addresses and first bytes:

```
RVA 0x4381d30  off 0x4381330  40 53 48 83 ec 20 88 54 24 38 48 8b d9
RVA 0x4381d70  off 0x4381370  4c 8b dc 48 83 ec 68 33 c0 49 89 7b f8
RVA 0x4394600  off 0x4393c00  40 55 56 57 48 83 ec 40 f6 81 48 03 00 00 02
```

What is visible here. `40 53` is `push rbx`, `48 83 ec 20` allocates stack, and `88 54 24 38`
stores a byte from register `dl` into the stack. So the second argument of
`ServerPushCheatLetter` is exactly one byte, the letter code. That matches the name and
confirms the function was found correctly.

## 6. Watching the game from inside

Why this step: the disassembly shows the functions exist, but not which controller fields they
read and in which order they are called. That is faster to see at runtime.

I looked for a way to watch the runtime like this: I read how other people intercept calls in
someone else's process. That is how I found Frida, a tool that attaches to a running process and
places hooks on functions by address. The script `scripts/duck_watch_all.py` hooks the found RVAs
and prints arguments on every call, and `scripts/duck_capture_payload2.py` specifically hooks
`ClientReceivePayload` and saves the received bytes to a file.

The first argument of these functions is the controller object itself, so the hooks show not only
the values but also the offsets they live at. The result was this layout:

- offset `0x808` holds a pointer to the letters array `Codes`,
- offset `0x810` holds the letter count in that array,
- offset `0x800` holds the unlock byte,
- a zone has the route number at offset `0x2c0`.

Letters are encoded simply: `A` is 0, `B` is 1, and so on, that is the character code minus 65.

## 7. Which letters are needed

Why this step: without the right sequence the server will not hand out the reward.

First I checked the obvious option: look for ready strings in the content. A search across the
unpacked assets and the pak in two encodings, plain and UTF-16, found neither `ILOVE` nor
`DUCKS` nor `ALOTT`. So the sequence is defined by the server logic, not stored as text in the
files.

Then I determined it in the game. The mechanic is this: the controller accepts exactly five
letters, and after the fifth the server either confirms the set or rejects it. So the set can be
searched: I submitted five-letter candidates and watched the server answer. Rejected variants
looked no different from each other, while confirmed ones formed a meaningful phrase, so the
search converged quickly on three sets, one per route: `ILOVE` for route 0, `DUCKS` for route 1,
`ALOTT` for route 2. Together they form `I LOVE DUCKS A LOT`.

## 8. Automating the playthrough

Why this step: running around the arena and typing letters by hand is slow and error prone.

The script `scripts/duck_auto_guide.py` does everything itself. The logic is:

1. Wait until the player enters a game zone and read the route number from the zone at
   offset `0x2c0`.
2. Take the player controller, it is remembered from the `ServerRecordFrame` call.
3. Write the letters of the required code into the `Codes` array byte by byte, where `A` is 0.
4. Set the counter `0x810` to five and the unlock byte `0x800` to zero.
5. Call `ServerPushCheatLetter` for each letter.
6. Bring the duck to the end of the zone so that `ServerRecordFrame` fires, that is the server
   records the completion.

The key fragment where letters are placed into the array and sent:

```javascript
for (let i=0;i<vals.length;i++) codesPtr.add(i).writeU8(vals[i]);
controller.add(OCN).writeS32(vals.length);
controller.add(OLCK).writeU8(0);
for (let i=0;i<vals.length;i++) pushLetter(controller, vals[i]);
```

What it gives us: the server sees exactly what it would see during a honest code entry, and
confirms the completion.

## 9. The flag

Why this step: to collect the reward the server sends after a correct code.

When the server accepts the code it calls `ClientReceivePayload` and passes a title as a string
and a byte array. The script `scripts/duck_capture_payload2.py` intercepts this call and saves
the received data. The flag is in it:

```
SOLARCTF{unr...d0ws}
```

I show the beginning and the end of the string on purpose, so it is visible the flag was really
obtained.

## 10. What did not work, and why

- Reading properties from blueprints. The packages are flagged `unversioned`, property names are
  not written there, so asset parsing gives only class and component names, not the logic.
- Decoding static mesh coordinates. I wrote a separate script for that, but it does not lead to
  the flag: level geometry does not participate in the code check.
- Parsing the container with a simple parser. The IoStore index starts with a sixteen-byte
  banner, so naive header reading from offset zero yields garbage; the offset had to be taken
  into account, and then `retoc` was used.
- Brute forcing letters across the whole alphabet. It works but is slow; once it was clear that
  each route needs a meaningful phrase, brute force was replaced by direct substitution.

## 11. In one paragraph

The content is in IoStore, I unpacked it with `retoc to-legacy` and `repak`, parsed the package
structure, and found out that the game logic is in native code while blueprints are only
wrappers. By the strings of the executable I found the RPC functions, checked their addresses
against the PE header, and confirmed that the letter input function takes exactly one byte.
Frida hooks showed that the controller keeps the letters array at `0x808` and the counter at
`0x810`, and the zone route is at `0x2c0`. Ready code strings are absent from the content, so I
determined them in the game: `ILOVE`, `DUCKS`, `ALOTT`. The script wrote the letters into the
array, sent them to the server and brought the duck to the end of the zone, after which the
server sent the reward through `ClientReceivePayload`, and the flag `SOLARCTF{unr...d0ws}` was
saved from it.

## 12. Lessons

- Before reading assets, find out where the logic lives: in the blueprint graph or in a native
  class. Here almost everything was in native code.
- RPC names in strings give a ready attack map, all that remains is to find addresses and check
  them against the file header.
- Dynamic interception is faster than disassembly when object field offsets are needed.
- A missing string in the content is a result too: if the code is not in the files, the server
  checks it, and it has to be determined in the game.
