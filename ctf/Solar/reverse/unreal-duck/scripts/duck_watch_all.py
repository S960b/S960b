#!/usr/bin/env python3
"""UnrealDuck решение для оператора: пассивный гид по игре.

Скрипт следит за игрой (Frida) и подсказывает, ЧТО ДЕЛАТЬ:
- какой код ввести (ILOVE / DUCKS / ALOTT) и в какой зоне,
- засчитался ли маршрут, сколько осталось.

Ничего не меняет и в сеть не шлёт. Только читает состояние.
Запуск на Windows (игра запущена):  py duck_solve_guide.py --pid <PID>
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

MOD_NAME = 'UnrealDuckClient-Win64-Shipping.exe'
PAYLOAD_RVA = 0x04394600
PAYLOAD_TAG = [0x40, 0x55, 0x56, 0x57]
WATCH_ACTIVATION = 0x4391670     # вход в зону (RunActivation)
WATCH_COMPLETION = 0x4391890     # завершение/сдача маршрута
WATCH_LETTER = 0x4381d30         # ServerPushCheatLetter (буква)
WATCH_SEND = 0x4381d70           # ServerRecordFrame (факт отправки кода)
WATCH_PAYLOAD = 0x4394600        # ClientReceivePayload (флаг!)

# Коды для каждого маршрута (0,1,2). Итог: "I LOVE DUCKS A LOT"
ROUTE_CODES = {0: 'ILOVE', 1: 'DUCKS', 2: 'ALOTT'}
ORDER = [0, 1, 2]                # порядок прохождения

SCRIPT = r'''
'use strict';
const MOD_NAME = 'UnrealDuckClient-Win64-Shipping.exe';
const PAYLOAD_RVA = 0x04394600;
const PAYLOAD_TAG = [0x40,0x55,0x56,0x57];
const WA = 0x4391670, WC = 0x4391890, WL = 0x4381d30, WS = 0x4381d70;
let mod = null, baseAddr = null, found = false;
for (const m of Process.enumerateModules()) {
  if (m.name.toLowerCase() === MOD_NAME.toLowerCase()) { mod = m; break; }
}
if (!mod) { send({type:'ready', ok:false, msg:'модуль игры не найден'}); }
else {
  baseAddr = mod.base;
  try {
    const a = baseAddr.add(PAYLOAD_RVA);
    const sig = Array.from(new Uint8Array(a.readByteArray(4)));
    found = sig.length===4 && sig.every((v,i)=>v===PAYLOAD_TAG[i]);
  } catch(e){}
  send({type:'ready', ok:found, msg:(found?'хук готов':'сигнатура не совпала'), base:String(baseAddr)});
}

function on(a, cb){ try{ Interceptor.attach(a, { onEnter: cb }); }catch(e){ send({type:'err', msg:String(e)}); } }

if (baseAddr) {
  // вход в стартовую зону маршрута
  on(baseAddr.add(WA), function(args){
    try{
      const routeId = args[0].add(0x2c0).readU8();
      send({type:'activation', routeId: routeId});
    }catch(e){ send({type:'err', msg:String(e)}); }
  });
  // завершение маршрута
  on(baseAddr.add(WC), function(args){
    try{
      const routeId = args[0].add(0x2c0).readU8();
      send({type:'completion', routeId: routeId});
    }catch(e){ send({type:'err', msg:String(e)}); }
  });
  // нажатая буква (код буквы 0-25)
  on(baseAddr.add(WL), function(args){
    try{
      const code = args[1].toUInt32() & 0xff;
      send({type:'letter', code: code});
    }catch(e){ send({type:'err', msg:String(e)}); }
  });
  // отправка кода серверу (маршрут сдан с буквами)
  on(baseAddr.add(WS), function(args){
    try{
      const routeId = args[1].toUInt32() & 0xff;
      const cnt = args[0].add(0x810).readS32();
      let letters = '';
      if (cnt>0 && cnt<=5){ const p=args[0].add(0x808).readPointer(); if(!p.isNull()){ const d=p.readVolatile(cnt); letters=Array.from(new Uint8Array(d)).map(c=>String.fromCharCode(65+c)).join(''); } }
      send({type:'send', routeId: routeId, count: cnt, letters: letters});
    }catch(e){ send({type:'err', msg:String(e)}); }
  });
  // флаг от сервера
  on(baseAddr.add(PAYLOAD_RVA), function(args){
    try{
      const t=args[1];
      let title='';
      const tn=t.add(Process.pointerSize).readS32();
      if(tn>0&&tn<=4096&&!t.readPointer().isNull()) title=t.readPointer().readUtf16String(tn-1);
      const bp=args[2].readPointer(), nn=args[2].add(Process.pointerSize).readS32();
      send({type:'flag', title:title, bytes:nn, data: nn>0&&nn<4000000&&!bp.isNull()?bp.readByteArray(nn):null});
    }catch(e){ send({type:'err', msg:String(e)}); }
  });
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int, help='PID процесса игры (Get-Process | ? ProcessName -like *Shipping*)')
    p.add_argument('--out', type=Path, default=Path('duck_solution'))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if not a.pid:
        print('Передай --pid <номер процесса игры>')
        return 2
    try:
        import frida
    except ImportError:
        print('Установи: py -m pip install frida', file=sys.stderr)
        return 2

    done = set()            # какие маршруты уже сданы с правильным кодом
    need = 0                # следующий маршрут по плану
    active_route = None     # маршрут, в который мы сейчас ведём ввод

    def banner(msg):
        print('\n' + '='*56 + '\n  ' + msg + '\n' + '='*56, flush=True)

    def on_message(message, data):
        nonlocal done, need, active_route
        if message.get('type') == 'error':
            print('JS-ошибка:', message.get('description',''), file=sys.stderr); return
        m = message.get('payload', {})
        t = m.get('type')
        if t == 'ready':
            print(('OK — ' if m.get('ok') else 'FAIL — ') + m.get('msg',''), flush=True)
            if m.get('ok'):
                banner('План: в каждой зоне введи код и доведи утку до конца.\nМаршрут 0: ILOVE\nМаршрут 1: DUCKS\nМаршрут 2: ALOTT')
        elif t == 'activation':
            r = m.get('routeId')
            if r in (0,1,2) and r not in done:
                active_route = r
                print(f'>>> Вошёл в зону МАРШРУТ {r}. Введи код: {ROUTE_CODES[r]}\n    (стой на месте, набери эти 5 букв, затем иди до конца зоны)', flush=True)
            elif r in done:
                print(f'(зона маршрута {r} уже сдана, игнорируй)', flush=True)
        elif t == 'letter':
            c = m.get('code')
            if active_route is not None and 0 <= c <= 25:
                ch = chr(65 + c)
                if ch == 'W' or (ch in ('A','S','D') and ch in ROUTE_CODES[active_route]):
                    print(f'   буква {ch} — это клавиша движения, старайся вводить не сдвигая утку', flush=True)
        elif t == 'completion':
            r = m.get('routeId')
            # не выводим спам: напомним, что маршрут сброшен
            if active_route is not None:
                print(f'>>> Пересёк границу зоны — маршрут {active_route} сброшен (код утерян, если не успел довести).', flush=True)
                active_route = None
            # продолжим: если все маршруты сданы, конец
        elif t == 'send':
            r, cnt, letters = m.get('routeId'), m.get('count'), m.get('letters', '')
            if cnt >= 5:
                print(f'>>> Отправлен код маршрута {r}: [{letters}]', flush=True)
                if letters == ROUTE_CODES.get(r):
                    done.add(r)
                    print(f'   ✓ КОД МАРШРУТА {r} ВЕРНЫЙ!', flush=True)
                    if r == need:
                        while need in done: need += 1
                    if done >= set(ORDER):
                        banner('ВСЕ 3 МАРШРУТА СДАНЫ. ЖДЁМ, ПОКА СЕРВЕР ПРИШЛЁТ ФЛАГ.')
                    else:
                        banner(f'Маршрут {r} сдан. Теперь иди к зоне МАРШРУТ {need} и введи {ROUTE_CODES[need]}')
                else:
                    print('   ✗ Код неверный — вероятно, буквы сбились (пересёк не ту зону). Попробуй ещё раз.', flush=True)
        elif t == 'flag':
            title = m.get('title', '')
            nb = m.get('bytes')
            blob = bytes(data or b'') if data else None
            print(f'\n!!! ПОЛУЧЕН PAYLOAD от сервера: title={title!r} bytes={nb}', flush=True)
            if blob:
                ext = '.png' if blob[:8]==b'\x89PNG\r\n\x1a\n' else ('.jpg' if blob[:2]==b'\xff\xd8' else '.bin')
                safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(title))[:40] or 'flag'
                path = a.out / f'{time.time():.0f}_{safe}{ext}'
                path.write_bytes(blob)
                (a.out / (path.stem + '.json')).write_text(repr({'title': title, 'bytes': nb}), encoding='utf-8')
                print(f'Сохранено: {path}', flush=True)
            else:
                print(f'Хранитель сообщения: title = {title!r}', flush=True)
        elif t == 'err':
            print('ошибка:', m.get('msg'), file=sys.stderr)

    try:
        session = frida.get_local_device().attach(a.pid)
        script = session.create_script(SCRIPT)
        script.on('message', on_message)
        script.load()
        print('Наблюдаю игру. Проходи маршруты в порядке 0→1→2. Ctrl+C — выход.', flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nОстановлено')
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())