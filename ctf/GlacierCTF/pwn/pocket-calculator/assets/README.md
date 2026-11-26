# Original assets / Исходные материалы

The original distribution is `pocket-calculator.tar.gz` (19,778,873 bytes). The two supplied copies are byte-identical.

SHA-256 of the archive: `1a962e317a9b24b5d44a7048a017df9bee8fdcf90f2f055264f0e1277b128e78`.

The executable `pocket-calculator/challenge` is 2,580,760 bytes; SHA-256: `53cdba8c2e720dd833eec0c20390e4bdcb792d5dd7ab214e525cf6edc58bcc6b`.

The large original executable, guest images and compressed archive are intentionally not duplicated in Git. Obtain the original attachment from your CTF materials, place it in this directory and extract it. All 16 files passed the organizer's checksum manifest in both the agent's preparation and the independent inspection for this write-up.

| File | Purpose |
| --- | --- |
| challenge | Static unstripped x86-64 calculator executable |
| bzImage | Guest kernel, 9,585,664 bytes |
| rootfs.qcow2 | Guest root filesystem, 24,117,248 bytes |
| session-runner, session-runner.c | Restricted process per connection |
| run-qemu.sh, Dockerfile, QEMU.md | Pinned KVM runtime |
| inetd.conf, S97flag, sh-wrapper.c, entrypoint.sh | Guest configuration and helpers |
| Makefile | Build configuration; main.cpp not supplied |
| deploy.sh, deploy.bat | Deployment |
| flag.txt | Organizer's dummy flag, not competition flag |
| sha256sum | Original file manifest |

The original checksum manifest is reproduced in [sha256sum.original](sha256sum.original). Run it from the extracted challenge directory:

```sh
sha256sum pocket-calculator.tar.gz
tar -xzf pocket-calculator.tar.gz
(cd pocket-calculator && sha256sum -c sha256sum)
```

Обе переданные копии исходного архива совпадают побайтно. Проверены контрольные суммы всех 16 файлов. Большие бинарники и образы не дублируются в Git, для воспроизведения используйте оригинальное вложение организаторов. В комплекте есть фейковый флаг для локальной проверки; настоящий флаг не публикуется.
