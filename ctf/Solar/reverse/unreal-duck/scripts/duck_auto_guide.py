#!/usr/bin/env python3
"""UnrealDuck AUTO-решение: скрипт сам вводит код маршрута при активации зоны.

Когда игра сообщает «вход в зону маршрута R», скрипт автоматически:
- записывает 5 букв кода в массив Codes контроллера,
- вызывает ServerPushCheatLetter для каждой буквы,
после чего остаётся только довести утку до конца зоны (ServerRecordFrame).

Флаг приходит через ClientReceivePayload и сохраняется в папку out.
Запуск (игра запущена):  py duck_auto_guide.py --pid <PID>
"""
import argparse
import sys
import time
from pathlib import Path

MOD_NAME = 'UnrealDuckClient-Win64-Shipping.exe'
DESCRIPTIONS = {0: 'ILOVE ("I love")', 1: 'DUCKS (ducks)', 2: 'ALOTT (a lot)'}
ORDER = [0, 1, 2]

# ADDRESS DEFS (RVA)
WA = 0x4391670       # вход в зону (RouteActivation), args[0] содержит зону
WS = 0x4381d70       # отправка кода (ServerRecordFrame), args[0] = controller
WL = 0x4381d30       # ServerPushCheatLetter wrapper, args[0]=controller,args[1]=letterCode
WF = 0x4394600       # ClientReceivePayload (флаг)

# offets
O_PROFILE = 0x7a8
O_ROUTEID = 0x7c8
O_LOCKED = 0x800
O_CODES_PTR = 0x808
O_CODES_NUM = 0x810

