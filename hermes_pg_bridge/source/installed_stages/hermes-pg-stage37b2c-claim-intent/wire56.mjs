import {verify} from "node:crypto";
const MAX=32768;
function utf8(s){
  if(typeof s!=="string")throw Error("string");
  const b=Buffer.from(s,"utf8");
  if(b.toString("utf8")!==s)throw Error("invalid Unicode");
  return b;
}
export function openWire(pub,tid,mid,actualSeq,wire){
  if(typeof tid!=="string"||!/^task_[0-9a-f]{32}$/.test(tid)||tid.length!==37||
    typeof mid!=="string"||!(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/).test(mid)||/[\r\n]/.test(mid)||
    !Number.isSafeInteger(actualSeq)||actualSeq<1)throw Error("identity");
  if(pub?.asymmetricKeyType!=="ed25519")throw Error("key");
  if(typeof wire!=="string"||wire.length>MAX||utf8(wire).length>MAX)throw Error("size");
  const i=wire.indexOf("\n");
  if(i<0||i>140)throw Error("header");
  const m=/^HPG1 ([1-9][0-9]{0,15}) (0|[1-9][0-9]{0,15}) ([A-Za-z0-9_-]{86})$/.exec(wire.slice(0,i));
  if(!m||m[0]!==wire.slice(0,i))throw Error("format");
  const seq=Number(m[1]),ver=Number(m[2]);
  if(!Number.isSafeInteger(seq)||seq!==actualSeq||!Number.isSafeInteger(ver)||ver<0)throw Error("counter");
  const sig=Buffer.from(m[3],"base64url");
  if(sig.length!==64||sig.toString("base64url")!==m[3])throw Error("signature encoding");
  const body=wire.slice(i+1);
  if(!body||body.includes("\x00"))throw Error("body");
  const data=Buffer.concat([Buffer.from("hermes-pg/task-wire/v1\x00","ascii"),Buffer.from([tid,mid,m[1],m[2]].join("\n")+"\n","ascii"),utf8(body)]);
  if(!verify(null,data,pub,sig))throw Error("signature");
  return Object.freeze({body,seq,expectedVersion:ver});
}
