'use strict';
const $ = id => document.getElementById(id);
const state = { festivals: [], selected: null, active: null, timer: null, busy: false, ai: false, history: [], dirty: false, logo: null, options: [], month: new Date().getFullYear()===2026?new Date().getMonth():0 };
const statuses = { queued:'In the queue', generating:'Creating', ready_for_review:'Ready for review', approved:'Approved', rejected:'Set aside', failed:'Could not generate' };
const fmt = { instagram_feed:[1080,1350], instagram_stories:[1080,1920], whatsapp_chat:[1080,1350], whatsapp_status:[1080,1920] };
let toastTimer;
function toast(message, error=false) { $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,6500); }
async function api(path, options={}) {
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const data=await response.json().catch(()=>({detail:'The studio could not reach the server.'}));
  if(!response.ok){ if(response.status===401){$('studio').hidden=true;$(state.accountMode?'account-panel':'login-panel').hidden=false;} throw new Error(typeof data.detail==='string'?data.detail:'Please check the form and try again.'); }
  return data;
}
function busy(value){ state.busy=value; $('generate').disabled=value||Boolean(state.user?.free_generation_used)||Boolean(state.user?.reserved_run); ['save','approve','download','copy','reject'].forEach(id=>$(id).disabled=value); }
function occasionDetails(){
  const f=state.selected;if(!f)return;
  $('festival-description').textContent=f.blurb;
  $('greeting-date').value=f.date;dateGuidance();
  const date=new Date(f.date+'T12:00:00').toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'});
  $('festival-date').textContent=date+' · '+(f.regions||[]).join(', ');
  $('festival-note').textContent=f.date_note||'';
  $('festival-sources').replaceChildren();
  for(const [index,url] of (f.source_urls||[]).entries()){
    if(!url.startsWith('https://'))continue;
    const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=index?' · Additional source':'Calendar source';$('festival-sources').append(link);
  }
}
function searchable(value){return value.normalize('NFKD').toLowerCase().replace(/['’]/g,'').replace(/[^a-z0-9]+/g,' ').trim();}
function festivals(){
  const query=searchable($('festival-search').value);
  const all=state.festivals.filter(f=>query.split(/\s+/).every(word=>searchable([f.name,f.category,...(f.aliases||[]),...(f.regions||[])].join(' ')).includes(word)));
  const preferred=['Diwali','Holi','Onam','Eid al-Fitr','Christmas','Ganesh Chaturthi'];
  const ordered=query?all:[...all].sort((a,b)=>{let ai=preferred.indexOf(a.name),bi=preferred.indexOf(b.name); return (ai<0?99:ai)-(bi<0?99:bi)||a.name.localeCompare(b.name);});
  $('festival-list').replaceChildren();
  $('festival-count').textContent=all.length+' of '+state.festivals.length+' occasions';
  for(const festival of ordered){const button=document.createElement('button');button.type='button';button.className='festival';button.textContent=festival.name;button.setAttribute('aria-pressed',String(state.selected?.id===festival.id));button.addEventListener('click',()=>{state.selected=festival;festivals();occasionDetails();});$('festival-list').append(button);}
  if(!ordered.length){const p=document.createElement('p');p.textContent='No matching occasion. Try a name, state or community.';$('festival-list').append(p);}
}
function updateDimensions(channel=$('channel').value){$('dimensions').textContent=fmt[channel].join(' × ');}
function imageUrl(run){return '/api/greetings/'+encodeURIComponent(run.id)+'/image.png?v='+run.revision;}
function showRun(run){
  showCost(run);
  $('generation-progress').textContent=run.generate_text===false?'Creating your artwork with standard wording…':'Writing your greeting and planning the artwork…';
  state.active=run;state.dirty=false;
  const generating=['queued','generating'].includes(run.status),ready=['ready_for_review','approved'].includes(run.status),hasImage=run.revision>0;
  $('empty-canvas').hidden=generating||hasImage;$('generation-state').hidden=!generating;$('preview').hidden=!hasImage||generating;
  $('edit-form').hidden=!ready;$('review-empty').hidden=ready;
  if(hasImage){$('preview').src=imageUrl(run);$('preview').alt=run.headline+' — '+run.brand;}
  updateDimensions(run.channel);$('canvas-caption').textContent=run.festival_name+' · '+statuses[run.status];
  $('image-source').hidden=!hasImage;$('image-source').textContent=run.image_source==='ai'?'AI artwork':'Illustrated template';
  if(ready){$('edit-date').value=run.greeting_date||state.festivals.find(f=>f.id===run.festival_id)?.date||'';$('edit-date-placement').value=run.date_placement||'none';$('run-settings').textContent=(run.persona||'warm')+' · '+(run.sender_type||'company')+(run.date_is_custom?' · Custom date':'');for(const key of ['headline','tagline','subline','message'])$(key).value=run[key]||'';$('review-status').textContent=statuses[run.status];$('warnings').hidden=!run.warnings?.length;$('warnings').textContent=(run.warnings||[]).join(' ');}
  if(run.status==='failed')toast(run.error||'Generation did not finish. Please try again.',true);
  busy(generating);
}
async function refreshHistory(){
  state.history=await api('/greetings');$('history-count').textContent=state.history.length+' greeting'+(state.history.length===1?'':'s');
  const container=$('history');container.replaceChildren();
  if(!state.history.length){const p=document.createElement('p');p.className='library-empty';p.textContent='Your saved greetings will live here, ready when you need them.';container.append(p);return;}
  for(const run of state.history){
    const button=document.createElement('button');button.className='history-card';button.type='button';button.setAttribute('aria-label','Open '+run.festival_name+', '+statuses[run.status]);
    if(run.revision>0){const img=document.createElement('img');img.src=imageUrl(run);img.loading='lazy';img.alt=run.headline;button.append(img);}else{const placeholder=document.createElement('span');placeholder.className='history-placeholder';placeholder.textContent=run.status==='failed'?'!':'✦';button.append(placeholder);}
    const name=document.createElement('strong');name.textContent=run.festival_name;const meta=document.createElement('small');meta.textContent=statuses[run.status];button.append(name,meta);
    button.addEventListener('click',()=>{if(state.busy){toast('Please wait for the current action to finish.');return;}if(state.dirty&&!confirm('Discard the unsaved text changes?'))return;clearTimeout(state.timer);showRun(run);if(['queued','generating'].includes(run.status))poll(run.id);document.querySelector('.canvas-panel').scrollIntoView({behavior:'smooth',block:'center'});});container.append(button);
  }
}
async function poll(id){
  state.timer=setTimeout(async()=>{try{const run=await api('/greetings/'+encodeURIComponent(id));showRun(run);if(['queued','generating'].includes(run.status)){poll(id);}else{await refreshAccount();await refreshHistory();toast(run.status==='failed'?'Generation failed. Please try again.':'Your greeting is ready to review.',run.status==='failed');}}catch(error){busy(false);$('generation-state').hidden=true;toast(error.message,true);}},2500);
}
async function loadStudio(){
  $('studio').hidden=false;$('login-panel').hidden=true;$('account-panel').hidden=true;state.festivals=await api('/festivals');state.selected=state.festivals.find(f=>f.name==='Diwali')||state.festivals[0];
  const options=await api('/options');state.options=options.personas;$('persona').replaceChildren(...options.personas.map(p=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.name;return option;}));personaDescription();festivals();occasionDetails();await refreshHistory();
  const pending=state.history.find(run=>['queued','generating'].includes(run.status));if(pending){showRun(pending);poll(pending.id);}
}
async function boot(){
  try{const session=await api('/session');state.accountMode=Boolean(session.account_mode);state.user=session.user;updateAccount();state.ai=session.ai_available;$('logout').hidden=!session.authenticated;$('render-mode').value=state.ai?'ai':'template';$('render-mode').options[0].disabled=!state.ai;if(!state.ai)$('generation-hint').textContent='Illustrated templates are ready. AI artwork becomes available after your personal key is configured.';if(session.authenticated)await loadStudio();else if(state.accountMode){await loadAccountPanel();}else $('login-panel').hidden=false;}
  catch(error){$('boot').textContent='The studio could not load. Refresh the page to try again.';toast(error.message,true);return;}$('boot').hidden=true;
}
$('festival-search').addEventListener('input',festivals);$('channel').addEventListener('change',()=>{if(!state.active)updateDimensions();});
$('generate-form').addEventListener('submit',async event=>{event.preventDefault();if(!state.selected||state.busy)return;if(state.dirty&&!confirm('Discard the unsaved text changes and create a new greeting?'))return;busy(true);clearTimeout(state.timer);try{const run=await api('/greetings',{method:'POST',body:JSON.stringify({festival_id:state.selected.id,brand:$('brand-name').value.trim(),channel:$('channel').value,use_ai:$('render-mode').value==='ai',generate_text:$('generate-text').checked,sender_type:$('sender-type').value,persona:$('persona').value,logo_base64:$('sender-type').value==='company'?state.logo:null,greeting_date:$('greeting-date').value,date_placement:$('date-placement').value})});showRun(run);poll(run.id);await refreshHistory();}catch(error){busy(false);toast(error.message,true);}});
async function saveEdits(){if(!state.active)return;const payload=Object.fromEntries(['headline','tagline','subline','message'].map(key=>[key,$(key).value]));payload.greeting_date=$('edit-date').value||null;payload.date_placement=$('edit-date-placement').value;const run=await api('/greetings/'+state.active.id,{method:'PUT',body:JSON.stringify(payload)});showRun(run);await refreshHistory();return run;}
$('edit-form').addEventListener('input',()=>state.dirty=true);
$('edit-form').addEventListener('submit',async event=>{event.preventDefault();busy(true);try{await saveEdits();toast('Preview updated.');}catch(error){toast(error.message,true);}finally{busy(false);}});
$('approve').addEventListener('click',async()=>{if(!$('edit-form').reportValidity())return;busy(true);try{if(state.dirty)await saveEdits();showRun(await api('/greetings/'+state.active.id+'/approve',{method:'POST'}));await refreshHistory();toast('Approved and saved. Ready to download.');}catch(error){toast(error.message,true);}finally{busy(false);}});
$('reject').addEventListener('click',async()=>{busy(true);try{showRun(await api('/greetings/'+state.active.id+'/reject',{method:'POST'}));await refreshHistory();toast('Greeting set aside. Your account allowance is unchanged.');}catch(error){toast(error.message,true);}finally{busy(false);}});
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('message').value);toast('Message copied.');}catch{$('message').focus();$('message').select();toast('Select and copy the highlighted message.');}});
$('download').addEventListener('click',async()=>{if(!$('edit-form').reportValidity())return;busy(true);try{if(state.dirty)await saveEdits();const response=await fetch(imageUrl(state.active));if(!response.ok)throw new Error('Could not download the image.');const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=state.active.festival_name.replace(/[^a-z0-9]+/gi,'-')+'-'+state.active.channel+'.png';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('Your PNG is ready.');}catch(error){toast(error.message,true);}finally{busy(false);}});
$('login-form').addEventListener('submit',async event=>{event.preventDefault();try{await api('/login',{method:'POST',body:JSON.stringify({password:$('password').value})});$('password').value='';await loadStudio();}catch(error){toast(error.message,true);}});
$('logout').addEventListener('click',async()=>{try{await api('/logout',{method:'POST'});clearTimeout(state.timer);location.reload();}catch(error){toast(error.message,true);}});
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
function dateGuidance(){
 const f=state.selected;if(!f)return;
 const custom=$('greeting-date').value!==f.date;
 $('date-guidance').textContent=(custom?'Custom date — check it against your local calendar. ':!f.source_urls?.length?'Date needs local verification before sharing. ':'')+(f.date_note||'');
}
function personaDescription(){const p=state.options.find(p=>p.id===$('persona').value);$('persona-description').textContent=p?.description||'';}
$('persona').addEventListener('change',personaDescription);
$('greeting-date').addEventListener('change',dateGuidance);
$('sender-type').addEventListener('change',()=>{const individual=$('sender-type').value==='individual';$('brand-label').textContent=individual?'Your name · optional':'Company name';$('brand-name').required=!individual;$('brand-name').placeholder=individual?'Leave blank for an unsigned greeting':'Company or organisation name';$('brand-name').value='';$('logo-field').hidden=individual;});
let logoVersion=0;
$('remove-logo').addEventListener('click',()=>{logoVersion++;state.logo=null;$('logo-file').value='';$('logo-preview').removeAttribute('src');$('logo-preview-wrap').hidden=true;});
$('logo-file').addEventListener('change',async()=>{const version=++logoVersion;state.logo=null;$('logo-preview-wrap').hidden=true;const file=$('logo-file').files[0];if(!file)return;if(file.size>2*1024*1024||!['image/png','image/jpeg','image/webp'].includes(file.type)){toast('Choose a PNG, JPEG or WebP logo under 2 MB.',true);$('logo-file').value='';return;}try{const url=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});if(version!==logoVersion)return;state.logo=url.split(',')[1];$('logo-preview').src=url;$('logo-preview-wrap').hidden=false;}catch{toast('The logo could not be read.',true);}});
function showCost(run){
 const c=run.cost;$('run-cost').hidden=false;$('cost-breakdown').replaceChildren();
 if(!c){$('cost-total').textContent=['queued','generating'].includes(run.status)?'Cost calculated after generation':'Cost not recorded';$('cost-note').textContent='Usage tracking applies to new generations.';return;}
 const money=c.inr==null?'INR unavailable':new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',minimumFractionDigits:2,maximumFractionDigits:2}).format(c.inr);
 $('cost-total').textContent=c.status==='template'?'₹0.00 · No API calls':(c.status==='partial'?'Known subtotal: ':'API cost: ')+money;
 $('cost-note').textContent=c.status==='template'?'Illustrated template. Editing the date or text adds no API cost.':(c.status==='partial'?'Some API usage is missing; the total may be higher. ':'Calculated from actual reported tokens. ')+(c.fx?'Converted at ₹'+c.fx.usd_inr.toFixed(4)+' / USD ('+c.fx.date+'). ':'Exchange rate unavailable. ')+ 'Excludes taxes and billing adjustments.';
 for(const call of c.calls||[]){const p=document.createElement('p');p.textContent=call.model+' · '+(call.usage?call.usage.input_tokens+' in / '+call.usage.output_tokens+' out':'usage unavailable')+' · '+(call.usd==null?'unpriced':'$'+call.usd.toFixed(6));$('cost-breakdown').append(p);}
 const p=document.createElement('p');p.textContent='Recorded USD '+Number(c.usd).toFixed(6)+' · Pricing checked '+c.pricing_date;$('cost-breakdown').append(p);
}
const months=Array.from({length:12},(_,m)=>new Date(2026,m,1).toLocaleDateString('en-IN',{month:'long'}));
$('calendar-month').replaceChildren(...months.map((name,m)=>{const o=document.createElement('option');o.value=m;o.textContent=name+' 2026';return o;}));
function calendarChoose(f){state.selected=f;$('festival-search').value=f.name;festivals();occasionDetails();$('calendar-dialog').close();$('greeting-date').focus();}
function calendarRender(){
 $('calendar-month').value=state.month;$('previous-month').disabled=state.month===0;$('next-month').disabled=state.month===11;
 const query=searchable($('calendar-filter').value);
 const entries=state.festivals.filter(f=>Number(f.date.slice(5,7))===state.month+1&&query.split(/\s+/).every(word=>searchable([f.name,...(f.aliases||[]),...(f.regions||[])].join(' ')).includes(word))).sort((a,b)=>a.date.localeCompare(b.date)||a.name.localeCompare(b.name));
 $('calendar-count').textContent=entries.length+' occasions in '+months[state.month]+'. Amber entries need date verification.';
 const grid=$('calendar-grid');grid.replaceChildren();
 for(const name of ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']){const el=document.createElement('div');el.className='calendar-weekday';el.textContent=name;grid.append(el);}
 const offset=(new Date(2026,state.month,1).getDay()+6)%7;
 for(let i=0;i<offset;i++){const el=document.createElement('div');el.className='calendar-day empty';grid.append(el);}
 for(let day=1;day<=new Date(2026,state.month+1,0).getDate();day++){
  const iso='2026-'+String(state.month+1).padStart(2,'0')+'-'+String(day).padStart(2,'0');const cell=document.createElement('div');cell.className='calendar-day';const now=new Date();if(now.getFullYear()===2026&&now.getMonth()===state.month&&now.getDate()===day)cell.classList.add('today');const time=document.createElement('time');time.dateTime=iso;time.textContent=day;const dayEntries=entries.filter(f=>f.date===iso);const dayButton=document.createElement('button');dayButton.type='button';dayButton.className='calendar-date-button';dayButton.setAttribute('aria-label','Choose occasion on '+iso);dayButton.disabled=!dayEntries.length;dayButton.append(time);dayButton.addEventListener('click',()=>{if(dayEntries.length===1){calendarChoose(dayEntries[0]);}else{state.calendarDay=iso;calendarRender();$('calendar-agenda').scrollIntoView({block:'start',behavior:'smooth'});}});cell.append(dayButton);
  for(const f of entries.filter(f=>f.date===iso)){const button=document.createElement('button');button.type='button';button.className='calendar-event'+(!f.source_urls?.length?' uncertain':'');button.textContent=f.name;button.title=f.date_note||'';button.addEventListener('click',()=>calendarChoose(f));cell.append(button);}grid.append(cell);
 }
 const agenda=$('calendar-agenda');agenda.replaceChildren();
 const agendaEntries=state.calendarDay?entries.filter(f=>f.date===state.calendarDay):entries;
 if(state.calendarDay){const reset=document.createElement('button');reset.type='button';reset.className='secondary';reset.textContent='Showing '+state.calendarDay+' · Show whole month';reset.addEventListener('click',()=>{state.calendarDay=null;calendarRender();});agenda.append(reset);}
 for(const f of agendaEntries){const item=document.createElement('article');item.className='agenda-item';const time=document.createElement('time');time.dateTime=f.date;time.textContent=new Date(f.date+'T12:00:00').toLocaleDateString('en-IN',{day:'numeric',month:'short'});const body=document.createElement('div');const name=document.createElement('h3');name.textContent=f.name;const region=document.createElement('p');region.textContent=(f.regions||[]).join(' · ');const note=document.createElement('p');note.textContent=f.date_note;body.append(name,region,note);for(const url of f.source_urls||[]){if(!url.startsWith('https://'))continue;const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener';link.textContent='View date source';body.append(link);}if(!f.source_urls?.length){const warning=document.createElement('span');warning.className='reference-status';warning.textContent='Date not yet independently verified';body.append(warning);}const choose=document.createElement('button');choose.type='button';choose.className='secondary';choose.textContent='Create greeting';choose.setAttribute('aria-label','Create greeting for '+f.name);choose.addEventListener('click',()=>calendarChoose(f));item.append(time,body,choose);agenda.append(item);}
 if(!entries.length){const p=document.createElement('p');p.textContent='No matching occasions in this month.';agenda.append(p);}
}
$('open-calendar').addEventListener('click',()=>{state.calendarDay=null;calendarRender();$('calendar-dialog').showModal();});
$('close-calendar').addEventListener('click',()=>$('calendar-dialog').close());
$('calendar-month').addEventListener('change',()=>{state.calendarDay=null;state.month=Number($('calendar-month').value);calendarRender();});
$('calendar-filter').addEventListener('input',()=>{state.calendarDay=null;calendarRender();});
$('previous-month').addEventListener('click',()=>{state.calendarDay=null;state.month=Math.max(0,state.month-1);calendarRender();});
$('next-month').addEventListener('click',()=>{state.calendarDay=null;state.month=Math.min(11,state.month+1);calendarRender();});

function updateAccount(){
 $('account-summary').hidden=!state.user;
 if(!state.user)return;
 $('account-name').textContent='Welcome, '+state.user.full_name;
 $('account-consent').checked=Boolean(state.user.marketing_consent);
 $('free-status').textContent=state.user.free_generation_used?'Your free AI creation has been used. You can still edit and download your saved greeting.':state.user.reserved_run?'Your creation is in progress.':'1 free AI creation available. Illustrated templates do not use it.';
 $('early-access').hidden=!state.user.free_generation_used;
 $('early-access').disabled=Boolean(state.user.early_access_at);
 $('early-access').textContent=state.user.early_access_at?'You’re on the early access list':'Get more creations · Join early access';
 $('generate').disabled=state.busy||Boolean(state.user.free_generation_used)||Boolean(state.user.reserved_run);
}
async function refreshAccount(){if(!state.accountMode)return;const result=await api('/session');state.user=result.user;updateAccount();}
async function loadAccountPanel(){
 $('account-panel').hidden=false;
 const options=await api('/accounts/options');
 const names=new Intl.DisplayNames(['en'],{type:'region'});
 $('country').replaceChildren(...options.countries.map(c=>{const o=document.createElement('option');o.value=c.code;o.textContent=names.of(c.code)+' (+'+c.calling_code+')';return o;}));$('country').value='IN';
 $('register-submit').disabled=!options.registration_ready;$('signin-submit').disabled=!options.mail_ready;
 $('account-availability').textContent=options.registration_ready?'':'Email registration is being prepared. Please check back shortly.';
 if(options.terms_url?.startsWith('https://')){$('account-terms').href=options.terms_url;$('account-terms-wrap').hidden=false;}
 if(options.privacy_url?.startsWith('https://'))$('account-privacy').href=options.privacy_url;
 if(new URLSearchParams(location.search).get('verification')==='invalid')$('email-status').textContent='This link has expired or was already used. Request a new sign-in link.';
}
$('show-register').addEventListener('click',()=>{$('register-form').hidden=false;$('signin-form').hidden=true;});
$('show-signin').addEventListener('click',()=>{$('register-form').hidden=true;$('signin-form').hidden=false;});
async function emailRequest(path,payload,button){
 button.disabled=true;
 try{const result=await api(path,{method:'POST',body:JSON.stringify(payload)});state.linkEmail=payload.email;$('email-status').textContent=result.message;$('resend-link').hidden=false;$('resend-link').disabled=true;setTimeout(()=>$('resend-link').disabled=false,60000);}
 catch(error){$('email-status').textContent=error.message;}
 finally{if(button.id!=='resend-link')button.disabled=false;}
}
$('register-form').addEventListener('submit',async event=>{event.preventDefault();const query=new URLSearchParams(location.search);await emailRequest('/accounts/register',{full_name:$('full-name').value,email:$('register-email').value,country:$('country').value,phone:$('phone').value,marketing_consent:$('marketing-consent').checked,...Object.fromEntries(['utm_source','utm_medium','utm_campaign'].map(k=>[k,(query.get(k)||'').slice(0,100)]))},$('register-submit'));});
$('signin-form').addEventListener('submit',async event=>{event.preventDefault();await emailRequest('/accounts/email-link',{email:$('signin-email').value},$('signin-submit'));});
$('resend-link').addEventListener('click',()=>emailRequest('/accounts/email-link',{email:state.linkEmail},$('resend-link')));
$('early-access').addEventListener('click',async()=>{try{const result=await api('/accounts/early-access',{method:'POST'});await refreshAccount();toast(result.message);}catch(error){toast(error.message,true);}});
$('account-consent').addEventListener('change',async()=>{try{await api('/accounts/consent',{method:'PUT',body:JSON.stringify({marketing_consent:$('account-consent').checked})});await refreshAccount();toast('Email preference saved.');}catch(error){toast(error.message,true);}});
boot();

