'use strict';
if ('scrollRestoration' in history) history.scrollRestoration='manual';
const $ = id => document.getElementById(id);
const pages = ['patch', 'tutorial', 'about'];
const state = {flavor:'vitrine', versions:{}, selected:false, valid:false, busy:false, done:false, spotify:'', checking:false};
const bridge = (action, data={}) => window.webkit?.messageHandlers.patcher.postMessage({action, ...data});
const brands = {vitrine:'Vitrine', chroma:'Chroma'};
const options = [...document.querySelectorAll('.flavor-option')];
function closeMenu(restoreFocus=false) {
  $('flavorMenu').classList.add('hidden');
  $('flavor').setAttribute('aria-expanded','false');
  if (restoreFocus) $('flavor').focus();
}
function openMenu(focusOption=false) {
  if (state.busy) return;
  $('flavorMenu').classList.remove('hidden');
  $('flavor').setAttribute('aria-expanded','true');
  if (focusOption) options.find(option=>option.dataset.flavor===state.flavor).focus();
}
function showPage(page) {
  if (!pages.includes(page)) return;
  closeMenu();
  for (const name of pages) {
    $(name+'Page').classList.toggle('hidden', name!==page);
    $(name+'Tab').classList.toggle('active', name===page);
    $(name+'Tab').setAttribute('aria-selected', String(name===page));
  }
  window.scrollTo(0,0);
  requestAnimationFrame(()=>window.scrollTo(0,0));
}
function render() {
  const f=state.flavor, brand=brands[f], version=state.versions[f];
  $('headingBrand').textContent=brand;
  $('modName').textContent=brand;
  $('modVersion').textContent=version?.version||'…';
  $('modLogo').src='assets/'+(f==='chroma'?'chroma.webp':'vitrine.png');
  $('modLogo').alt=brand+' logo';
  $('modCard').classList.toggle('vitrine', f==='vitrine');
  $('flavor').value=f;
  $('flavor').setAttribute('aria-label', 'Choose your mod. Currently '+brand);
  options.forEach(option=>option.setAttribute('aria-selected', String(option.dataset.flavor===f)));
  $('compatibility').textContent=state.spotify?'for Spotify '+state.spotify:(f==='chroma'?'Official stable release':'Free community fork');
  $('buildSource').textContent=version?(f==='chroma'?'Official Chroma release · '+version.commit:'GitHub main · '+version.commit.slice(0,7)):'Checking latest '+brand+'…';
  $('patch').disabled=!state.valid||state.busy;
  $('patch').textContent=state.busy?'Working…':state.done?'Patch again':'Patch';
  $('drop').disabled=state.busy;
  $('flavor').disabled=state.busy;
  $('refresh').disabled=state.busy||state.checking;
  if(state.busy) closeMenu();
}
function status(text,error=false,settled=false) {
  $('progressArea').classList.remove('hidden');
  $('progressArea').classList.toggle('error',error);
  $('progressArea').classList.toggle('settled',settled);
  $('status').textContent=text;
}
function hideResult() { $('reveal').classList.add('hidden'); $('resultHint').classList.add('hidden'); }
window.receive=function(e) {
  switch(e.type) {
    case 'flavor':
      state.flavor=e.flavor; state.done=false; hideResult();
      if(!state.busy) $('progressArea').classList.add('hidden');
      break;
    case 'selected':
      state.selected=true; state.valid=false; state.done=false; state.spotify='';
      $('fileName').textContent=e.name; $('fileName').title=e.name;
      $('fileDetail').textContent='Checking your IPA…';
      $('drop').classList.add('selected');
      $('uploadIcon').classList.add('hidden'); $('spotifyIcon').classList.remove('hidden'); hideResult();
      break;
    case 'validated':
      state.valid=true; state.spotify=e.version;
      $('fileDetail').textContent='Spotify '+e.version+' · '+Math.round(e.size/1048576)+' MB';
      status('Ready to patch. Click the file card to choose another IPA.',false,true);
      break;
    case 'version':
      state.versions[e.flavor]=e;
      $('about'+(e.flavor==='chroma'?'Chroma':'Vitrine')).textContent=e.version;
      break;
    case 'checking': state.checking=true; $('refresh').textContent='Checking…'; break;
    case 'checked': state.checking=false; $('refresh').textContent='Check for updates ↻'; break;
    case 'versionError':
      if(!state.versions[e.flavor]) state.versions[e.flavor]={version:'Latest',commit:'Version check unavailable'};
      break;
    case 'busy':
      state.busy=true; state.done=false; $('cancel').classList.toggle('hidden', e.operation!=='build'); hideResult();
      status(e.operation==='validate'?'Checking your Spotify IPA…':'Preparing your patch…');
      break;
    case 'status': status(e.message); break;
    case 'error': status(e.message,true,true); if(!state.valid) $('fileDetail').textContent='Choose a decrypted Spotify IPA'; break;
    case 'cancelled': status('Cancelled. You can patch again whenever you’re ready.',false,true); break;
    case 'done':
      state.done=true;
      status('✓ Patched and verified. Your IPA is ready.',false,true);
      $('reveal').classList.remove('hidden'); $('resultHint').classList.remove('hidden');
      break;
    case 'idle': state.busy=false; $('cancel').classList.add('hidden'); break;
  }
  render();
};
for(const page of pages) $(page+'Tab').addEventListener('click',()=>showPage(page));
for(const button of document.querySelectorAll('[data-page]')) button.addEventListener('click',()=>showPage(button.dataset.page));
$('drop').addEventListener('click',()=>bridge('choose'));
$('patch').addEventListener('click',()=>bridge('patch'));
$('cancel').addEventListener('click',()=>bridge('cancel'));
$('reveal').addEventListener('click',()=>bridge('reveal'));
$('refresh').addEventListener('click',()=>bridge('refresh'));
$('flavor').addEventListener('click',()=> $('flavorMenu').classList.contains('hidden')?openMenu():closeMenu());
$('flavor').addEventListener('keydown', e=>{
  if(['ArrowDown','ArrowUp'].includes(e.key)) { e.preventDefault(); openMenu(true); }
});
for(const option of options) {
  option.addEventListener('click',()=>{ if(!state.busy) { bridge('flavor',{value:option.dataset.flavor}); closeMenu(true); } });
  option.addEventListener('keydown',e=>{
    let index=options.indexOf(option);
    if(e.key==='ArrowDown') index=(index+1)%options.length;
    else if(e.key==='ArrowUp') index=(index-1+options.length)%options.length;
    else if(e.key==='Home') index=0;
    else if(e.key==='End') index=options.length-1;
    else return;
    e.preventDefault(); options[index].focus();
  });
}
document.addEventListener('keydown', e=>{if(e.key==='Escape' && !$('flavorMenu').classList.contains('hidden')) { e.preventDefault(); closeMenu(true); }});
document.addEventListener('pointerdown', e=>{if(!$('modCard').contains(e.target)) closeMenu();});
$('modCard').addEventListener('focusout',()=>setTimeout(()=>{if(!$('modCard').contains(document.activeElement)) closeMenu();},0));
$('getIPA').addEventListener('click',()=>showPage('tutorial'));
for(const a of document.querySelectorAll('a[href]')) a.addEventListener('click',e=>{e.preventDefault();bridge('external',{url:a.href});});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('drop',e=>e.preventDefault());
render();
