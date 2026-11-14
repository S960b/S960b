# Just in Time

[English](README.md) | [Решение](solve.ru.md)

- **Категория:** blockchain
- **Сложность:** 1
- **Исходники:** Solidity `Chal.sol`, `Setup.sol`
- **Флаги:** 1 принято (`PP{...}`)

Хранилище утверждает, что пароль нигде не записан. На деле он однозначно получается из публичных данных развёртывания.

**Причина:** Конструктор считает `password = uint256(keccak256(abi.encodePacked(block.timestamp, block.number, msg.sender)))` и хранит только повторный Keccak от password. Контракт `Chal` создаёт `Setup`, поэтому `msg.sender` при создании — адрес Setup, а не кошелёк игрока. Timestamp и номер блока публичны. Значит пароль полностью воспроизводим.

[Подробный ход решения](solve.ru.md).
