# PlatyLocker: секретный RSA-показатель в публичном storage

[English](solve.md)

## 0. Что требовалось

`Setup` создаёт `Chal` с оператором, передаёт оператору генерацию RSA-пары и AES-ключа, а затем проверяет только флаг `filesRestored`. Внутри контракта есть `payRansom()` с ценой **1000 ETH**, то есть заведомо выше бюджета обычного игрока. Но для победы нет нужды платить выкуп: достаточно узнать `bytes32` ключ и вызвать `restoreFiles(key)`.

Уязвимость находится в формулировке исходника:

```solidity
bytes public modulus;
bytes public publicExponent;
bytes private privateExponent; // private, so only the contract can use it
bytes public encryptedKey;
bytes32 public keyHash;
bool public filesRestored;
```

Модификатор `private` запрещает другим Solidity-контрактам обращаться к полю через имя, но **все storage-слоты Ethereum публично читаются через RPC**.

## 1. Как появляется ключ

Из `service/locker_operator.py`:

```python
BITS = 1024
e = 65537
p, q = getPrime(BITS // 2), getPrime(BITS // 2)
n = p * q
d = inverse(e, (p - 1) * (q - 1))
aes_key = os.urandom(32)
c = pow(int.from_bytes(aes_key, "big"), e, n)
```

Оператор вызывает `initialize(n,e,d,c,keccak256(aes_key))`. То есть **секретный показатель d буквально передаётся транзакцией на контракт** и присваивается `privateExponent`. Эти данные доступны даже в аргументах транзакции создания/инициализации, не только в постоянном storage.

## 2. Раскладка storage

В `Chal.sol` первый реальный slot хранения принадлежит динамическому массиву `bytes modulus`:

| slot | Переменная |
|---:|---|
| 0 | `modulus` (dynamic bytes) |
| 1 | `publicExponent` (dynamic bytes) |
| 2 | `privateExponent` (dynamic bytes, помечен private) |
| 3 | `encryptedKey` (dynamic bytes) |
| 4 | `keyHash` |
| 5 | `filesRestored` (packed bool) |

`operator` — immutable и не занимает обычный storage-slot.

Для длинного `bytes` содержимое хранится начиная с `keccak256(slot_index_encoded_as_32_bytes)`. В основном слоте лежат метаданные длины (для длинного массива: 2*len+1). `privateExponent` в этом задании имеет ~128 байт, то есть это именно длинное содержимое.

```python
# Рабочая формула расположения байтов в EVM
from eth_utils import keccak

slot = 2
start = int.from_bytes(keccak(slot.to_bytes(32, "big")), "big")
# eth_getStorageAt(TARGET, hex(start + i), "latest"), i=0,1,2,3
```

Здесь `eth_getStorageAt` — операция чтения блокчейна, не обход прав приложения. В качестве альтернативы можно разобрать calldata к `initialize` в истории транзакций.

## 3. Расшифровка AES-ключа без выкупа

Получив `d`, `n` и `c` в big-endian:

```python
m = pow(int.from_bytes(c, "big"),
        int.from_bytes(d, "big"),
        int.from_bytes(n, "big"))
key = m.to_bytes(32, "big")
```

Почему 32 байта: оператор генерирует `os.urandom(32)`, а `m<n`; leading zero байты ключа нельзя потерять при переводе из числа.

Код `restoreFiles`:

```solidity
function restoreFiles(bytes32 key) external {
    require(keccak256(abi.encodePacked(key)) == keyHash, "Wrong key");
    filesRestored = true;
}
```

В отличие от `payRansom`, этот метод не требует оплаты. Достаточно передать восстановленный ключ; после установки `filesRestored=true` условие `Setup.isSolved()` выполнено.

## 4. Почему это не взлом RSA

Алгоритм RSA не факторизуется. Само значение `d` открыто, потому что оно находится в calldata и в EVM storage. Если известен `d`, вычисление `c^d mod n` — штатная операция расшифровки. Ошибка полностью в хранении секретного компонента на публичной цепочке.

## 5. Подтверждение и границы материала

Архив содержит `Chal.sol`, `Setup.sol`, `locker_operator.py` и команду сдачи принятого флага. Транзакция вызова и конкретные числа `n,d,c` не сохранились; поэтому приведённая формула воспроизводима на чистом экземпляре, а точный живой параметр не придуман.

## 6. Защита

На публичной цепочке **нет тайного storage**. Ни `private`, ни модификаторы доступа не скрывают записанные данные. Секретные RSA/AES ключи должны оставаться вне блокчейна. Если нужно проверять знание ключа — хранить только криптографическое обязательство/хэш.

## Исходники задания в репозитории

Оригинальные файлы задачи теперь лежат в `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol), [`locker_operator.py`](assets/locker_operator.py). Это исходные контракты и сервисы из раздатки. Примеры кода для воспроизведения отмечены отдельно и не выдаются за потерянный оригинальный эксплойт.
