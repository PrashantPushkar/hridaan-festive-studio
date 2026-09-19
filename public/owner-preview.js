'use strict';
(async()=>{
  const status=document.getElementById('owner-preview-status');
  const token=location.hash.startsWith('#')?decodeURIComponent(location.hash.slice(1)):'';
  history.replaceState(null,'','/owner-preview');
  if(token.length<32){status.textContent='This owner link is incomplete or has expired.';return;}
  try{
    const response=await fetch('/api/owner-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    if(!response.ok)throw new Error();
    location.replace('/');
  }catch{
    status.textContent='This owner link is unavailable. Ask for a new link.';
  }
})();

