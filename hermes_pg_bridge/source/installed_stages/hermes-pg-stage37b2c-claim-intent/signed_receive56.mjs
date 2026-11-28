import {createPublicKey} from "node:crypto";
import {closeSync,fstatSync,lstatSync,openSync,readFileSync,constants} from "node:fs";
import {join} from "node:path";
import {openWire} from "./wire56.mjs";
export const SCOPE="task_b6c26aa18220412bafdbf32393f16568";
const ROOT="/home/kali/.config/bridge-task-sign56";
export function checkScope(ctx,state){
  if(!Array.isArray(ctx.taskIds)||ctx.taskIds.length!==1||ctx.taskIds[0]!==SCOPE)
    throw Error("signed_scope_invalid");
  if(!Object.prototype.hasOwnProperty.call(state.seen,SCOPE)||
     !Number.isSafeInteger(state.seen[SCOPE])||state.seen[SCOPE]<1)
    throw Error("signed_highwater_missing");
}
function privateFile(s){
  if(!s.isFile()||s.uid!==process.getuid()||s.nlink!==1||(s.mode&0o777)!==0o600||s.size>4096)
    throw Error("signed_public_key_unsafe");
}
export function loadPublic(root=ROOT){
  const d=lstatSync(root);
  if(!d.isDirectory()||d.uid!==process.getuid()||(d.mode&0o777)!==0o700)
    throw Error("signed_directory_unsafe");
  const fd=openSync(join(root,"task-public.pem"),constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  try{
    privateFile(fstatSync(fd));
    const b=readFileSync(fd);
    if(b.length>4096)throw Error("signed_public_key_size");
    const pub=createPublicKey(b);
    if(pub.asymmetricKeyType!=="ed25519")throw Error("signed_public_key_type");
    return pub;
  }finally{closeSync(fd);}
}
export function verifiedMessage(pub,dialog,msg,state){
  if(dialog.task_id!==SCOPE||msg.from!=="chatgpt"||msg.to!=="mt"||msg.kind!=="task"||
     !Number.isSafeInteger(state.seen[SCOPE])||!Number.isSafeInteger(msg.seq)||msg.seq<=state.seen[SCOPE])
    throw Error("signed_message_scope");
  const v=openWire(pub,SCOPE,msg.message_id,msg.seq,msg.body);
  // Never propagate title or other DB-controlled data into prompt.
  return Object.freeze({seq:v.seq,message_id:msg.message_id,body:v.body,from:"chatgpt",to:"mt",kind:"task"});
}