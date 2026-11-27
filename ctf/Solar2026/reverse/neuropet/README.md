# NeuroPet (reverse)

An Android application challenge. Given: a file `neuropet.ab` of 359 458 bytes and a task text that
says "a neuro-tamagotchi in every home, password 0000". Nothing else.

About the input file:

- It is an Android Backup version 1, content compressed with zlib, no encryption: the header says
  `none`. SHA-256 `698a730af816cf91f324aeccbdde8ce66d5ca9acb2e367cb8f65d7b26a27eae9`.
- Inside is a TAR with three entries, the main one being
  `apps/com.solarlabs.neuropet/a/base.apk` of 364 193 bytes, that is the application itself and not
  user settings.
- SHA-256 of the extracted APK
  `5c2f61f102bda136956e25867b4a4fa6b2f65592251e4c978ed86d9fbff9ee49`.

What the application contains:

- `classes.dex` of 24 764 bytes, native libraries `libneurocore.so` for ARM64 of 11 320 bytes and
  for x86-64 of 11 960 bytes, resources `assets/neuro/brain.bin` of 1865 bytes, `skin.dat` of
  5342 bytes and `weights.bin` of 262 154 bytes.
- The library exports the JNI functions `nInit`, `nMoodTick`, `nRun`, `nQuery`, carries the string
  `NEUROC1!NEUROC2!NEUROC3!NEUROC4!` and the names of dynamic analysis tools, so it contains both a
  marker template and anti-debug checks.
- The resources are decrypted with a linear congruential generator:
  `state = (state * 0x41C64E6D + 0x3039) mod 2^32`, the key byte is the state shifted right by 16.
- `brain.bin` is a custom `NRBC` container with two scenarios: `mood` with seed `0x86DBA6AD` of 62
  bytes and `devroom` with seed `0xD2020289` of 1766 bytes.
- Inside is bytecode for a custom stack machine, its interpreter sits at `0x1300`, and opcodes are
  transformed before dispatch by multiplying with `-0x49` and adding `-0x4B`.

What the `devroom` scenario emits:

- In 525 instructions it performs four emissions, each with a marker from `NEUROC1!` to `NEUROC4!`,
  with lengths 10, 10, 9 and 9 bytes.
- The first fragment is ordinary text, the beginning of the flag. The second is XORed with `0x02`,
  the key comes from the seed. The third is stored backwards. The fourth is text in UTF-16LE.
- After the four transforms the parts glue into the flag.

Files:

- `scripts/01_unpack_android_backup.py` - unpacks the `.ab` and extracts the APK and resources
- `scripts/02_solve_neurovm.py` - parses `NRBC`, decrypts, runs `devroom` and assembles the flag
- `assets/neuropet.ab` - the original backup from the task
- `assets/base.apk` - the extracted application
- `assets/brain.bin` - the container with the scenarios
- `assets/devroom_decrypted.vm.bin` - the decrypted `devroom` bytecode

The full solving path is in `solve.md`.
