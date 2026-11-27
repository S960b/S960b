<!-- Source: participant's recovered mircosoft materials from original solve conversation. Updated after independently testing unpack.py and original solve.py against the organizer archive. -->
# Mircosoft — GlacierCTF 2026

**Category:** Reverse Engineering / Cryptography / Forensics  
**Status:** Solved, independently reproduced from the original challenge archive (offline, 27 November 2026)  
**Flag:** `gctf{...}`

> The name is spelled **mircosoft** in the supplied archive and executable. All byte offsets in this document are hexadecimal unless stated otherwise. Everything below was reproduced offline from the supplied challenge files; no live Windows execution or real license server was required.

---

# Русская версия

## 1. Исходные данные и идея

В архиве `mircosoft.tar.gz` находятся:

| Файл | Размер | SHA-256 |
|---|---:|---|
| `mircosoft.exe` | 27 136 байт | `5412f535d238bdf2418f179044dcd2ee25c6cc3d60d2669196bc9e5131f99712` |
| `admin_machine.dmp` | 50 328 байт | `a51edf96a395692dabfb67b654f4eb3f4742dd483da8d7d3d49579a369d2d5e4` |
| `license.bin` | 104 байта | `56b71ff7a851d4b7c074637cd1a96f458b32e2e306754b788c01af8fe8b89891` |

Задача построена на имитации DRM: криптографический ключ зависит от аппаратных идентификаторов «лицензированного» ПК. Нам выдали дамп памяти именно этой машины. Поэтому путь решения — **сначала распаковать программу и извлечь встроенные ключи, потом достать значения железа из дампа, проверить лицензию и расшифровать полезную нагрузку**.

Исходный EXE не содержит флаг в явном виде. Это PE32+ x64 с запутанной точкой входа и зашифрованным внутренним PE. Поэтому извлечение строк из оригинального EXE недостаточно.

## 2. Распаковка оригинального EXE

### 2.1. Как нашли загрузчик

У `mircosoft.exe` точка входа находится по RVA `0x14F8` (адрес `0x1400014F8` при ImageBase `0x140000000`). В коде виден диспетчер состояний на `EBX`, который постоянно возвращается к общему блоку `0x14000157C`. Ветвления и дополнительные вычисления затрудняют чтение, но полезные операции вынесены в отдельные функции.

В загрузчике есть проверки `PEB.BeingDebugged`, `NtGlobalFlag` и `RDTSC`; при обнаружении отладки меняются рабочие параметры. Для воспроизведения выбран **обычный путь без отладчика**: байт проверки отладки равен нулю. Загрузчик также динамически ищет системные API, выделяет память, переносит туда PE и обрабатывает импорты/релокации. Чтобы получить содержимое внутреннего PE, выполнять эти Windows API не нужно.

Исходные данные для распаковки находятся в секции `.rdata` оригинального файла:

- `.rdata` RVA `0x3000`, raw offset `0x1800`.
- Первые `0x4800` байт секции (18 432 байта) — упакованный внутренний PE.
- Три небольшие обфусцированные таблицы располагаются по RVA `0x7800`, `0x7810` и `0x7830` (16, 32 и 32 байта соответственно).

### 2.2. Восстановление трёх таблиц

Функция загрузчика `0x140001068` декодирует таблицы так:

```python
out[i] = src[i] ^ ((i * 0x5A + 0x3C) & 0xFF)
```

Она возвращает:

```text
xor_key      = 01e0b04660652e12bf68b4a4f59c82ca
subtract_key = 715434a3945858313b98ba8f8f66273488bb358bcc8c93edb4b8aadfbfd2e932
rotation_key = 0101060505030304020703020203060606030607050104070702020705010505
```

Это **ключи распаковщика**, не четыре криптографических ключа DRM, которые мы получим позднее.

### 2.3. Слой 1: восстановление порядка блоков

Функция `0x140001144` обрабатывает `0x4800` байт как `0x48 = 72` блока по `0x100 = 256` байт. Перемешивание восстанавливается детерминированной перестановкой Фишера–Йетса. Для генерации индексов используется 64-битный LCG:

```python
state = 0xDEAD1337CAFE4269
for i in range(71, 0, -1):
    state = (state * 0x5851F42D4C957F2D + 0x14057B7EF767814F) & ((1 << 64) - 1)
    j = state % (i + 1)
    permutation[i], permutation[j] = permutation[j], permutation[i]
```

