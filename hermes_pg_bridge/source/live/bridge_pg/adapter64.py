import re
def enrollment_history(page,task_id,offset,read):
    if type(task_id) is not str or not re.fullmatch(r'task_[0-9a-f]{32}',task_id):
        raise ValueError('invalid_task_id')
    if type(offset) is not int or offset<0:
        raise ValueError('invalid_offset')
    if type(page) is not dict or page.get('task_id')!=task_id or page.get('offset')!=offset:
        raise ValueError('history_binding_mismatch')
    out=dict(page)
    out.pop('enrollment_event',None)
    if offset!=0:return out
    event=read(task_id)
    if event is None:return out
    if (type(event) is not dict or set(event)!={'task_id','kind','note'}
       or event['task_id']!=task_id or event['kind']!='agent_opening_note'
       or type(event['note']) is not str or not event['note'].strip()
       or len(event['note'].encode('utf-8'))>4096):
        raise ValueError('invalid_enrollment_event')
    out['enrollment_event']=dict(event)
    return out
