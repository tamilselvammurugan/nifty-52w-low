"""
Password-protect an HTML page with real encryption (AES-256-GCM, key from
PBKDF2-SHA256). The browser asks for the password and decrypts with WebCrypto.
Without the password the page content cannot be read, even from page source.
"""

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 250_000

TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>__TITLE__</title>
<style>
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#14213d;color:#fff;
 min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:#fff;color:#1f2328;border-radius:14px;padding:26px 22px;width:min(340px,88vw);box-shadow:0 10px 30px #0004}
h1{font-size:19px;margin:0 0 4px} p{font-size:13px;color:#666;margin:0 0 16px}
input[type=password]{width:100%;box-sizing:border-box;font-size:16px;padding:11px 12px;border:1px solid #ccc;border-radius:9px}
button{width:100%;margin-top:12px;padding:11px;font-size:15px;font-weight:600;border:0;border-radius:9px;background:#14213d;color:#fff;cursor:pointer}
label{display:flex;gap:6px;align-items:center;font-size:13px;color:#444;margin-top:10px}
.err{color:#b42318;font-size:13px;margin-top:8px;min-height:16px}
</style></head><body>
<form class="box" id="f">
 <h1>🔒 __TITLE__</h1><p>This report is password protected.</p>
 <input type="password" id="pw" placeholder="Enter password" autocomplete="current-password" autofocus>
 <label><input type="checkbox" id="rem"> Remember on this device</label>
 <button type="submit">Open report</button>
 <div class="err" id="err"></div>
</form>
<script>
const D={s:"__SALT__",i:"__IV__",c:"__CT__",n:__ITER__};
const b=x=>Uint8Array.from(atob(x),c=>c.charCodeAt(0));
async function open_(pw,quiet){
 try{
  const k0=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveKey"]);
  const k=await crypto.subtle.deriveKey({name:"PBKDF2",salt:b(D.s),iterations:D.n,hash:"SHA-256"},k0,{name:"AES-GCM",length:256},false,["decrypt"]);
  const pt=await crypto.subtle.decrypt({name:"AES-GCM",iv:b(D.i)},k,b(D.c));
  const html=new TextDecoder().decode(pt);
  if(document.getElementById('rem')?.checked){try{localStorage.setItem('rpw',pw)}catch(e){}}
  document.open();document.write(html);document.close();
 }catch(e){
  try{localStorage.removeItem('rpw')}catch(_){}
  if(!quiet)document.getElementById('err').textContent='Wrong password. Please try again.';
 }
}
document.getElementById('f').onsubmit=e=>{e.preventDefault();document.getElementById('err').textContent='Opening…';open_(document.getElementById('pw').value)};
try{const s=localStorage.getItem('rpw');if(s)open_(s,true)}catch(e){}
</script></body></html>"""


def encrypt_page(html: str, password: str, title: str = "Daily Stock Screening Report") -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, html.encode(), None)
    e = lambda x: base64.b64encode(x).decode()
    return (TEMPLATE.replace("__TITLE__", title).replace("__SALT__", e(salt)).replace("__IV__", e(iv))
            .replace("__CT__", e(ct)).replace("__ITER__", str(ITER)))
