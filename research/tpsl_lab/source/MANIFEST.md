# TP/SL Lab — source provenance and checksums

Оригинальные архивы кода из приватного `S960b/agent-dialogs` (ветка `research/tpsl-lab-v0.13`), перенесены в этот публичный каталог байт-в-байт с сохранением Git blob SHA. Никаких заново написанных post-hoc торговых правил.

## SHA256

```text
571c5b2a4d0ad94c77c94ee335a8385c602e7839d4a5969b79dd19a9ee190ade  tpsl-lab-v0.1.0-src.tar.gz
d06062fba8929b01d8050ebc6fb7aa2e3015d660fde2ad7b6acfe05d395ae9ba  tpsl-lab-v0.2.0-src.tar.gz
f70b399b5e65e9a30fe2be8a23d35397a02218f91695e36c256cc4d5d9ceeecc  tpsl-lab-v0.4.0-src.tar.gz
78cffefe0d52668f03977b326c5d4e6c0788d3366c2a17aef2b528a0e8d0a2f0  tpsl-lab-v0.5.0-src.tar.gz
3bf9ad6a4b3297e87b49255fc03f7b0669218943d679dc2b208c0c7b4a3d4e8e  tpsl-lab-v0.6.0-src.tar.gz
554bb04d876293b2caf3cd312c4fbbcb8fecf96df31da45e6155a939f4c5d13e  tpsl-lab-v0.7.0-src.tar.gz
8dbb4baf7fe0d06304e8f196cb0967484ce335d8d805cb649560d31d70c0ac1b  tpsl-lab-v0.8.0-src.tar.gz
3fc620b5af3f576987cf1ebd3ad26b9a01faf5616c6af2bdccc6eacbe4de631d  tpsl-lab-v0.8.1-src.tar.gz
b3cafe22c6d0703b9628834db0eb75a3051ad12524b531db135ee1ab6e4f2fe7  tpsl-lab-v0.9.0-src.tar.gz
2f06759e56d2f2f13944e5ee08fa136bd773d14cae3a2cb75f547ae95eb36716  tpsl-lab-v0.10.0-src.tar.gz
b0174e2bbb7c1555c74414b227f1e053a75a3a927d48bfc816d97eb0b78a5b66  tpsl-lab-v0.11.0-src.tar.gz
3d1dd0bcbf2944e989f6d9e70e65e23597340009e1c08c3940fe3fb9daf8aa40  tpsl-lab-v0.12.0-src.tar.gz
f46f6153c6dbdc9a32303c8c86e84ca44fc9032928dc5d9c7bebef9b438a2fed  tpsl-lab-v0.13.0-src.tar.gz
```

v0.3 в приватном Git хранилась **как** `upgrade-v02-to-v03.patch.gz`, а не полноценный архив; он тоже скопирован сюда. Для воспроизведения v0.3 следует применять patch к v0.2 согласно оригинальному README этой версии.

Скрипты хранятся внутри архивов `src/tpsl_lab/` вместе с `tests/`, `pyproject.toml` и исследовательскими протоколами. Входные рыночные архивы не включены в Git.

Проверка:
```bash
sha256sum -c SHA256SUMS.txt
# либо вручную сравните sha256sum нужного файла с таблицей выше
tar -tzf tpsl-lab-v0.13.0-src.tar.gz | head
```

**Примечание:** `SHA256SUMS.txt` в этой папке не создавался, поэтому используйте ручную сверку или извлеките таблицу. Не выполняйте код в реальном торговом аккаунте.
