import fs from 'node:fs';
import path from 'node:path';
export const SCOPE='task_b6c26aa18220412bafdbf32393f16568';
const MID=/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
function need(b,code){if(!b)throw Error(code);}
export function isRevoked(root,tid,seq,mid){
  // Caller must hold receiver exclusion lock. No ENOENT fallback for root.
  need(tid===SCOPE && Number.isSafeInteger(seq) && seq>=1 &&
       typeof mid==='string' && MID.test(mid) && !/[\r\n]/.test(mid),'invalid_identity');
  const dir=fs.lstatSync(root);
  need(dir.isDirectory() && dir.uid===process.getuid() && (dir.mode&0o777)===0o700,'unsafe_directory');
  const file=path.join(root,String(seq)+'.revoked');
  let fd;
  try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);}
  catch(e){if(e.code==='ENOENT')return false;throw e;}
  try{
    const st=fs.fstatSync(fd);
    need(st.isFile()&&st.uid===process.getuid()&&st.nlink===1&&
         (st.mode&0o777)===0o600&&st.size<=256,'unsafe_revocation');
    const b=Buffer.alloc(st.size);
    let n=0;while(n<b.length){const r=fs.readSync(fd,b,n,b.length-n,n);need(r>0,'revocation_size');n+=r;}
    need(b.every(x=>x<=127),'corrupt_revocation');
    const a=b.toString('ascii').split('\n');
    need(a.length===4&&a[0]===tid&&a[1]===String(seq)&&MID.test(a[2])&&
         !/[\r\n]/.test(a[2])&&a[3]==='','corrupt_revocation');
    return true;
  }finally{fs.closeSync(fd);}
}
