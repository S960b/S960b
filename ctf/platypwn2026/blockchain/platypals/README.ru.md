# PlatyPals

[English](README.md) | [Решение](solve.ru.md)

- **Категория:** blockchain
- **Сложность:** 2
- **Исходники:** `Chal.sol`, `Setup.sol`, `box_opener.py`; direct Git-bridge success report
- **Флаги:** 1 принято (`PP{...}`)

В магазине боксов легендарный персонаж выпадает с шансом 1%, но callback покупателя позволяет отменять все неудачные результаты.

**Причина:** `buyBox` стоит 1 ETH. Бэкенд открывает бокс через `openBoxes(rolls)`, где `rolls` генерируется `secrets.randbelow(100)`, и повторяет транзакцию при `receipt.status != 1`. Если владелец — контракт, вызывается `onPalReceived(id,rarity)`. `revert` из callback откатывает **всю транзакцию**, включая `nextUnopened`, и бокс остаётся неоткрытым. Владелец отклоняет обычные редкости, пока не выпадет LEGENDARY. `sellLegendary` платит 50 ETH.

[Подробный ход решения](solve.ru.md).
