"""Reuse exact Taskboard frontend source with PG-specific labels & success checks.

Run ONLY on a separate installed v2 directory, never in live git_dialog tree.
"""
from pathlib import Path
import sys


def main():
    src=Path('/home/kali/agent-dialog-beta/code/src/git_dialog')
    dst=Path(__file__).resolve().parents[1]/'bridge_pg/static'
    assert src.resolve()!=dst.resolve()
    dst.mkdir(parents=True,exist_ok=True)
    for name in ('taskboard.html','taskboard.css','taskboard.js'):
        raw=(src/name).read_text()
        if name=='taskboard.js':
            replacements={
                'В Git нет сообщений':'В PostgreSQL нет сообщений',
                'Подтверждено Git':'Подтверждено PostgreSQL',
                'подтверждено Git':'подтверждено PostgreSQL',
                'в Git.':'в PostgreSQL.',
                'только Git':'только PostgreSQL',
                'Запись в Git':'Запись в PostgreSQL',
                'Git содержит событие':'PostgreSQL содержит событие',
                'Git-событие':'событие в PostgreSQL',
                'GitHub API: данные недоступны':'PostgreSQL · без лимитов GitHub',
                'GitHub full-snapshot read':'лишнее чтение PostgreSQL',
                'Загружаем историю из GitHub':'Загружаем историю из PostgreSQL',
                "'HEAD: ' + d.head":"'VERSION: ' + d.head",
                "last.kind === 'return' || last.kind === 'close'":"last.kind === 'return' || last.kind === 'close' || last.returned",
                "response.returned_message_id !== last.message_id ||\n         response.created !== true":"response.returned_message_id !== last.message_id ||\n         response.created !== true",
                'Git-ветка будет УДАЛЕНА. История может сохраниться в Git-объектах и backups.':'Диалог будет архивирован, история сохранится в PostgreSQL и резервных копиях.',
                'НАВСЕГДА удалить Git-ветку':'Архивировать диалог',
                'Ветка удалена: ':'Диалог архивирован: ',
            }
            for before,after in replacements.items():
                if before not in raw:
                    # Wording changes in future revisions should be reviewed.
                    if before.startswith(('last.kind',"'HEAD:",'НАВСЕГДА')):
                        raise SystemExit('required upstream JS pattern changed: '+before)
                    continue
                raw=raw.replace(before,after)
            # Return-turn already-visible returned flag must suppress duplicate action.
            raw=raw.replace("if (!last || last.kind === 'return' || last.kind === 'close' ||", "if (!last || last.returned || last.kind === 'return' || last.kind === 'close' ||")
            raw=raw.replace("if (response.deleted !== true || response.remote_absent !== true)",
                            "if (response.deleted !== true || response.remote_absent !== true)")
        if name=='taskboard.html':
            raw=raw.replace('Git', 'PG').replace('GitHub','PostgreSQL')
        (dst/name).write_text(raw)
    print('STATIC_ASSETS_PORTED',len(list(dst.iterdir())))

if __name__=='__main__':main()