Загрузчик вычисляет обратную перестановку `inverse[permutation[i]] = i` и копирует 256-байтовые блоки: `destination[inverse[i]] = source[i]`. Важно использовать именно **обратную перестановку**. Простая перестановка блоков в прямом порядке не даёт корректный PE.

### 2.4. Слой 2: вращение и вычитание

Функция `0x1400010F0` преобразует каждый байт:

```python
rotation = rotation_key[i % 32] & 7
byte = ror8(byte, rotation)
byte = (byte - subtract_key[i % 32]) & 0xFF
```

Индекс повторяется каждые 32 байта, а вращение выполняется по модулю 8 бит.

### 2.5. Слой 3: XOR с обратной связью

Функция `0x1400010A4` проводит следующий проход:

```python
feedback = 0
for i, encrypted_byte in enumerate(buffer):
    buffer[i] = encrypted_byte ^ xor_key[i % 16] ^ feedback
    feedback = (encrypted_byte + xor_key[(i + 3) % 16]) & 0xFF
```

При вычислении `feedback` нужен **исходный байт до XOR**, а не уже расшифрованный. Это существенно: в противном случае результат расходится после первого байта.

После третьего слоя первые байты равны:

```text
4d 5a 90 00 03 00 00 00 04 00 00 00 ff ff 00 00
M  Z
```

По `e_lfanew = 0xD8` находится настоящая сигнатура `PE\0\0`.

**Результат:** `unpacked_pe.bin`, 18 432 байта, SHA-256:

```text
9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80
```

Отдельный, не зависящий от Windows скрипт `unpack.py` воспроизводит ровно эти три этапа.

## 3. Извлечение четырёх ключей из внутреннего PE

В распакованном PE уже другая секционная таблица. Её `.data` имеет RVA `0x6000`, а **файловое** смещение — `0x3E00`. Это объясняет ранее непонятный фрагмент:

```python
exe = Path('unpacked_pe.bin').read_bytes()
D = exe[0x3e00:]
keys = [D[16*i:16*i+16] for i in range(4)]
```

Это не поиск магического флага и не подстановка ключей. Из первых 64 байт `.data` берутся **четыре 16-байтовых ключа**:

| Ключ | Внутренний PE, файловые смещения | Значение |
|---|---|---|
| `K0` | `0x3E00–0x3E0F` | `67e6096a85ae67bb72f36e3c3af54fa5` |
| `K1` | `0x3E10–0x3E1F` | `7f520e518c68059babd9831f19cde05b` |
| `K2` | `0x3E20–0x3E2F` | `982f8a4291443771cffbc0b5a5dbb5e9` |
| `K3` | `0x3E30–0x3E3F` | `5bc25639f111f159a4823f92d55e1cab` |

В окончательном `solve.py` файловый offset не захардкожен: он определяется из секционной таблицы PE через `read_section(inner, '.data')`.

## 4. Разбор дампа памяти Windows

`admin_machine.dmp` — настоящий Windows MiniDump (сигнатура `MDMP`), а не набор вырезанных бинарных файлов. В нём пять stream-записей; stream типа `5` — `MINIDUMP_MEMORY_LIST` с семью областями памяти. Каждый дескриптор содержит виртуальный базовый адрес, размер и смещение данных внутри `.dmp`.

Не нужно вручную создавать `region0.bin`–`region6.bin`: `solve.py` читает таблицу дескрипторов MiniDump, выбирает области по базовым адресам и извлекает байты по внутриблочным смещениям.

| Параметр | Базовый адрес области | Смещение / длина | Извлечённое значение |
|---|---|---|---|
| CPUID brand string | `0x7FFE0000` | `0x274` / 48 | `Intel(R) Core(TM) Ultra 7 155H` с нулевым дополнением |
| Serial тома | `0x22000000000` | `0x48` / 4 | `914c8dd4` (hex, raw bytes) |
| `MachineGuid` | `0x1C000000000` | `0x500` / 36 | `658c05ff-e49e-40aa-b041-9a5f56886dea` (ASCII) |
| SMBIOS UUID | `0x20000000000` | `0x55` / 16 | `3c31a857b2b700488ce185385968c8a8` (hex, raw bytes) |
| MAC | `0x21000000000` | `0x198` / 6 | `52:54:00:36:e6:64` |

