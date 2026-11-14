# Just in Time: пароль из блока создания контракта

[English](solve.md)

## 0. План

Цель: вывести 100 ETH из `Chal`, для чего требуется неизвестный `uint256 password`. Программа называет пароль случайным, но код конструктора строит его только из `block.timestamp`, `block.number` и `msg.sender`. Все три величины можно восстановить из истории сети.

План: определить адрес `TARGET`, найти блок развёртывания, правильно установить адрес `msg.sender` при создании, повторить `abi.encodePacked` и Keccak, вызвать `withdraw` и проверить `Setup.isSolved()`.

## 1. Источник: конструктор и критерий победы

```solidity
constructor() payable {
    uint256 password = uint256(keccak256(
        abi.encodePacked(block.timestamp, block.number, msg.sender)
    ));
    passwordHash = keccak256(abi.encodePacked(password));
}

function withdraw(uint256 password) external {
    require(keccak256(abi.encodePacked(password)) == passwordHash, "Wrong password");
    payable(msg.sender).transfer(address(this).balance);
}
```

Именно `Setup` создаёт `Chal`:

```solidity
constructor() payable {
    require(msg.value == 100 ether, "Must send 100 ether");
    TARGET = new Chal{value: 100 ether}();
}

function isSolved() public view returns (bool) {
    return address(TARGET).balance == 0;
}
```

Значит `msg.sender` внутри `Chal.constructor` — **адрес контракта Setup**, а не внешний EOA игрока или деплоера. Это главный нюанс при повторении хэша.

## 2. Какие данные нужны

После получения адреса Setup из интерфейса инстанса читаем `TARGET()`. Затем находим транзакцию, в которой Setup был создан, и блок, где этот вызов выполнился. Значение `block.timestamp` и `block.number` соответствует блоку создания **внутреннего** `Chal`; в данном `Setup.constructor` это тот же блок создания Setup.

Типы параметров `abi.encodePacked` имеют значение:

- `block.timestamp`: `uint256` = 32 байта big-endian;
- `block.number`: `uint256` = 32 байта big-endian;
- `msg.sender`: `address` = 20 байт без заполнения;
- результат — ровно 84 байта.

Нельзя использовать `sha3_256` из стандартного Python как подмену Ethereum Keccak-256: это разные схемы финализации.

## 3. Воспроизводимый расчёт

Ниже самостоятельный калькулятор, построенный из исходного `Chal.sol`; оригинальный скрипт живого прогона не сохранился.

```python
from eth_utils import keccak

def calculate_password(timestamp: int, block_number: int, setup_address: str) -> int:
    address_bytes = bytes.fromhex(setup_address.removeprefix("0x"))
    assert len(address_bytes) == 20
    packed = (
        timestamp.to_bytes(32, "big")
        + block_number.to_bytes(32, "big")
        + address_bytes
    )
    assert len(packed) == 84
    return int.from_bytes(keccak(packed), "big")
```

Проверить себя можно ещё до отправки транзакции: вычислить `keccak(password.to_bytes(32,"big"))` и сравнить с неизменяемым `passwordHash`, если восстановить его из bytecode/трассы создания. Публичного геттера для `private immutable` нет, поэтому проще провести `eth_call` к `withdraw` с вычисленным паролем (без изменения состояния), а затем реальную транзакцию.

## 4. Последний вызов

Победный путь:

```text
Setup.TARGET() -> Chal address
block(timestamp, number) + Setup address
    -> keccak256(84 packed bytes) -> password
Chal.withdraw(password) -> transfer(all 100 ETH)
Setup.isSolved() -> true
```

`transfer` пересылает весь остаток, а не фиксированную выплату. Других условий для `isSolved` нет.

## 5. Подтверждение и ограничения

В переданном агентом перечне зафиксирован принятый флаг `PP{...}`. В исходном ZIP есть оба Solidity-файла, но не сохранены адрес конкретного инстанса, номер блока, значение найденного пароля или хэш транзакции. Поэтому код выше — **воспроизводимый расчёт по исходнику**, а не якобы скопированный из несохранившегося терминала.

## 6. Вывод

Публичные поля блока не создают секрет. Хэширование известного набора исходных данных скрывает лишь арифметическое выражение, но не делает пароль непредсказуемым.
