"""Read/ack this native GUI's bounded text queue; never invoke a game controller."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import currency_wars_artifacts as artifacts


def load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2_000_000:raise ValueError('留言记录路径或大小异常')
    return json.loads(path.read_text(encoding='utf8'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','read','ack'])
    parser.add_argument('--gui-run-dir',required=True);parser.add_argument('--chat-id',required=True)
    parser.add_argument('--message-id');parser.add_argument('--reply-file')
    args=parser.parse_args();runtime=Path(args.gui_run_dir).resolve();marker=artifacts.read_marker(runtime)
    process=load(runtime/'gui-process.json')
    if process.get('framework')!='Rust/Tauri' or process.get('chat_id')!=args.chat_id or artifacts.process_identity(process['pid'])!=('active','windows:'+process['creation_id']):raise ValueError('实际GUI进程或聊天归属不匹配')
    records=load(runtime/'messages.json').get('messages',[]) if (runtime/'messages.json').exists() else []
    if not isinstance(records,list) or len(records)>20 or any(item.get('chat_id')!=args.chat_id for item in records):raise ValueError('留言聊天归属或数量异常')
    path=runtime/'message-acks.json';acks=load(path).get('acks',[]) if path.exists() else []
    if not isinstance(acks,list) or len(acks)>20:raise ValueError('实际确认记录数量异常')
    if args.action!='list':
        record=next((item for item in records if item.get('id')==args.message_id),None)
        if not record:raise ValueError('留言ID不属于当前GUI')
        previous=next((item for item in acks if item.get('id')==args.message_id),{})
        ack={**previous,'id':args.message_id,'chat_id':args.chat_id,'gui_creation_id':process['creation_id'],'read_at_ms':previous.get('read_at_ms') or int(time.time()*1000)}
        if args.reply_file:
            reply=Path(args.reply_file).read_text(encoding='utf8').strip()
            if not reply or len(reply)>4000:raise ValueError('助手回复需要1–4000字')
            ack.update(reply=reply,replied_at_ms=int(time.time()*1000))
        acks=[item for item in acks if item.get('id')!=args.message_id]+[ack]
        staging=runtime/('message-acks-'+str(os.getpid())+'.staging');staging.write_text(json.dumps({'acks':acks},ensure_ascii=False),encoding='utf8');os.replace(staging,path)
    output=[]
    for record in records:
        ack=next((item for item in acks if item.get('id')==record['id'] and item.get('chat_id')==args.chat_id and item.get('gui_creation_id')==process['creation_id']),None)
        output.append({**record,**(ack or {}),'state':'replied' if ack and 'reply' in ack else 'read' if ack else 'pending'})
    print(json.dumps({'chat_id':args.chat_id,'messages':output},ensure_ascii=False))


if __name__=='__main__':main()