Обратите внимание: CPU-строка используется как **все 48 байт**, включая завершающие нули. Serial и UUID — бинарные поля, а `MachineGuid` — 36 байт ASCII. Ошибка в формате любого поля ломает проверку лицензии.

Аппаратный отпечаток составлен в фиксированном порядке:

```python
fingerprint = cpuid_48 + volume_serial_4 + machine_guid_36 + smbios_uuid_16 + mac_6
assert len(fingerprint) == 110
```

## 5. Что лежит в `license.bin`

Размер файла — 104 байта. Результат разбора:

| Смещение | Размер | Значение/назначение |
|---|---:|---|
| `0x00` | 4 | ASCII `MSLC` |
| `0x04` | 4 | `01 00 00 00`, версия 1 |
| `0x08` | 16 | сохранённый MAC/контрольное значение |
| `0x18` | 8 | nonce для поточного шифрования |
| `0x20` | 64 | зашифрованный payload (8 блоков по 8 байт) |
| `0x60` | 8 | завершающие данные; в восстановленном алгоритме расшифровки не используются |

Хранимый MAC:

```text
8305d5d5c5316f76bd27ddbbc5221f56
```

Nonce:

```text
ed815533e468086f
```

### 5.1. Примитив: XTEA

Здесь используется **XTEA**, а не классический TEA, с 32 раундами, 64-битным блоком, 128-битным ключом, константой `0x9E3779B9` и 32-битной арифметикой по модулю `2^32`. Обе половины и ключевые слова интерпретируются в `little-endian`. Функция `xtea_encrypt()` реализована в `solve.py`.

### 5.2. Проверка подлинности лицензионного отпечатка

Собственная 16-байтовая MAC-подобная конструкция работает с четырьмя 32-битными словами состояния `(a,b,c,d)`. Каждые четыре байта данных трактуются как `little-endian` `u32` (последний неполный блок дополняется нулями). Затем:

```python
a ^= input_word
a, b = XTEA_encrypt((a, b), Kleft)
c, d = XTEA_encrypt((c, d), Kright)
d ^= b
a ^= c
```

Это самодельный криптографический аутентификатор задания, а не стандартный HMAC.

Контрольное значение лицензии вычисляется от всех 110 байт железа с начальным состоянием:

```python
IV = pack_u32_le(0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476)
tag = custom_mac(IV, fingerprint, K2, K3)
```

Результат совпадает **полностью, все 16 байт**:

```text
Сохранённый:  8305d5d5c5316f76bd27ddbbc5221f56
Вычисленный: 8305d5d5c5316f76bd27ddbbc5221f56
```

Это независимая проверка, что порядок, типы и длины извлечённых аппаратных значений правильны, а ключи из `.data` действительно подходят к лицензии.

## 6. Производный ключ и расшифровка флага

Для ключа шифрования используем **уже другие ключи `K0`, `K1`**, стартуя с нулевого состояния из 16 байт. Данные обрабатываются не одним вызовом, а тремя группами:

```python
state = bytes(16)
state = custom_mac(state, cpuid,               K0, K1)
state = custom_mac(state, volume + machineguid, K0, K1)
state = custom_mac(state, uuid + macaddr,       K0, K1)
```

Полученные промежуточные состояния (hex):

```text
после CPUID:       6e75487e27f3f55f92a66707fa322b63
после volume+GUID: 9df5ab2c698825c8b3ea6b1bd1ab63fa
после UUID+MAC:    55e8a940fa964f9fe1c996e31f338143
```

Далее программа дополнительно смешивает 32-битные слова через `ROL(..., 7)`, XOR и сложение по `2^32` **последовательно**: новый `a` участвует в вычислении `c`, а новый `b` — в вычислении `d`:

```python
a = (rol32(a,7) ^ b) + c  # mod 2^32
b = (rol32(b,7) ^ c) + d
c = (rol32(c,7) ^ d) + a
_d = (rol32(d,7) ^ a) + b
```

Итоговый ключ поточного режима:

```text
3b8652afcde7de87294a383867f1a9f7
```

`nonce` состоит из двух маленьких 32-битных слов `(v0,v1)`. Для восьми 8-байтовых блоков полезной нагрузки гамма вычисляется так:

```python
for i in range(8):
    gamma = XTEA_encrypt(pack_u32_le((v0+i)&0xffffffff, v1), derived_key)
    plaintext[8*i:8*i+8] = ciphertext[8*i:8*i+8] XOR gamma
```

