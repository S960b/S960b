# Referral: реферал подменяет адрес реализации прокси

[English](solve.md)

## 0. План

В `PlatyVault v2` появилась реферальная программа, а сама логика спрятана за самописным прокси. `Setup` кладёт **100 ETH** в прокси через `deposit(0)`. Чтобы выиграть, нужно сделать `address(TARGET).balance == 0`. Обычное `withdraw` не поможет, потому что деньги принадлежат записи `balances[Setup]`, не аккаунту игрока.

Уязвимость — **совпадение слота 0** у двух разных контрактов, исполняемых вместе через `delegatecall`.

## 1. Где именно совпали слоты

В `Proxy.sol`:

```solidity
address public implementation;  // slot 0
address public admin;           // slot 1
```

В `Chal.sol`:

```solidity
address public latestReferrer;    // slot 0
mapping(address => uint256) public balances;  // slot 1
mapping(address => uint256) public referrals; // slot 2
```

`Proxy.fallback` делает:

```solidity
let ok := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
```

При таком вызове исполняется код `Chal`, но **storage, address(this), msg.value и баланс принадлежат прокси**. Значит инструкция записи `latestReferrer=referrer` меняет в `Proxy` именно `implementation`.

## 2. Публичный путь к перезаписи

Функция внесения депозита:

```solidity
function deposit(address referrer) external payable {
    require(referrer != msg.sender, "No self referrals");
    balances[msg.sender] += msg.value;
    if (referrer != address(0)) {
        referrals[referrer]++;
        latestReferrer = referrer;
    }
}
```

Условие запрещает назначать самого себя рефералом, но не требует, чтобы реферал был известным пользователем. Можно передать **адрес собственного контракта E**, вызвав `deposit(E)` из другого адреса A. При этом `E != A`, `E != 0` и значение `msg.value=0` допускается.

После этого `Proxy.implementation` в слоте 0 заменится на E. Прокси начнёт делегировать вызовы на чужой код.

## 3. Зачем отдельный исполняемый контракт

Пример минимальной реализации, достаточной по логике исходника:

```solidity
contract Drain {
    function drain(address payable recipient) external {
        recipient.transfer(address(this).balance);
    }
}
```

Эта реализация приведена для воспроизведения и **не объявляется историческим контрактом, использованным в решении**.

Порядок:

1. Развернуть `Drain`, получить адрес E.
2. Через адрес прокси с ABI `Chal` выполнить `deposit(E)` от A.
3. Убедиться, что slot 0 прокси стал равен E.
4. Вызвать `drain(recipient)` **на адресе прокси**, только ABI взять от `Drain`.
5. `delegatecall` выполнит функцию в контексте прокси; `address(this).balance` покажет его 100 ETH и отправит их получателю.

Почему не надо вызывать `Drain` напрямую: тогда `address(this)` означал бы адрес самого `Drain`, у которого нет тех 100 ETH. Весь смысл в исполнении кода через прокси.

## 4. Почему контракт был уязвим сразу после развёртывания

`Setup.constructor` создаёт прокси, затем:

```solidity
TARGET = Chal(address(proxy));
TARGET.deposit{value: 100 ether}(address(0));
```

Здесь нулевой реферал не вызывает записи `latestReferrer`, поэтому указатель реализации ещё не меняется. Деньги остаются в прокси, а баланс учтён за адресом Setup. После вредоносного `deposit(E)` указатель начинает вести на наш контракт, и можно вывести средства, минуя обычную проверку `balances[msg.sender]` в `withdraw`.

## 5. Проверка и ограничения

`Setup.isSolved()` проверяет остаток ETH в прокси, а не состояние mapping и не адрес реализации. Поэтому после полного вывода получаем true.

В ZIP лежат оригинальные `Proxy.sol`, `Chal.sol` и `Setup.sol`, а также команда отправки принятого флага. Нет исходного использованного контракта, хешей транзакций и calldata. Приведённый `Drain` — компактная реконструкция из подтверждённого кода, не выдаём её за исходный эксплойт.

## 6. Защита

Использовать EIP-1967/UUPS с отдельным implementation slot, проверять совместимость layout и жёстко разграничивать обновление кода и пользовательские бизнес-поля. Реферальная логика не должна иметь способ менять адрес исполняемого кода.