SCRIPT = r'''
'use strict';
const MOD = 'UnrealDuckClient-Win64-Shipping.exe';
const ORDER = __ORDER__;      // [0,1,2]
const ROUTE_CODES = __CODES__;// {'0':'ILOVE',...}

const WA=0x4391670, WS=0x4381d70, WL=0x4381d30, WF=0x4394600;
const OP=0x7a8, ORT=0x7c8, OLCK=0x800, OCP=0x808, OCN=0x810;

let base=null, prologueOk=false, controller=null;

for (const m of Process.enumerateModules())
  if (m.name.toLowerCase()===MOD.toLowerCase()){ base=m.base; break; }

if (!base){ send({type:'ready',ok:false,msg:'модуль не найден'}); }
else {
  try { const a=base.add(WF); const s=Array.from(new Uint8Array(a.readByteArray(4)));
        prologueOk = s.length===4 && s[0]===0x40 && s[1]===0x55 && s[2]===0x56 && s[3]===0x57;
  } catch(e){}
  send({type:'ready',ok:prologueOk,base:String(base),msg:prologueOk?'хук+инжект готов':'сигнатура не совпала'});
}

function watch(rva,cb){ try{ Interceptor.attach(base.add(rva),{onEnter:cb}); }catch(e){ send({type:'err',msg:String(e)}); } }

let done = new Set();

// ServerPushCheatLetter(this=controller, letterCode byte)
const pushLetter = prologueOk ? new NativeFunction(base.add(WL), 'void', ['pointer','uint8']) : null;

function inject(controller, str){
  // обязательные условия
  try {
    const p=controller.add(OP).readPointer();
    if (p.isNull()){ send({type:'llog',msg:'нет профиля — пропуск'}); return false; }
  } catch(e){ send({type:'llog',msg:'профиль не читается: '+e}); return false; }

  const vals = [];
  for (const ch of str) vals.push(ch.charCodeAt(0)-65); // A=0..Z=25

  try {
    const codesPtr = controller.add(OCP).readPointer();
    // пишем буквы в массив Codes
    for (let i=0;i<vals.length;i++) codesPtr.add(i).writeU8(vals[i]);
    controller.add(OCN).writeS32(vals.length);   // count = 5
    controller.add(OLCK).writeU8(0);             // unlocked
    // шлём каждую букву как ServerPushCheatLetter
    if (pushLetter) {
      for (let i=0;i<vals.length;i++) pushLetter(controller, vals[i]);
    }
  } catch(e){ send({type:'err',msg:'inject: '+e}); return false; }
  return true;
}

watch(WA, function(args){
  try {
    const zone = args[0];
    const routeId = zone.add(0x2c0).readU8();
    if (!(routeId in ROUTE_CODES)) { send({type:'zonelog',routeId:routeId,note:'не игровой маршрут'}); return; }
    if (done.has(routeId)) return;
    // controller берём из аргумента зоны? аргумент в RouteActivation это зона(профиль), НЕ player.
    // controller надёжно получаем из ServerRecordFrame/Letter. Здесь — берём последний известный.
    if (!controller){ send({type:'zonelog',routeId:routeId,note:'controller ещё не известен, жду'}); return; }
    const code = ROUTE_CODES[routeId];
    if (inject(controller, code)) {
      send({type:'injected',routeId:routeId,code:code});
    }
  } catch(e){ send({type:'err',msg:'wa:'+e}); }
});

// запоминаем controller из вызова ServerRecordFrame (это точно player)
watch(WS, function(args){ controller = args[0]; 
  try {
    const cnt=controller.add(OCN).readS32(); let l='';
    if(cnt>0&&cnt<=5){const p=controller.add(OCP).readPointer();if(!p.isNull()){const d=p.readVolatile(cnt);l=Array.from(new Uint8Array(d)).map(c=>String.fromCharCode(65+c)).join('');}}
    send({type:'sent',routeId:(args[1].toUInt32()&255),codes:l,count:cnt});
  }catch(e){ send({type:'err',msg:'ws:'+e}); }
});

// фиксация controller и от ServerPushCheatLetter (момент ввода)
watch(WL, function(args){ if(!controller) controller=args[0]; });

// флаг
watch(WF, function(args){
  try {
    const t=args[1]; let title=''; const tn=t.add(8).readS32();
    if(tn>0&&tn<=4096&&!t.readPointer().isNull()) title=t.readPointer().readUtf16String(tn-1);
    const bp=args[2].readPointer(), nn=args[2].add(8).readS32();
    send({type:'flag',title:title,bytes:nn,data: (nn>0&&nn<4000000&&!bp.isNull())?bp.readByteArray(nn):null});
  }catch(e){ send({type:'err',msg:'flag:'+e}); }
});

// периодически подсказываем когда всё готово
setInterval(function(){
  if (done.size===ORDER.length){
    send({type:'banner',msg:'ВСЕ МАРШРУТЫ ВВЕДЕНЫ. Доведи утку до конца зоны для каждого (или ждём сервер).'});
  }
}, 5000);
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int, help='PID игры')
    p.add_argument('--out', type=Path, default=Path('duck_solution'))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if not a.pid:
        print('Передай --pid <PID игры>')
        return 2
    try:
        import frida
    except ImportError:
        print('py -m pip install frida', file=sys.stderr)
        return 2

    def banner(m):
        print('\n' + '='*58 + '\n  ' + m + '\n' + '='*58, flush=True)

    def on_message(message, data):
        if message.get('type') == 'error':
            print('JS-ошибка:', message.get('description',''), file=sys.stderr); return
        m = message.get('payload', {})
        t = m.get('type')
        if t == 'ready':
            print(('OK — ' if m.get('ok') else 'FAIL — ') + m.get('msg',''), flush=True)
            if m.get('ok'):
                banner('AUTO-ПРОХОЖДЕНИЕ. Скрипт сам введёт код, когда ты войдёшь в зону.\nМаршрут 0: ILOVE\nМаршрут 1: DUCKS\nМаршрут 2: ALOTT\nПросто ходи по арене и доводи утку до конца зоны.')
        elif t == 'zonelog':
            print(f'  [зона routeId={m.get("routeId")} {m.get("note","")}]', flush=True)
        elif t == 'injected':
            print(f'>>> КОД ВВЕДЁН для маршрута {m.get("routeId")}: {m.get("code")}\n    Теперь доведи утку до конца этой зоны (не пересекай другие зоны).', flush=True)
        elif t == 'sent':
            print(f'<<< Маршрут {m.get("routeId")} отправлен на сервер: [{m.get("codes")}] (count={m.get("count")})', flush=True)
        elif t == 'llog':
            print('  *', m.get('msg'), flush=True)
        elif t == 'banner':
            print(m.get('msg'), flush=True)
        elif t == 'flag':
            title = m.get('title',''); nb = m.get('bytes')
            blob = bytes(data or b'') if data else None
            print(f'\n!!! ПОЛУЧЕН PAYLOAD: title={title!r} bytes={nb}', flush=True)
            if blob:
                ext = '.png' if blob[:8]==b'\x89PNG\r\n\x1a\n' else ('.jpg' if blob[:2]==b'\xff\xd8' else '.bin')
                safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(title))[:40] or 'flag'
                path = a.out / f'{int(time.time())}_{safe}{ext}'
                path.write_bytes(blob)
                (a.out / (path.stem+'.json')).write_text(repr({'title':title,'bytes':nb}), encoding='utf-8')
                print('Сохранено:', path, flush=True)
            else:
                print('Сообщение сервера:', repr(title), flush=True)
        elif t == 'err':
            print('ошибка:', m.get('msg'), file=sys.stderr)

    # build JS arrays
    codes_map = {0: 'ILOVE', 1: 'DUCKS', 2: 'ALOTT'}
    codes_js = '{' + ','.join(f'"{r}":"{codes_map[r]}"' for r in ORDER) + '}'
    script_txt = SCRIPT.replace('__ORDER__', f'[{",".join(map(str,ORDER))}]').replace('__CODES__', codes_js)

    try:
        session = frida.get_local_device().attach(a.pid)
        script = session.create_script(script_txt)
        script.on('message', on_message)
        script.load()
        print('Жду входа в зону... Скрипт введёт код сам. Ctrl+C — выход.', flush=True)
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