Получаем 64 байта: **38 байт ASCII-флага + 26 нулевых байт дополнения**.

```text
gctf{...}
```

Смысл `cert_f0rg3d` здесь — результат вскрытия DRM на основе дампа. Не стоит автоматически утверждать, что в задании использовался настоящий сертификат X.509: в воспроизведённой проверке видны нестандартные MAC и XTEA, а не PKI.

## 7. Как повторить с нуля

Нужен **только Python 3.9+ и оригинальный `mircosoft.tar.gz`**. Сеть, Windows, Wine и сторонние модули не нужны.

Поместите `unpack.py`, `solve.py`, `test_repro.py` и `mircosoft.tar.gz` в одну директорию и выполните:

```bash
python3 solve.py mircosoft.tar.gz --save-unpacked unpacked_pe.bin
python3 test_repro.py mircosoft.tar.gz
```

Для независимой проверки распаковщика:

```bash
tar -xzf mircosoft.tar.gz
python3 unpack.py mircosoft/mircosoft.exe -o another_unpacked_pe.bin
sha256sum another_unpacked_pe.bin unpacked_pe.bin
cmp another_unpacked_pe.bin unpacked_pe.bin
```

Ожидаемый итог:

```text
License MAC (stored):     8305d5d5c5316f76bd27ddbbc5221f56
License MAC (calculated): 8305d5d5c5316f76bd27ddbbc5221f56
Derived stream key: 3b8652afcde7de87294a383867f1a9f7
FLAG: gctf{...}
```

Регрессионные тесты проверяют корректный PE и флаг, а также отрицательные сценарии: изменённый MAC лицензии, изменённый дамп и повреждённый ключ упаковщика. Все шесть тестов проходят.

**Ключевой урок:** исходный `mircosoft.exe` — не тот файл, из которого надо читать ключи по `0x3E00`. Сначала нужно воспроизвести загрузчик и получить `unpacked_pe.bin`; только после этого становятся доступными настоящая таблица секций, четыре ключа DRM и полный проверяемый путь до флага.

---

# English version

## 1. Files and attack idea

The original `mircosoft.tar.gz` contains a 27,136-byte x64 executable (`mircosoft.exe`), a 50,328-byte Windows MiniDump (`admin_machine.dmp`) and a 104-byte license (`license.bin`). The license is bound to five hardware identifiers from the machine whose memory was dumped. The task is to reconstruct the program's decryption logic and use the supplied machine state to decrypt the licensed payload.

The original EXE is a packed PE32+ executable with an obfuscated loader, so strings and static inspection of the outer PE are not enough. The successful offline chain is:

```text
original EXE ──loader deobfuscation──> inner PE ──.data──> K0,K1,K2,K3
Windows MiniDump ──MemoryList parser──> CPU, volume, GUID, UUID, MAC
hardware fingerprint + K2/K3 ──custom MAC──> license authentication
hardware groups + K0/K1 ──custom MAC + transform──> XTEA stream key
license payload + key + nonce ──XTEA-CTR-like XOR──> flag
```

## 2. Reconstructing the unpacker

The outer EXE's entry RVA is `0x14F8` (VA `0x1400014F8`). Its flattened dispatch loop uses `EBX` state values and dispatches through `0x14000157C`. It also checks `PEB.BeingDebugged`, `NtGlobalFlag` and timing through `RDTSC`; the reproducible unpacker follows the ordinary non-debugged path. The loader allocates memory and performs import/relocation handling, but none of those Windows API operations are necessary to recover the embedded PE bytes.

The outer `.rdata` starts at RVA `0x3000`, file offset `0x1800`. Its first `0x4800` bytes hold the encrypted inner PE. Three decoder constants are stored at loader RVAs `0x7800` (16 bytes), `0x7810` (32 bytes), and `0x7830` (32 bytes).

At `0x140001068`, the loader decodes these constant tables with:

```python
key[i] = encoded[i] ^ ((i * 0x5A + 0x3C) & 0xFF)
```

They yield:

```text
XOR key:      01e0b04660652e12bf68b4a4f59c82ca
Subtract key: 715434a3945858313b98ba8f8f66273488bb358bcc8c93edb4b8aadfbfd2e932
Rotate key:   0101060505030304020703020203060606030607050104070702020705010505
```

