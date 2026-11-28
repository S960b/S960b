"""ONE read-only local MT gateway/Git view; bounded metadata only, no polling."""
import json
import os
import stat

from .gateway_lifespan import APPENDS
from .unix_authority import SOCKETS, UnixClient


def main():
    try:
        info = APPENDS.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_uid != os.getuid() or info.st_nlink != 1):
            raise ValueError()
        sockets = {}
        for label, path in SOCKETS.items():
            item = path.lstat()
            if (not stat.S_ISSOCK(item.st_mode) or stat.S_IMODE(item.st_mode) != 0o600 or
                    item.st_uid != os.getuid() or item.st_nlink != 1):
                raise ValueError()
            sockets[label] = {'mode':'0600','uid':item.st_uid}
        view = UnixClient(SOCKETS['mt']).call({'op':'view','offset':0,'limit':1})
        from .github import sha
        sha(view['head'])
        result = {'status':'gateway_live_viewed','head':view['head'],
            'total_messages':view['total_messages'],'waiting_for':view['waiting_for'],
            'append_store':{'mode':'0600','uid':info.st_uid,'bytes':info.st_size},
            'sockets':sockets,'git_mutations':0,'delete_enabled':False}
    except Exception:
        result = {'error':'gateway_health_unconfirmed','retry_authorized':False}
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
