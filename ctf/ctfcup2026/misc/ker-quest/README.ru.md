# ker quest

[English](README.md) | [Решение](solve.ru.md)

- **Соревнование:** CTFCUP 2026, квалификация 31 октября
- **Баллы:** 400
- **Задание:** https://game.ctfcup.online/tasks/ker_quest
- **Направление:** misc, криптография, форензика, реверс, OSINT
- **Флаг:** `ctfcup{...}` (скрыт)

Девять слоёв, каждый даёт пароль к следующему архиву: вопрос про первый CTF автора, PNG с неверной высотой в IHDR, пазл из шестидесяти фрагментов, MIPS-crackme, произнесённый по буквам пароль, Base64 в картинке, один пакет из десяти тысяч, фотография отеля в Акихабаре, константа из обфусцированного JavaScript и в конце ROT13.

Все архивы это WinZip AES с HMAC, поэтому каждый пароль цепочки подтверждается криптографически до перехода к следующему слою. Именно это делает райтап воспроизводимым: пароли `2015`, `myfirstsolvedforensicstask2016`, `takearesttraveller`, `easyreverseforeveryone`, `th3r34l0n3p4ssw0rd1337`, `ohnicetoseeyouin2036year`, `glinomesieforever`, `superhotelpremierakihabara` и `junkjunkjunkjunkcode` перепроверены на исходных архивах. Пакету 1337 из дампа нужен ещё свой пароль `1337`, прежде чем он отдаст следующий.

Полная цепочка в [solve.ru.md](solve.ru.md); `scripts/restore_ihdr.py` воспроизводит починку PNG и сверяет CRC шапки.