**Unpacking stage 1, `0x140001144`:** recover the order of 72 shuffled blocks of 256 bytes. The code uses a Fisher–Yates permutation with a 64-bit LCG:

```python
state = 0xDEAD1337CAFE4269
for i in range(71, 0, -1):
    state = (state * 0x5851F42D4C957F2D + 0x14057B7EF767814F) & ((1<<64)-1)
    j = state % (i+1)
    permutation[i], permutation[j] = permutation[j], permutation[i]
```

Its inverse is applied to the chunks: `destination[inverse[i]] = source[i]`. The inverse matters: using the forward permutation directly produces garbage.

**Unpacking stage 2, `0x1400010F0`:** for byte index `i`, rotate the byte right by `rotation_key[i % 32] & 7`, then subtract `subtract_key[i % 32]` modulo 256.

**Unpacking stage 3, `0x1400010A4`:** apply a repeating 16-byte XOR key plus feedback:

```python
feedback = 0
for i, raw in enumerate(buffer):
    buffer[i] = raw ^ xor_key[i % 16] ^ feedback
    feedback = (raw + xor_key[(i+3) % 16]) & 255
```

Note that the feedback uses the *pre-XOR* byte. The result begins `4d 5a 90 00`, its PE header is at `e_lfanew = 0xD8`, and it contains seven PE sections. The extracted inner file is `unpacked_pe.bin` (18,432 bytes), SHA-256:

```text
9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80
```

The dependency-free `unpack.py` reconstructs this inner PE directly from the original EXE without executing it.

## 3. Four embedded cryptographic keys

The **inner**, not the outer, PE has a `.data` section with RVA `0x6000` and raw file offset `0x3E00`. The first 64 bytes of that section contain four consecutive 16-byte keys:

```text
K0 = 67e6096a85ae67bb72f36e3c3af54fa5
K1 = 7f520e518c68059babd9831f19cde05b
K2 = 982f8a4291443771cffbc0b5a5dbb5e9
K3 = 5bc25639f111f159a4823f92d55e1cab
```

This explains the original `exe[0x3e00:]` expression: it slices the **unpacked** PE at the start of its raw `.data` section, then groups the first 64 bytes into four keys. The final solution queries the PE section table instead of hardcoding that location.

## 4. Extracting the hardware fingerprint from the MiniDump

The supplied dump starts with `MDMP`; its stream directory contains a type-5 `MINIDUMP_MEMORY_LIST` with seven memory descriptors. The solution parses the descriptors and extracts the values at these virtual bases and offsets:

| Identifier | Region base | Region-relative offset | Size | Extracted value |
|---|---|---|---:|---|
| CPUID brand | `0x7FFE0000` | `0x274` | 48 | `Intel(R) Core(TM) Ultra 7 155H` plus zero padding |
| Volume serial | `0x22000000000` | `0x48` | 4 | `914c8dd4` (raw hex) |
| MachineGuid | `0x1C000000000` | `0x500` | 36 | `658c05ff-e49e-40aa-b041-9a5f56886dea` (ASCII) |
| SMBIOS UUID | `0x20000000000` | `0x55` | 16 | `3c31a857b2b700488ce185385968c8a8` (raw hex) |
| NIC MAC | `0x21000000000` | `0x198` | 6 | `52:54:00:36:e6:64` |

The fingerprint is precisely:

```python
fingerprint = cpuid48 + volume_serial4 + machine_guid36 + smbios_uuid16 + mac6
assert len(fingerprint) == 110
```

Keep the 48-byte padded CPU string, the binary byte ordering of the serial and UUID, and the 36-byte ASCII registry GUID. Converting everything into printed strings would change the cryptographic input.

## 5. Understanding `license.bin`

The 104-byte license has the following layout:

| Offset | Length | Meaning |
|---|---:|---|
| `0x00` | 4 | `MSLC` magic |
| `0x04` | 4 | version 1 (`01 00 00 00`) |
| `0x08` | 16 | stored custom MAC |
| `0x18` | 8 | stream nonce |
| `0x20` | 64 | ciphertext, eight 8-byte blocks |
| `0x60` | 8 | trailer (not used by recovered decryption path) |

Stored authentication value: `8305d5d5c5316f76bd27ddbbc5221f56`.

