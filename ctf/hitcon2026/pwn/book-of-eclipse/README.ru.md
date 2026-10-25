# Book of Eclipse (pwn)

- **Платформа:** HITCON CTF 2026
- **Категория:** Pwn (ядро)
- **Очки / Решений:** 156 очков
- **Дата:** 2026-10-24
- **Remote:** `<host>:13370` (TCP, обёртка с PoW и скачиванием эксплойта, QEMU)
- **Флаг:** `hitcon{...}` (замаскирован)
- **Файлы:** `assets/bzImage`, `assets/initramfs.cpio`, `assets/kernel.config`, `assets/run.sh`, `assets/solve_pow.py`
- **Описание (выведено из бокса):** разовый драйвер ядра позволяет перевернуть любой один бит `IA32_FMASK`; получи root и прочитай `/flag.txt`.

Классический kernel pwn «переверни один привилегированный бит один раз». Драйвер отдаёт
`ioctl(0x41004500, buf); buf[0]` выбирает бит 0..31, и драйвер один раз выполняет `btc` +
`wrmsr` для `IA32_FMASK` (0xC0000084). Переворот бита 10 (DF) убирает DF из маски syscall, так
что userland `std` доходит до ядра, и `rep movs` внутри usercopy идёт назад. Два syscall,
`uname` (утечка KASLR) и `adjtimex` (перезапись стека), дают ROP-цепочку в ядре даже при
включённых `pti=on`, `kaslr`, SMEP и SMAP.

Файлы:

- `solve.md` - полный райтрайт, включая тупики (английский)
- `solve.ru.md` - тот же райтрайт на русском (рабочая копия)
- `scripts/exploit5.c` - финальный эксплойт (статический, clang `-O2 -no-pie`)
- `scripts/remote_run.py` - PoW + загрузка URL + прогон сессии
- `scripts/solve_pow.py` - солвер PoW (как выдавался)
- `evidence/FINAL_run_flag.txt` - захваченный успешный прогон на remote
- `evidence/System.map` - символы ядра, восстановленные из `bzImage`
- `evidence/stage1_kaslr.c` - ранний зонд утечки KASLR (тупиковая ветка)