# Not My Type: проверяем целые ETH, списываем дробные

[English](solve.md)

## 0. План

`PlatyPay` разрешает отправлять «человеческие» суммы ETH строками, например `"0.5"`. Python-бэкенд проверяет платежи, затем вызывает `settle` в Solidity. Задача — вывести из контракта с начальным балансом 100 ETH больше 20 ETH, чтобы `Setup.isSolved()` вернул true при балансе **строго ниже 80 ETH**.

Баг не в гонке. Проверка округляет сумму к целым ETH вниз, а реальное списание использует все дробные цифры. В контракте балансы типа `int256`, поэтому после списания можно получить отрицательное число.

## 1. Проверка суммы в Python

В `service/payment_backend.py` написано:

```python
def check_transfer(balance: float, amount: str):
    try:
        value = float(amount)
    except ValueError:
        return None
    if not value > 0:
        return None
    try:
        if balance < int(value):
            return None
    except (ValueError, OverflowError):
        return None
    return value
```

Дальше сервер читает количество wei и переводит их в ETH, а результат вызова `check_transfer` снова превращает в wei:

```python
balance = chal.functions.balanceOf(sender).call() / 10**18
value = check_transfer(balance, amount)
accepted = value is not None
amount_wei = int(value * 10**18) if accepted else 0
chal.functions.settle(next_id, accepted, amount_wei)
```

Что происходит при `amount="0.9"` с нулевого баланса:

```text
balance = 0.0
value = 0.9
int(value) = 0
0.0 < 0 == False   => платёж разрешён
amount_wei = 900000000000000000
```

Проблема не только в точности `float`. Основная ошибка: **проверяют одно количество, а списывают другое**.

## 2. Почему контракт принимает отрицательный баланс

`Chal.sol` хранит данные так:

```solidity
mapping(address => int256) public balanceOf;

function transfer(address to, string calldata amount) external returns (uint256 id) {
    id = nextTransferId++;
    transfers[id] = Transfer(msg.sender, to, amount, false);
    emit TransferRequested(id, msg.sender, to, amount);
}

function settle(uint256 id, bool accepted, uint256 amountWei) external {
    require(msg.sender == backend, "Only backend");
    Transfer storage t = transfers[id];
    require(!t.processed, "Already processed");
    t.processed = true;
    if (accepted) {
        balanceOf[t.from] -= int256(amountWei);
        balanceOf[t.to] += int256(amountWei);
    }
}
```

Здесь проверяют права бэкенда и то, что платёж не обработан дважды. Но проверка `balanceOf[t.from] >= amountWei` **отсутствует**. Со знакомым `uint256` вычитание из нуля бы откатилось по underflow, а `int256` позволяет законно записать `-0.9 ETH`.

## 3. Как получилось вывести деньги

Нужны два подконтрольных адреса A и B.

1. У A внутренний баланс ноль.
2. A вызывает `transfer(B, "0.9")`. Появляется `TransferRequested`.
3. Бэкенд видит `int(0.9)==0`, одобряет платёж и вызывает `settle(id,true,0.9e18)`.
4. `balanceOf(A)` становится `-0.9 ETH`, `balanceOf(B)` — `+0.9 ETH`.
5. Адрес B вызывает `withdraw(0.9 ether)` и получает настоящие ETH со счёта контракта.

`withdraw` проверяет только баланс B:

```solidity
function withdraw(uint256 amount) external {
    require(balanceOf[msg.sender] >= int256(amount), "Insufficient balance");
    balanceOf[msg.sender] -= int256(amount);
    payable(msg.sender).transfer(amount);
}
```

При старте 100 ETH и цене одной итерации 0.9 ETH потребуется **не менее 23 повторений**: после 22 останется 80.2 ETH, после 23 — 79.3 ETH. Это важно: `isSolved` сравнивает **строго меньше 80**, а не меньше или равно.

### Откуда возникают «лишние деньги»

Сумма внутренних балансов A и B остаётся нулевой: `-0.9+0.9=0`. Но B сразу выводит положительный баланс, а отрицательный долг A система не заставляет покрывать. Контракту есть чем платить, потому что Setup заранее положил 100 ETH.

## 4. Проверки для воспроизведения

```text
A: transfer(B, "0.9")
backend: TransferProcessed(id, accepted=true)
balanceOf(A) == -0.9e18
balanceOf(B) == +0.9e18
B: withdraw(0.9e18)
повторение до getBalance() < 80e18
```

Архив содержит исходные `Chal.sol` и `payment_backend.py` и команду сдачи принятого флага. Хеши транзакций и рабочий эксплойт **не приложены**. Поэтому схема выше выведена из проверки исходников, а не названа копией потерянного лога. Итоговые данные подтверждают принятый флаг.

## 5. Что не помогло бы

- Целое число ETH с пустого счёта отклоняется корректно.
- Если попытаться вызвать `settle` напрямую от игрока — `Only backend`.
- Положить свои 20 ETH в контракт не решает задачу, а увеличивает его баланс.

Нужен общий тип суммы (лучше целые wei) на всех слоях, и главная проверка `sender.balance >= amount` должна быть в `settle` на цепочке, а не только в доверенном Python-процессе.

## Исходники задания в репозитории

Оригинальные файлы задачи теперь лежат в `assets/`: [`Chal.sol`](assets/Chal.sol), [`Setup.sol`](assets/Setup.sol), [`payment_backend.py`](assets/payment_backend.py). Это исходные контракты и сервисы из раздатки. Примеры кода для воспроизведения отмечены отдельно и не выдаются за потерянный оригинальный эксплойт.