The core block cipher is **XTEA** (not original TEA): 32 rounds, 64-bit block, 128-bit key, round delta `0x9E3779B9`, unsigned 32-bit arithmetic and little-endian word packing. See `xtea_encrypt()` in `solve.py`.

The custom 16-byte, two-key MAC-like primitive maintains four 32-bit words `(a,b,c,d)`. For each 4-byte little-endian input word, zero-padding the final partial word:

```python
a ^= input_word
a, b = XTEA_encrypt((a,b), left_key)
c, d = XTEA_encrypt((c,d), right_key)
d ^= b
a ^= c
```

This is a challenge-specific authenticator, **not** standard HMAC.

To authenticate the hardware fingerprint, initialize the four-word state with:

```text
0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
```

Run the custom MAC over the 110 fingerprint bytes with **K2 and K3**. The output agrees byte-for-byte with the stored 16-byte value:

```text
Stored:     8305d5d5c5316f76bd27ddbbc5221f56
Calculated: 8305d5d5c5316f76bd27ddbbc5221f56
```

This provides a strong check for all extracted identifiers, field lengths, byte order, and the recovered keys.

## 6. Key derivation and plaintext recovery

Start with 16 zero bytes and MAC the three *separate* hardware groups with **K0 and K1**:

```python
state = bytes(16)
state = custom_mac(state, cpuid, K0, K1)
state = custom_mac(state, volume_serial + machine_guid, K0, K1)
state = custom_mac(state, smbios_uuid + nic_mac, K0, K1)
```

These group boundaries matter because each call pads its last partial 32-bit word independently.

```text
After CPU:       6e75487e27f3f55f92a66707fa322b63
After volume+ID: 9df5ab2c698825c8b3ea6b1bd1ab63fa
After UUID+MAC:  55e8a940fa964f9fe1c996e31f338143
```

A final transform on `(a,b,c,d)` consists of 7-bit 32-bit rotates, XORs and additions modulo `2^32`. Crucially, updates are sequential, so `c` uses the new `a` and `d` uses the new `a` and `b`:

```python
a = (rol32(a,7) ^ b) + c
b = (rol32(b,7) ^ c) + d
c = (rol32(c,7) ^ d) + a
d = (rol32(d,7) ^ a) + b
# mask each assignment to 32 bits
```

Derived stream key:

```text
3b8652afcde7de87294a383867f1a9f7
```

Read the 8-byte nonce as two little-endian 32-bit words `(v0,v1)`. For block counter `i=0..7`, compute `XTEA_encrypt(pack32le((v0+i) mod 2^32, v1), derived_key)`, then XOR that eight-byte keystream with ciphertext block `i`. This is a simple XTEA counter-like stream, not a standard TLS/DRM certificate verification protocol.

The 64-byte result consists of 38 readable flag bytes and 26 NUL padding bytes:

```text
gctf{...}
```

The flag text refers to a forged DRM certificate, but the recovered procedure itself uses a custom MAC and XTEA; it does not demonstrate an actual X.509 certificate chain.

## 7. Reproduce from the original archive

Only Python 3.9+ is required; there are no third-party dependencies, Windows processes, live endpoints or hardcoded plaintexts in the solver.

```bash
python3 solve.py mircosoft.tar.gz --save-unpacked unpacked_pe.bin
python3 test_repro.py mircosoft.tar.gz
```

You can verify unpacking separately:

```bash
tar -xzf mircosoft.tar.gz
python3 unpack.py mircosoft/mircosoft.exe -o unpacked_independent.bin
sha256sum unpacked_independent.bin unpacked_pe.bin
cmp unpacked_independent.bin unpacked_pe.bin
```

Expected proof points:

```text
Unpacked PE SHA256: 9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80
License MAC (stored):     8305d5d5c5316f76bd27ddbbc5221f56
License MAC (calculated): 8305d5d5c5316f76bd27ddbbc5221f56
Derived stream key: 3b8652afcde7de87294a383867f1a9f7
FLAG: gctf{...}
```

`test_repro.py` checks the exact recovered PE hash and flag, parses the MiniDump, and confirms that changed hardware data, changed license MAC, and a corrupted loader decoding key fail. All six offline tests pass.

**Takeaway:** the critical missing artifact was `unpacked_pe.bin`. It is now produced deterministically from the original EXE, not assumed to exist. Only *after* that step does the `.data` offset `0x3E00` make sense, and the entire path from original challenge files to accepted flag becomes independently reproducible.