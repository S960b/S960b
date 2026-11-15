# PlatyLocker: секретный показатель RSA лежит в публичном блокчейне

[English](solve.md)

## 0. Что требовалось

`Setup` деплоит `Chal`, а затем проверяет единственный флаг `filesRestored`:

```solidity
function isSolved() external view returns (bool) {
    return chal.filesRestored();
}
```

Внутри `Chal` есть `payRansom()` за **1000 ETH** — заведомо недостижимая сумма. Но флаг ставит не она:

```solidity
function restoreFiles(bytes32 key) external {
    require(keccak256(abi.encodePacked(key)) == keyHash, "Wrong key");
    filesRestored = true;
}
```

Значит, нужно знать `key` — 32-байтный AES-ключ жертвы. Платить не надо, надо прочитать ключ.

## 1. Как стенд создаёт секреты (`service/locker_operator.py`)

Оператор на каждую жертву генерирует свежую RSA-пару и AES-ключ:

```python
e = 65537
p, q = getPrime(512), getPrime(512)
n = p * q
d = inverse(e, (p - 1) * (q - 1))
aes_key = os.urandom(32)
c = pow(int.from_bytes(aes_key, "big"), e, n)

chal.functions.initialize(
    to_bytes(n),              # modulus
    e.to_bytes(3, "big"),     # publicExponent
    to_bytes(d),              # privateExponent  <-- секрет
    to_bytes(c),              # encryptedKey
    Web3.keccak(aes_key),     # keyHash
)
```

Ключевой момент: `d` передаётся **аргументом внешнего вызова** `initialize`. В Ethereum calldata любой транзакции публична навсегда, а контракт вдобавок складывает `d` в собственный storage. То есть секрет известен дважды.

## 2. Куда именно это записано

`operator` объявлен `immutable`, `RANSOM` — `constant`: оба живут в байткоде и слотов не занимают. Поэтому раскладка storage начинается с `bytes`-полей:

| Слот | Переменная | Тип |
|---:|---|---|
| 0 | `modulus` | `bytes` (128 байт) |
| 1 | `publicExponent` | `bytes` (3 байта) |
| 2 | `privateExponent` | `bytes` (128 байт), помечен `private` |
| 3 | `encryptedKey` | `bytes` (128 байт) |
| 4 | `keyHash` | `bytes32` |
| 5 | `filesRestored` | `bool` |

Правила EVM: если `bytes` длиннее 31 байта, в слоте лежит `2*len+1`, а сами данные — по адресу `keccak256(slot)`. Для короткого `publicExponent` (3 байта) значение хранится прямо в слоте как `len*2+1` вместе с байтами.

Отсюда два равнозначных способа достать секрет:

1. **Чтение storage** через `eth_getStorageAt` — читаем слот 2 и последующие блоки по `keccak256(2)`.
2. **Разбор calldata** транзакции `initialize` — селектор `initialize(bytes,bytes,bytes,bytes,bytes32)`, дальше четыре `bytes`-аргумента с классическими ABI-смещениями; `d` — третий.

Оба варианта — обычные операции чтения блокчейна, никакого обхода прав приложения нет.

## 3. Точные шаги

```python
# 1. адрес самой задачи
setup = w3.eth.contract(address=SETUP, abi=SETUP_ABI)
target = setup.functions.TARGET().call()

# 2. читаем bytes-поле из storage
def read_bytes(slot):
    s = int.from_bytes(w3.eth.get_storage_at(target, slot), "big")
    if s == 0:
        return b""
    if s & 1:                     # короткое значение в самом слоте
        return s.to_bytes(32, "big")[:(s - 1) // 2]
    base = int.from_bytes(Web3.keccak(slot.to_bytes(32, "big")), "big")
    body = b"".join(w3.eth.get_storage_at(target, base + i) for i in range((s // 2 + 31) // 32))
    return body[:s // 2]

n = read_bytes(0)
d = read_bytes(2)
c = read_bytes(3)
h = read_bytes(4)                  # keyHash
```

```python
# 3. расшифровка: key = c^d mod n
key = pow(int.from_bytes(c, "big"), int.from_bytes(d, "big"),
          int.from_bytes(n, "big")).to_bytes(32, "big")
assert Web3.keccak(key) == h        # сверка с keyHash из контракта
```

```python
# 4. ставим filesRestored без выкупа
chal = w3.eth.contract(address=target, abi=[RESTORE_FILES_ABI])
tx = chal.functions.restoreFiles(key).build_transaction({...})
w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction))
print(setup.functions.isSolved().call())   # True
```

Проверка `keccak256(key) == keyHash` в контракте — единственное условие, и она проходит, потому что ключ восстановлен, а не угадан.

## 4. Почему 32 байта и откуда берётся ключ

`aes_key` — это `os.urandom(32)`, а `c = aes_key^e mod n`. При возведении `c` в степень `d` по модулю `n` возвращается тот же `aes_key`. Перевод числа в 32 байта обязателен: если ключ начинается с нулевых байт, `int.to_bytes` без фиксированной длины их потеряет, и сверка с `keyHash` провалится.

## 5. Почему это не «сломанный RSA»

Факторизовать `n` не нужно и никто не факторизовал. `d` открыт не из-за слабой математики, а потому что его положили в calldata и в storage публичной цепочки. Если `d` известен, `c^d mod n` — штатная расшифровка.

## 6. Флаг

`PP{pr1v4t3_15_n0t_s…c8Gu}`

## 7. Защита

На публичной цепочке **нет тайного storage**: `private` ограничивает только доступ из Solidity, но не чтение через RPC и не calldata. Секретные компоненты (RSA `d`, AES-ключи) должны оставаться вне блокчейна; в контракте допустимо хранить только обязательство — например, `keccak256(key)`.

## 8. Что сохранилось

- `assets/`: `Chal.sol`, `Setup.sol`, `locker_operator.py` — оригинальная раздатка.
- `scripts/exploit.py` — рабочий скрипт: читает слоты 0–4, пересчитывает ключ, сверяет `keyHash`, вызывает `restoreFiles`. RPC-адрес, приватный ключ и адрес `Setup` заменены заглушками.
- Не сохранились: конкретные `n`, `d`, `c` живого инстанса и хэш транзакции — инстанс выключен. Скрипт поэтому ничего не «вспоминает», а читает всё со стенда.

## Исходники задания в репозитории

Оригинальные файлы задачи лежат в `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol), [`locker_operator.py`](assets/locker_operator.py).
