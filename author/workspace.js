(function(){
'use strict';

const M=BookModelV4;
const X=MathExpressionV4;
const C=BookCommandsV4;
const S=new BookServiceV4.BookService();
const D=DocxCoreV4;
const P=BookPaginationV4;
const session=new C.WorkspaceSession();

const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":'&#39;'}[ch]));
const clone=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
const storage={get(k,d=''){try{return localStorage.getItem(k)??d}catch{return d}},set(k,v){try{localStorage.setItem(k,v)}catch{}}};
const APP_VERSION='4.5.0-rc1';
const APP_AUTHORING_VERSION='bookwriter-4.5.0-rc1';
const PUBLIC_HMK_BASE='https://emkyma.netlify.app/';

function resolveSceneSource(value='',fallbackBase=location.href){
  const raw=String(value||'').trim();
  if(!raw)return '';
  if(/^\/?HMK(?:\/|$)/i.test(raw)){
    const publicPath=raw.replace(/^\/?HMK\/?/i,'');
    try{return new URL(publicPath,PUBLIC_HMK_BASE).href}catch{return raw}
  }
  try{return new URL(raw,fallbackBase).href}catch{return raw}
}

const state={
  view:'home',
  tab:'book',
  mode:null,
  source:null,
  report:null,
  candidateSaved:false,
  name:'',
  audit:null,
  compatibility:null,
  paginationReconciliation:null,
  assets:new Map(),
  realScenes:true,
  propertiesOpen:storage.get('bw-v4_2-properties')!=='closed',
  previewZoom:Number(storage.get('bw-v4_4-zoom')||1),
  previewZoomMode:storage.get('bw-v4_4-zoom-mode','fit'),
  previewOverflowAudit:null,
  previewOverflowAuditToken:0,
  busy:false,
  library:{selectedHandle:null,booksHandle:null,entries:[],loading:false,error:'',restored:false,sortKey:'title',sortDir:'asc',filters:{query:'',discipline:'',level:'',status:''},selectedName:''},
  docx:{mode:'create',file:null,result:null,entries:[],startKey:'',endKey:'',anchorKey:'',focusKey:'',blobUrls:new Map(),report:null,insertion:null},
  insert:{split:Number(storage.get('bw-v4_4-insert-split')||.5),bookZoom:Number(storage.get('bw-v4_4-insert-book-zoom')||.56),bookZoomMode:storage.get('bw-v4_4-insert-book-zoom-mode','fit')},
  sourceFileName:'book.json'
};

let activeRichContext=null;

const has=()=>session.hasBook();
const page=()=>has()?(session.book.pages.find(p=>p.id===session.selection.pageId)||session.book.pages[0]):null;
const item=()=>page()?.items.find(i=>i.id===session.selection.itemId)||null;
const pageIndex=()=>page()?session.book.pages.indexOf(page()):-1;
const itemIndex=()=>item()?page().items.indexOf(item()):-1;

function setStatus(text,kind=''){
  const node=$('#statusMessage');
  node.textContent=text;
  node.className=kind;
}

function modeLabel(){
  if(state.mode==='migration') return 'migration candidate';
  if(state.mode==='candidate') return 'v4 candidate';
  if(state.mode==='canonical') return 'canonical v4';
  if(state.mode==='new') return 'νέο v4';
  return '—';
}

function viewTitle(){
  if(state.view==='home')return 'Βιβλιοθήκη';
  if(state.view==='insert')return 'Παρεμβολή από Word';
  if(state.view==='docx')return 'Νέο βιβλίο από Word';
  return 'Χώρος βιβλίου';
}

function setView(name){
  state.view=name;
  $$('.workspace-view').forEach(v=>v.classList.toggle('active',v.id===name+'View'));
  if(name==='home')renderLibrary();
  renderChrome();
  closeMenus();
  if(name==='book'&&has()) renderAll();
  if(name==='insert'&&has()) requestAnimationFrame(()=>renderInsertWorkspace());
}

function modal(title,html,buttons=[{label:'OK',value:true,primary:true}]){
  return new Promise(resolve=>{
    $('#modalTitle').textContent=title;
    $('#modalBody').innerHTML=html;
    const host=$('#modalButtons');
    host.innerHTML='';
    buttons.forEach(spec=>{
      const button=document.createElement('button');
      button.textContent=spec.label;
      if(spec.primary) button.classList.add('primary');
      if(spec.danger) button.classList.add('danger');
      button.onclick=()=>{
        $('#modalBackdrop').classList.add('hidden');
        resolve(spec.value);
      };
      host.appendChild(button);
    });
    $('#modalBackdrop').classList.remove('hidden');
  });
}

async function confirmBox(title,html,label='Συνέχεια'){
  return modal(title,html,[{label:'Ακύρωση',value:false},{label,value:true,primary:true}]);
}

async function saveCurrentBook(){
  return state.mode==='migration'?saveCandidate():saveBook();
}

async function allowReplaceCurrentBook(actionLabel='άνοιγμα άλλου βιβλίου'){
  if(!has()||!session.isDirty())return true;
  const decision=await modal('Μη αποθηκευμένες αλλαγές',`<div class="info-card warn">Το ανοιχτό βιβλίο έχει μη αποθηκευμένες αλλαγές πριν από: <b>${esc(actionLabel)}</b>.</div>`,[
    {label:'Ακύρωση',value:'cancel'},
    {label:'Αποθήκευση και συνέχεια',value:'save',primary:true},
    {label:'Συνέχεια χωρίς αποθήκευση',value:'discard',danger:true}
  ]);
  if(decision==='save'){const saved=await saveCurrentBook();return saved===true&&!session.isDirty()}
  return decision==='discard';
}

function closeMenus(){
  $$('.menu').forEach(m=>m.classList.remove('open'));
}

$$('.menu').forEach(menu=>{
  menu.querySelector('.menu-button').addEventListener('click',event=>{
    event.stopPropagation();
    const wasOpen=menu.classList.contains('open');
    closeMenus();
    if(!wasOpen) menu.classList.add('open');
  });
});
document.addEventListener('click',closeMenus);

function currentSummary(){
  if(item()) return `${item().type} · ${item().id}`;
  if(page()) return `Σελίδα · ${page().id}`;
  return '—';
}

function renderChrome(){
  $('#windowBookTitle').textContent=has()?(session.book.meta?.title||state.name||'Βιβλίο'):'Χωρίς ανοιχτό βιβλίο';
  $('#windowViewTitle').textContent=viewTitle();
  $('#statusBook').textContent=has()?`${state.name||session.book.meta?.projectId||'βιβλίο'} · ${modeLabel()}`:'Χωρίς βιβλίο';
  $('#statusDirty').textContent=has()?(session.isDirty()?'Μη αποθηκευμένο':'Αποθηκευμένο'):'—';
  $('#statusDirty').className=has()?(session.isDirty()?'warn':'good'):'';
  $('#statusSelection').textContent=currentSummary();
  $('#statusSelection').className='selection-state';
  $('#statusView').textContent=viewTitle();
  $('#sceneToggleButton').textContent=`Σκηνές: ${state.realScenes?'ON':'OFF'}`;
  $('#bookView').classList.toggle('properties-hidden',!state.propertiesOpen);
  updateCommands();
}

function updateCommands(){
  $$('[data-command]').forEach(button=>button.disabled=!commandEnabled(button.dataset.command));
}

async function readNamedJson(dir,name){
  const handle=await dir.getFileHandle(name);
  const file=await handle.getFile();
  return {handle,data:JSON.parse(await file.text())};
}


const LIBRARY_DB='bookwriter-library-v1',LIBRARY_STORE='handles',LIBRARY_KEY='books-root';
function openLibraryDb(){
  return new Promise((resolve,reject)=>{const request=indexedDB.open(LIBRARY_DB,1);request.onupgradeneeded=()=>{const db=request.result;if(!db.objectStoreNames.contains(LIBRARY_STORE))db.createObjectStore(LIBRARY_STORE)};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)});
}
async function rememberLibraryHandle(handle){try{const db=await openLibraryDb();await new Promise((resolve,reject)=>{const tx=db.transaction(LIBRARY_STORE,'readwrite');tx.objectStore(LIBRARY_STORE).put(handle,LIBRARY_KEY);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error)});db.close()}catch(error){console.warn('Library handle was not persisted',error)}}
async function recalledLibraryHandle(){try{const db=await openLibraryDb(),value=await new Promise((resolve,reject)=>{const tx=db.transaction(LIBRARY_STORE,'readonly'),r=tx.objectStore(LIBRARY_STORE).get(LIBRARY_KEY);r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error)});db.close();return value}catch(error){console.warn('Library handle restore failed',error);return null}}
async function permissionFor(handle,interactive=false){if(!handle)return'denied';try{let p=await handle.queryPermission?.({mode:'readwrite'})||'prompt';if(p==='prompt'&&interactive)p=await handle.requestPermission?.({mode:'readwrite'})||'denied';return p}catch{return'denied'}}
async function normalizeLibrarySelection(selected){if(!selected)return null;if(selected.name==='books')return selected;try{return await selected.getDirectoryHandle('books')}catch{return selected}}
async function chooseLibrary(){
  if(typeof showDirectoryPicker!=='function'){modal('Βιβλιοθήκη','<div class="info-card bad">Η Βιβλιοθήκη απαιτεί Chrome ή Edge μέσω localhost.</div>');return}
  try{const selected=await showDirectoryPicker({mode:'readwrite'}),books=await normalizeLibrarySelection(selected);if(await permissionFor(books,true)!=='granted')throw Error('Δεν δόθηκε άδεια ανάγνωσης και εγγραφής.');state.library.selectedHandle=selected;state.library.booksHandle=books;state.library.error='';await rememberLibraryHandle(books);await scanLibrary();setView('home');setStatus(`Συνδέθηκε η Βιβλιοθήκη: ${books.name}.`,'good')}catch(error){if(error.name==='AbortError')return;state.library.error=error.message;renderLibrary();modal('Βιβλιοθήκη',`<div class="info-card bad">${esc(error.message)}</div>`)}
}
async function restoreLibrary(){if(state.library.restored)return;state.library.restored=true;const handle=await recalledLibraryHandle();if(!handle){renderLibrary();return}state.library.booksHandle=handle;const permission=await permissionFor(handle,false);if(permission==='granted')await scanLibrary();else{state.library.error='Ο αποθηκευμένος φάκελος χρειάζεται νέα άδεια. Πάτησε «Σύνδεση φακέλου books». ';renderLibrary()}}
function libraryBookTitle(book,folder){return String(book?.meta?.title||book?.meta?.projectId||folder||'Χωρίς τίτλο')}
function libraryDate(ms){if(!Number.isFinite(ms)||ms<=0)return'—';try{return new Intl.DateTimeFormat('el-GR',{dateStyle:'medium',timeStyle:'short'}).format(new Date(ms))}catch{return new Date(ms).toLocaleString('el-GR')}}
function libraryMetadata(book={}){const raw=book?.meta?.library||{};const tags=Array.isArray(raw.tags)?raw.tags.map(String):String(raw.tags||book?.meta?.tags||'').split(',').map(x=>x.trim()).filter(Boolean);return{discipline:String(raw.discipline||book?.meta?.discipline||'').trim(),level:String(raw.level||book?.meta?.level||'').trim(),category:String(raw.category||book?.meta?.category||'').trim(),status:String(raw.status||'').trim(),tags}}
function libraryStatusClass(value=''){const key=String(value).toLowerCase();if(key.includes('έτοι')||key.includes('ready'))return'ready';if(key.includes('αρχει')||key.includes('archive'))return'archived';if(key.includes('προσχ')||key.includes('draft')||key.includes('επεξεργ'))return'draft';return''}
function librarySortValue(entry,key){if(key==='title')return entry.title||'';if(key==='discipline'||key==='level'||key==='category'||key==='status')return entry[key]||'';if(key==='pages'||key==='items'||key==='updated')return Number(entry[key])||0;if(key==='folder')return entry.name||'';return entry[key]||''}
function libraryCompare(a,b,key,dir){const av=librarySortValue(a,key),bv=librarySortValue(b,key);let result=typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),'el',{numeric:true,sensitivity:'base'});if(!result)result=String(a.title||'').localeCompare(String(b.title||''),'el',{numeric:true,sensitivity:'base'});return dir==='asc'?result:-result}
function libraryVisibleEntries(){const f=state.library.filters,q=String(f.query||'').trim().toLocaleLowerCase('el');return state.library.entries.filter(entry=>{const text=[entry.title,entry.name,entry.discipline,entry.level,entry.category,entry.status,...(entry.tags||[])].join(' ').toLocaleLowerCase('el');return(!q||text.includes(q))&&(!f.discipline||entry.discipline===f.discipline)&&(!f.level||entry.level===f.level)&&(!f.status||entry.status===f.status)}).sort((a,b)=>libraryCompare(a,b,state.library.sortKey,state.library.sortDir))}
function libraryFilterOptions(id,values,current){const node=$(id);if(!node)return;const label=node.options[0]?.textContent||'Όλα';node.innerHTML=`<option value="">${esc(label)}</option>`+[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b,'el')).map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');node.value=current||''}
async function scanLibrary(){
  const root=state.library.booksHandle;if(!root)return renderLibrary();state.library.loading=true;state.library.error='';renderLibrary();const entries=[];
  try{if(await permissionFor(root,false)!=='granted')throw Error('Ο φάκελος Βιβλιοθήκης δεν έχει ενεργή άδεια.');for await(const [name,handle] of root.entries()){if(handle.kind!=='directory'||name.startsWith('.'))continue;try{const opened=await readNamedJson(handle,'book.json'),file=await opened.handle.getFile(),book=opened.data,canonical=book?.schemaVersion===M.SCHEMA_VERSION,meta=libraryMetadata(book);entries.push({name,handle,fileHandle:opened.handle,book,title:libraryBookTitle(book,name),canonical,pages:(book.pages||[]).length,items:(book.pages||[]).reduce((n,p)=>n+(p.items||[]).length,0),updated:file.lastModified,version:book?.meta?.authoringVersion||book?.schemaVersion||'—',paginationStatus:book?.extensions?.paginationStatus||'',discipline:meta.discipline,level:meta.level,category:meta.category,status:meta.status,tags:meta.tags,error:''})}catch(error){entries.push({name,handle,title:name,canonical:false,pages:0,items:0,updated:0,version:'—',paginationStatus:'',discipline:'',level:'',category:'',status:'',tags:[],error:error.message})}}
    state.library.entries=entries;
  }catch(error){state.library.error=error.message;state.library.entries=[]}finally{state.library.loading=false;renderLibrary()}
}
function renderLibrary(){
  const connection=$('#libraryConnection'),host=$('#libraryTableHost');if(!connection||!host)return;const root=state.library.booksHandle,filters=state.library.filters;
  const search=$('#librarySearch');if(search&&search.value!==filters.query)search.value=filters.query||'';
  libraryFilterOptions('#libraryDisciplineFilter',state.library.entries.map(x=>x.discipline),filters.discipline);libraryFilterOptions('#libraryLevelFilter',state.library.entries.map(x=>x.level),filters.level);libraryFilterOptions('#libraryStatusFilter',state.library.entries.map(x=>x.status),filters.status);
  if(!root){connection.className='library-connection info-card';connection.innerHTML='Δεν έχει συνδεθεί ακόμη φάκελος Βιβλιοθήκης. Επίλεξε τον <code>BookWriter/books</code>.';host.innerHTML='<div class="library-empty">Σύνδεσε τον φάκελο <b>books</b> για να εμφανιστούν τα βιβλία.</div>';$('#libraryResultSummary').textContent='';return}
  connection.className='library-connection info-card '+(state.library.error?'warn':'good');connection.innerHTML=`<span><b>Φάκελος:</b> <code>${esc(root.name)}</code>${state.library.error?` · ${esc(state.library.error)}`:''}</span><span class="library-count">${state.library.loading?'Ανάγνωση…':state.library.entries.length+' βιβλία'}</span>`;
  if(state.library.loading){host.innerHTML='<div class="library-empty">Ανάγνωση βιβλίων…</div>';$('#libraryResultSummary').textContent='';return}if(!state.library.entries.length){host.innerHTML='<div class="library-empty">Δεν βρέθηκε υποφάκελος με <b>book.json</b>. Δημιούργησε νέο βιβλίο ή μετέφερε εδώ έναν φάκελο βιβλίου.</div>';$('#libraryResultSummary').textContent='0 βιβλία';return}
  const rows=libraryVisibleEntries();$('#libraryResultSummary').textContent=rows.length===state.library.entries.length?`${rows.length} βιβλία`:`${rows.length} από ${state.library.entries.length}`;
  if(!rows.length){host.innerHTML='<div class="library-empty">Δεν βρέθηκε βιβλίο με τα συγκεκριμένα φίλτρα.</div>';return}
  const arrow=key=>state.library.sortKey===key?`<span class="library-sort-arrow">${state.library.sortDir==='asc'?'▲':'▼'}</span>`:'';
  const th=(key,label,cls='')=>`<th class="${cls}"><button type="button" data-library-sort="${key}">${label}${arrow(key)}</button></th>`;
  host.innerHTML=`<table class="library-table"><thead><tr>${th('title','Τίτλος')}${th('discipline','Κλάδος')}${th('level','Επίπεδο')}${th('category','Είδος')}${th('status','Κατάσταση')}${th('pages','Σελίδες','numeric')}${th('updated','Τροποποίηση')}${th('folder','Φάκελος')}<th>Ενέργειες</th></tr></thead><tbody>${rows.map(entry=>`<tr tabindex="0" class="${entry.error?'invalid ':''}${state.library.selectedName===entry.name?'selected':''}" data-library-name="${esc(entry.name)}"><td class="library-title-cell"><div class="library-title-main">${esc(entry.title)}</div><div class="library-title-sub">${entry.canonical?'canonical v4':'μη έγκυρο/παλιό'} · ${esc(entry.paginationStatus||'χωρίς πιστοποίηση')}</div></td><td>${entry.discipline?esc(entry.discipline):'<span class="library-meta-empty">—</span>'}</td><td>${entry.level?esc(entry.level):'<span class="library-meta-empty">—</span>'}</td><td>${entry.category?esc(entry.category):'<span class="library-meta-empty">—</span>'}</td><td>${entry.status?`<span class="library-status ${libraryStatusClass(entry.status)}">${esc(entry.status)}</span>`:'<span class="library-meta-empty">—</span>'}</td><td style="text-align:right">${entry.pages}</td><td>${esc(libraryDate(entry.updated))}</td><td><div>${esc(entry.name)}</div>${entry.tags?.length?`<div class="library-tags" title="${esc(entry.tags.join(', '))}">${esc(entry.tags.join(', '))}</div>`:''}</td><td class="library-actions-cell"><button class="primary" data-library-action="author" ${entry.error?'disabled':''}>Συγγραφέας</button><button data-library-action="reader" ${entry.error?'disabled':''}>Βιβλίο</button><button data-library-action="metadata" ${entry.error?'disabled':''}>Στοιχεία</button></td></tr>`).join('')}</tbody></table>`;
  $$('[data-library-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.librarySort;if(state.library.sortKey===key)state.library.sortDir=state.library.sortDir==='asc'?'desc':'asc';else{state.library.sortKey=key;state.library.sortDir=['updated','pages','items'].includes(key)?'desc':'asc'}renderLibrary()});
  $$('[data-library-name]').forEach(row=>{const entry=state.library.entries.find(x=>x.name===row.dataset.libraryName);row.onclick=event=>{if(event.target.closest('button'))return;state.library.selectedName=entry?.name||'';$$('[data-library-name]').forEach(node=>node.classList.toggle('selected',node.dataset.libraryName===state.library.selectedName))};row.ondblclick=event=>{if(!event.target.closest('button')&&entry&&!entry.error)openLibraryEntry(entry,false)};row.onkeydown=event=>{if(event.key==='Enter'&&entry&&!entry.error){event.preventDefault();openLibraryEntry(entry,false)}}});
  $$('[data-library-action]').forEach(button=>button.onclick=async event=>{event.stopPropagation();const row=button.closest('[data-library-name]'),entry=state.library.entries.find(x=>x.name===row?.dataset.libraryName);if(!entry)return;if(button.dataset.libraryAction==='metadata')await editLibraryMetadata(entry);else await openLibraryEntry(entry,button.dataset.libraryAction==='reader')});
}
async function editLibraryMetadata(entry){
  const meta=libraryMetadata(entry.book),accepted=await modal('Στοιχεία Βιβλιοθήκης',`<div class="library-metadata-form"><label class="wide">Τίτλος<input id="libraryEditTitle" value="${esc(entry.title)}"></label><label>Κλάδος<input id="libraryEditDiscipline" list="libraryDisciplineSuggestions" value="${esc(meta.discipline)}" placeholder="π.χ. Φυσική"></label><label>Επίπεδο<input id="libraryEditLevel" list="libraryLevelSuggestions" value="${esc(meta.level)}" placeholder="π.χ. Γ΄ Λυκείου"></label><label>Είδος<input id="libraryEditCategory" value="${esc(meta.category)}" placeholder="π.χ. Φυλλάδιο"></label><label>Κατάσταση<select id="libraryEditStatus"><option value=""></option>${['Προσχέδιο','Σε επεξεργασία','Έτοιμο','Αρχειοθετημένο'].map(value=>`<option ${meta.status===value?'selected':''}>${value}</option>`).join('')}</select></label><label class="wide">Λέξεις-κλειδιά<input id="libraryEditTags" value="${esc(meta.tags.join(', '))}" placeholder="χωρισμένες με κόμμα"></label><div class="wide info-card">Τα πεδία αποθηκεύονται δηλωτικά στο <code>meta.library</code> του βιβλίου και χρησιμοποιούνται για φίλτρα και ταξινόμηση.</div></div>`,[{label:'Ακύρωση',value:false},{label:'Αποθήκευση στοιχείων',value:true,primary:true}]);
  if(!accepted)return;try{const book=clone(entry.book),next={discipline:$('#libraryEditDiscipline').value.trim(),level:$('#libraryEditLevel').value.trim(),category:$('#libraryEditCategory').value.trim(),status:$('#libraryEditStatus').value.trim(),tags:$('#libraryEditTags').value.split(',').map(x=>x.trim()).filter(Boolean)};book.meta={...(book.meta||{}),title:$('#libraryEditTitle').value.trim()||entry.title,updatedAt:new Date().toISOString(),library:next};await S.saveBook(entry.handle,book,{backup:true,targetName:'book.json'});if(has()&&session.directoryHandle?.name===entry.name){session.book.meta=clone(book.meta);session.markSaved();renderAll()}await scanLibrary();setStatus(`Αποθηκεύτηκαν τα στοιχεία Βιβλιοθήκης για «${book.meta.title}».`,'good')}catch(error){modal('Στοιχεία Βιβλιοθήκης',`<div class="info-card bad">${esc(error.message)}</div>`)}
}
async function openLibraryEntry(entry,reader=false){
  if(!await allowReplaceCurrentBook(reader?'άνοιγμα βιβλίου για ανάγνωση':'άνοιγμα άλλου βιβλίου'))return;
  try{const opened=await readNamedJson(entry.handle,'book.json');if(opened.data.schemaVersion!==M.SCHEMA_VERSION){const proceed=await confirmBox('Μετάβαση σε v4',`Το βιβλίο <b>${esc(entry.title)}</b> είναι ${esc(opened.data.schemaVersion||'άγνωστης δομής')}. Θα δημιουργηθεί v4 candidate στη μνήμη χωρίς αλλαγή του book.json.`,'Δημιουργία candidate');if(!proceed)return;const migration=M.migratePagesV1(opened.data,{language:'el',includeTranslations:false});ensureMigratedLayoutDefaults(migration.book);await attachOpened(entry.handle,opened.handle,migration.book,'migration',entry.name,clone(opened.data),migration.report);setStatus('Δημιουργήθηκε migration candidate από τη Βιβλιοθήκη.','warn');if(reader){modal('Άνοιγμα βιβλίου','<div class="info-card warn">Αποθήκευσε πρώτα το candidate ως canonical v4 και μετά άνοιξέ το ως βιβλίο.</div>');return}}else{await attachOpened(entry.handle,opened.handle,opened.data,'canonical',entry.name);setStatus(`Άνοιξε από τη Βιβλιοθήκη: ${entry.title}.`,'good');if(reader)await openReader()}
  }catch(error){modal('Άνοιγμα από Βιβλιοθήκη',`<div class="info-card bad">${esc(error.message)}</div>`)}
}
async function requireLibraryBooksHandle(interactive=true){if(!state.library.booksHandle&&interactive)await chooseLibrary();if(!state.library.booksHandle)return null;if(await permissionFor(state.library.booksHandle,interactive)!=='granted'){if(interactive)await chooseLibrary();}return state.library.booksHandle}

async function loadAssets(){
  for(const url of state.assets.values()) if(String(url).startsWith('blob:')) URL.revokeObjectURL(url);
  state.assets.clear();
  if(!session.imagesDirectoryHandle||!has()) return;
  const sources=new Set();
  session.book.pages.forEach(p=>p.items.forEach(current=>{
    if(current.type==='figure'&&String(current.src||'').startsWith('images/')) sources.add(String(current.src));
  }));
  for(const src of sources){
    try{
      const handle=await session.imagesDirectoryHandle.getFileHandle(src.slice(7));
      const file=await handle.getFile();
      state.assets.set(src,URL.createObjectURL(file));
    }catch(_error){}
  }
}

function imageCandidates(src){
  return state.assets.has(src)?[state.assets.get(src)]:src?[src]:[];
}

function defaultFloatInteraction(type){
  return type==='clear'?'clear':'wrap';
}

function ensureMigratedLayoutDefaults(book){
  let changed=0;
  for(const p of book.pages||[]){
    for(const current of p.items||[]){
      current.layout=current.layout&&typeof current.layout==='object'?current.layout:{};
      if(!current.layout.floatInteraction){
        current.layout.floatInteraction=defaultFloatInteraction(current.type);
        changed++;
      }
    }
  }
  return changed;
}

function ensureNewItemLayout(current){
  current.layout=current.layout&&typeof current.layout==='object'?current.layout:{};
  if(!current.layout.floatInteraction) current.layout.floatInteraction=defaultFloatInteraction(current.type);
}

async function attachOpened(dir,fileHandle,book,mode,name,source=null,report=null){
  session.attachBook(book,{directoryHandle:dir,bookFileHandle:fileHandle,imagesDirectoryHandle:null});
  state.paginationReconciliation=reconcilePaginationCertification(session.book);
  try{session.imagesDirectoryHandle=await dir.getDirectoryHandle('images',{create:true});}catch{}
  state.mode=mode;
  state.name=name||dir.name;
  state.source=source;
  state.report=report;
  state.candidateSaved=false;
  state.sourceFileName=fileHandle?.name||'book.json';
  state.audit=M.auditIntegrity(session.book);
  state.compatibility=BookCore.auditData(session.book);
  await loadAssets();
  setView('book');
}

async function openBook(){
  if(!await allowReplaceCurrentBook('άνοιγμα άλλου βιβλίου'))return;
  try{
    const dir=await showDirectoryPicker({mode:'readwrite'});
    const opened=await readNamedJson(dir,'book.json');
    if(opened.data.schemaVersion===M.SCHEMA_VERSION){
      await attachOpened(dir,opened.handle,opened.data,'canonical',dir.name);
      const reconciled=state.paginationReconciliation;
      setStatus(reconciled?.changed?`Άνοιξε canonical v4 βιβλίο · ακυρώθηκε παλιά πιστοποίηση (${reconciled.mismatches.join(', ')}).`:'Άνοιξε canonical v4 βιβλίο.',reconciled?.changed?'warn':'good');
      return;
    }
    const proceed=await confirmBox('Μετάβαση σε v4',`<div class="info-card warn">Το <b>book.json</b> είναι <b>${esc(opened.data.schemaVersion||'άγνωστο')}</b>. Θα δημιουργηθεί ελληνικό-only v4 candidate στη μνήμη. Το production book.json δεν αλλάζει.</div>`,'Δημιουργία candidate');
    if(!proceed) return;
    const migration=M.migratePagesV1(opened.data,{language:'el',includeTranslations:false});
    const defaultsAdded=ensureMigratedLayoutDefaults(migration.book);
    migration.report.fieldMappings.push('all content items → wrap by default; explicit clear/avoid preserved');
    migration.report.layoutInteractionDefaultsAssigned=defaultsAdded;
    await attachOpened(dir,opened.handle,migration.book,'migration',dir.name,clone(opened.data),migration.report);
    setStatus(`Δημιουργήθηκε v4 migration candidate στη μνήμη · ${defaultsAdded} explicit float rules.`,'good');
  }catch(error){
    if(error.name==='AbortError') return;
    console.error(error);
    setStatus('Αποτυχία ανοίγματος: '+error.message,'bad');
    modal('Αποτυχία ανοίγματος',`<div class="info-card bad">${esc(error.message)}</div>`);
  }
}

async function openCandidate(){
  if(!await allowReplaceCurrentBook('άνοιγμα candidate'))return;
  try{
    const dir=await showDirectoryPicker({mode:'readwrite'});
    const opened=await readNamedJson(dir,'book_v4_candidate.json');
    if(opened.data.schemaVersion!==M.SCHEMA_VERSION) throw new Error('Το book_v4_candidate.json δεν είναι canonical v4.');
    await attachOpened(dir,opened.handle,opened.data,'candidate',dir.name);
    setStatus('Άνοιξε το αποθηκευμένο v4 candidate.','good');
  }catch(error){
    if(error.name==='AbortError') return;
    modal('Άνοιγμα candidate',`<div class="info-card bad">${esc(error.message)}</div>`);
  }
}

async function newBook(){
  if(!await allowReplaceCurrentBook('δημιουργία νέου βιβλίου'))return;
  const root=await requireLibraryBooksHandle(true);if(!root)return;
  const accepted=await modal('Νέο βιβλίο',`<div class="library-metadata-form"><label class="wide">Τίτλος βιβλίου<input id="newLibraryBookTitle" value="Νέο βιβλίο"></label><label>book_id<input id="newLibraryBookId" value="new_book" placeholder="new_book"></label><label>Κλάδος<input id="newLibraryDiscipline" list="libraryDisciplineSuggestions" placeholder="π.χ. Φυσική"></label><label>Επίπεδο<input id="newLibraryLevel" list="libraryLevelSuggestions" placeholder="π.χ. Γ΄ Λυκείου"></label><label>Είδος<input id="newLibraryCategory" placeholder="π.χ. Βιβλίο"></label><label>Κατάσταση<select id="newLibraryStatus"><option>Προσχέδιο</option><option>Σε επεξεργασία</option><option>Έτοιμο</option><option>Αρχειοθετημένο</option></select></label><label class="wide">Λέξεις-κλειδιά<input id="newLibraryTags" placeholder="χωρισμένες με κόμμα"></label><div class="wide info-card">Θα δημιουργηθεί υποφάκελος μέσα στη συνδεδεμένη Βιβλιοθήκη.</div></div>`,[{label:'Ακύρωση',value:false},{label:'Δημιουργία',value:true,primary:true}]);
  if(!accepted)return;const title=$('#newLibraryBookTitle')?.value.trim()||'Νέο βιβλίο',id=D.safeId($('#newLibraryBookId')?.value||'');if(!id){modal('Νέο βιβλίο','<div class="info-card bad">Χρειάζεται έγκυρο book_id.</div>');return}
  try{let exists=true;try{await root.getDirectoryHandle(id)}catch{exists=false}if(exists)throw Error('Υπάρχει ήδη ο φάκελος '+id+'.');const book=M.createBook(),library={discipline:$('#newLibraryDiscipline').value.trim(),level:$('#newLibraryLevel').value.trim(),category:$('#newLibraryCategory').value.trim(),status:$('#newLibraryStatus').value.trim(),tags:$('#newLibraryTags').value.split(',').map(x=>x.trim()).filter(Boolean)};book.meta={...(book.meta||{}),projectId:id,fileName:`/${id}/book.json`,title,library,authoringVersion:APP_AUTHORING_VERSION,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()};book.pages[0].items.push(M.normalizeItem(M.createItem('hero',{id:'hero-1',title})));ensureMigratedLayoutDefaults(book);const dir=await root.getDirectoryHandle(id,{create:true});await dir.getDirectoryHandle('images',{create:true});const fh=await BookServiceV4.writeNamed(dir,'book.json',JSON.stringify(book,null,2));await BookServiceV4.writeNamed(dir,'index.html',D.launcher(title,id,false));await BookServiceV4.writeNamed(dir,'Editor.html',D.launcher(title,id,true));await attachOpened(dir,fh,book,'canonical',id);session.markSaved();await scanLibrary();setStatus(`Δημιουργήθηκε το βιβλίο ${title} στη Βιβλιοθήκη.`,'good')}catch(error){modal('Νέο βιβλίο',`<div class="info-card bad">${esc(error.message)}</div>`)}
}

async function saveBook(){
  try{
    if(state.mode==='migration') return saveCandidate();
    if(!session.directoryHandle) session.directoryHandle=await showDirectoryPicker({mode:'readwrite'});
    session.book.meta={...(session.book.meta||{}),authoringVersion:APP_AUTHORING_VERSION,updatedAt:new Date().toISOString()};
    const targetName=state.mode==='candidate'?'book_v4_candidate.json':'book.json';
    const result=await S.saveBook(session.directoryHandle,session.book,{backup:true,targetName});
    session.markSaved();
    if(state.mode!=='candidate') state.mode='canonical';
    state.name=session.directoryHandle.name;
    renderAll();
    setStatus(`Αποθηκεύτηκε ${targetName} · ${result.bytes} bytes.`,'good');
    return true;
  }catch(error){
    if(error.name==='AbortError') return false;
    await modal('Αποτυχία αποθήκευσης',`<div class="info-card bad">${esc(error.message)}</div>`);
    return false;
  }
}

async function saveCandidate(){
  try{
    if(state.mode!=='migration') return;
    const enriched=Object.assign({},state.report||{}, {
      compatibility:state.compatibility||BookCore.auditData(session.book),
      compatibilityRenderer:{bookCoreVersion:BookCore.VERSION,directV4:true},
      layoutPolicy:{default:'wrap',mediaDefault:'float-right + wrap',explicitOverrides:['avoid','clear','wrap:false']},
      savedAt:new Date().toISOString()
    });
    const result=await S.saveCandidate(session.directoryHandle,session.book,enriched);
    state.candidateSaved=true;
    session.markSaved();
    renderAll();
    setStatus(`Αποθηκεύτηκαν ${result.candidateName}, ${result.reportName}, ${result.originalBackup}.`,'good');
    return true;
  }catch(error){
    await modal('Αποτυχία candidate',`<div class="info-card bad">${esc(error.message)}</div>`);
    return false;
  }
}

async function manualBackup(){
  try{
    const result=await S.backup(session.directoryHandle,session.book,'manual_v4');
    setStatus(`Δημιουργήθηκε backup ${result.path}.`,'good');
  }catch(error){
    modal('Backup',`<div class="info-card bad">${esc(error.message)}</div>`);
  }
}

async function closeBook(){
  if(!has())return;
  if(session.isDirty()){
    const decision=await modal('Μη αποθηκευμένες αλλαγές','<div class="info-card warn">Το βιβλίο έχει μη αποθηκευμένες αλλαγές.</div>',[
      {label:'Ακύρωση',value:'cancel'},
      {label:'Αποθήκευση και κλείσιμο',value:'save',primary:true},
      {label:'Κλείσιμο χωρίς αποθήκευση',value:'discard',danger:true}
    ]);
    if(decision==='save'){if(await saveCurrentBook())detachBook();return}
    if(decision!=='discard')return;
  }
  detachBook();
}

function detachBook(){
  for(const url of state.assets.values()) if(String(url).startsWith('blob:')) URL.revokeObjectURL(url);
  state.assets.clear();
  session.detach();
  state.mode=null;state.source=null;state.report=null;state.name='';state.candidateSaved=false;state.paginationReconciliation=null;
  hideSelectionOverlay();
  setView('home');
  setStatus('Το βιβλίο έκλεισε.');
}

function isPaginationManaged(book){
  return !!book&&(book?.extensions?.paginationRequired===true||!!book?.importManifest?.pagination);
}

function invalidatePaginationCertification(book,reason='manual-layout-change'){
  if(!isPaginationManaged(book))return;
  const now=new Date().toISOString(),previous=book?.importManifest?.pagination||{};
  book.extensions={
    ...(book.extensions||{}),
    paginationCertified:false,
    paginationStatus:'stale',
    paginationStaleAt:now,
    paginationStaleReason:String(reason||'manual-layout-change'),
    paginationCertifiedPageCount:Number(previous.outputPages)||null,
    paginationCurrentPageCount:(book.pages||[]).length
  };
}

function reconcilePaginationCertification(book){
  if(!isPaginationManaged(book))return{changed:false,reason:'not-managed'};
  const pagination=book?.importManifest?.pagination||{},audit=pagination.audit||{},current=(book.pages||[]).length;
  const reported=Number(pagination.outputPages),checked=Number(audit.pagesChecked);
  const certified=book?.extensions?.paginationCertified===true;
  const mismatches=[];
  if(Number.isFinite(reported)&&reported>0&&reported!==current)mismatches.push(`outputPages ${reported} → ${current}`);
  if(Number.isFinite(checked)&&checked>0&&checked!==current)mismatches.push(`pagesChecked ${checked} → ${current}`);
  if(certified&&mismatches.length){
    invalidatePaginationCertification(book,'stored-pagination-page-count-mismatch');
    book.extensions={...(book.extensions||{}),paginationReconciledAt:new Date().toISOString(),paginationReconciliation:mismatches};
    return{changed:true,certified,reported,checked,current,mismatches};
  }
  return{changed:false,certified,reported:Number.isFinite(reported)?reported:null,checked:Number.isFinite(checked)?checked:null,current,mismatches};
}

function mutate(label,operation,options={}){
  try{
    let result;
    session.execute(label,book=>{
      result=operation(book)||{};
      if(!options.preservePaginationCertification)invalidatePaginationCertification(book,label);
      if(result.pageId) session.selection={pageId:result.pageId,itemId:result.itemId||null};
      return result;
    });
    state.audit=M.auditIntegrity(session.book);
    state.compatibility=BookCore.auditData(session.book);
    renderAll();
    setStatus(label,'good');
  }catch(error){
    modal('Η πράξη ακυρώθηκε',`<div class="info-card bad">${esc(error.message)}</div>`);
  }
}

async function chooseItemType(){
  const options=M.ITEM_TYPES.map(type=>`<option value="${esc(type)}">${esc(type)}</option>`).join('');
  const ok=await modal('Νέο item',`<label class="field-row"><span>Τύπος</span><select id="newItemType">${options}</select></label>`,[{label:'Ακύρωση',value:false},{label:'Δημιουργία',value:true,primary:true}]);
  return ok?$('#newItemType').value:null;
}

function moveItem(direction){mutate(direction<0?'Item πάνω':'Item κάτω',book=>C.Operations.moveItem(book,page().id,item().id,direction));}
function moveAcross(direction){mutate(direction==='prev'?'Μεταφορά στην προηγούμενη σελίδα':'Μεταφορά στην επόμενη σελίδα',book=>C.Operations.moveItemToPage(book,page().id,item().id,direction));}
function splitPage(){mutate('Νέα σελίδα από εδώ',book=>C.Operations.splitPage(book,page().id,item().id));}
function cloneSelected(){if(item())mutate('Κλωνοποίηση item',book=>C.Operations.cloneItem(book,page().id,item().id));else mutate('Κλωνοποίηση σελίδας',book=>C.Operations.clonePage(book,page().id));}

function undo(){if(session.undo()){state.audit=M.auditIntegrity(session.book);state.compatibility=BookCore.auditData(session.book);renderAll();setStatus('Αναίρεση.');}}
function redo(){if(session.redo()){state.audit=M.auditIntegrity(session.book);state.compatibility=BookCore.auditData(session.book);renderAll();setStatus('Επανάληψη.');}}

function select(pageId,itemId=null,source=''){
  session.selection={pageId,itemId};
  updateSelection();
  renderChrome();
  renderProperties();
  requestAnimationFrame(()=>{
    const target=source==='tree'
      ? document.querySelector(itemId?`[data-preview-item="${CSS.escape(itemId)}"]`:`[data-preview-page="${CSS.escape(pageId)}"]`)
      : document.querySelector(itemId?`[data-tree-item="${CSS.escape(itemId)}"]`:`[data-tree-page="${CSS.escape(pageId)}"]`);
    target?.scrollIntoView({block:'center',behavior:'smooth'});
    updateSelectionOverlay();
  });
}

function itemSummary(current){
  const raw=M.summarizeItem(current)||current.caption||current.title||current.type||'(κενό)';
  return M.textFromHtml(raw)||current.type||'(κενό)';
}

function renderTree(){
  const host=$('#bookTree');
  host.innerHTML='';
  let itemCount=0;
  session.book.pages.forEach((p,pi)=>{
    const prow=document.createElement('div');
    prow.className='tree-row page';
    prow.dataset.treePage=p.id;
    prow.innerHTML=`<span class="tree-icon">▤</span><span class="tree-label">${pi+1}. ${esc(p.id)}</span>`;
    prow.onclick=()=>select(p.id,null,'tree');
    host.appendChild(prow);
    p.items.forEach(current=>{
      itemCount++;
      const row=document.createElement('div');
      row.className='tree-row item-type';
      row.dataset.treeItem=current.id;
      row.dataset.search=(current.type+' '+itemSummary(current)+' '+current.id).toLowerCase();
      row.innerHTML=`<span class="tree-type">${esc(current.type)}</span><span class="tree-label">${esc(itemSummary(current))}</span>`;
      row.onclick=event=>{event.stopPropagation();select(p.id,current.id,'tree');};
      host.appendChild(row);
    });
  });
  $('#bookCounts').textContent=`${session.book.pages.length} σελίδες · ${itemCount} items`;
  filterTree();
  updateSelection();
}

function filterTree(){
  const query=String($('#bookTreeSearch').value||'').trim().toLowerCase();
  $$('#bookTree .tree-row.item-type').forEach(row=>row.classList.toggle('filtered-out',query&&!row.dataset.search.includes(query)));
}
$('#bookTreeSearch').addEventListener('input',filterTree);

function applyFlowLayout(){
  /* Direct v4 layout is applied inside BookCore. */
}

function updateSelection(){
  $$('#bookTree .selected').forEach(n=>n.classList.remove('selected'));
  $$('#bookPreviewPages .selected-preview-page').forEach(n=>n.classList.remove('selected-preview-page'));
  const p=page(),i=item();
  if(!p){hideSelectionOverlay();return;}
  if(i){
    document.querySelector(`[data-tree-item="${CSS.escape(i.id)}"]`)?.classList.add('selected');
  }else{
    document.querySelector(`[data-tree-page="${CSS.escape(p.id)}"]`)?.classList.add('selected');
    document.querySelector(`[data-preview-page="${CSS.escape(p.id)}"]`)?.classList.add('selected-preview-page');
  }
  updateSelectionOverlay();
}

function hideSelectionOverlay(){
  $('#selectionOverlay').classList.add('hidden');
}

function updateSelectionOverlay(){
  const current=item();
  if(!current||state.view!=='book'){
    hideSelectionOverlay();
    return;
  }
  const target=document.querySelector(`[data-preview-item="${CSS.escape(current.id)}"]`);
  const scroller=$('#bookPreviewScroller');
  if(!target||!scroller){hideSelectionOverlay();return;}
  const r=target.getBoundingClientRect();
  const s=scroller.getBoundingClientRect();
  const visible=r.bottom>s.top&&r.top<s.bottom&&r.right>s.left&&r.left<s.right;
  if(!visible){hideSelectionOverlay();return;}
  const overlay=$('#selectionOverlay');
  overlay.style.left=`${Math.max(r.left,s.left)}px`;
  overlay.style.top=`${Math.max(r.top,s.top)}px`;
  overlay.style.width=`${Math.max(8,Math.min(r.right,s.right)-Math.max(r.left,s.left))}px`;
  overlay.style.height=`${Math.max(8,Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top))}px`;
  $('#selectionOverlayLabel').textContent=`${current.type} · ${current.layout?.floatInteraction||defaultFloatInteraction(current.type)}`;
  overlay.classList.remove('hidden');
}

function section(title){
  const node=document.createElement('section');
  node.className='property-section';
  node.innerHTML=`<h3>${esc(title)}</h3><div class="property-form"></div>`;
  return node;
}

function check(label,value,onChange){
  const row=document.createElement('label');
  row.className='field-check';
  const input=document.createElement('input');
  input.type='checkbox';input.checked=!!value;
  input.onchange=()=>onChange(input.checked);
  row.append(input,document.createTextNode(label));
  return row;
}

function bodyHtml(current){
  return BookCore.richTextToHtml(current.body);
}

function renderAuditProperties(host){
  state.audit=M.auditIntegrity(session.book);
  state.compatibility=BookCore.auditData(session.book);
  const v=state.audit.validation,c=state.compatibility,ext=session.book.extensions||{},pagination=session.book.importManifest?.pagination||{},currentPages=session.book.pages.length,reportedPages=Number(pagination.outputPages)||null,checkedPages=Number(pagination.audit?.pagesChecked)||null,certified=ext.paginationCertified===true,stale=ext.paginationStatus==='stale'||certified&&(reportedPages&&reportedPages!==currentPages||checkedPages&&checkedPages!==currentPages);
  host.innerHTML=`
    <div class="info-card ${state.audit.ok?'good':v.ok?'warn':'bad'}"><b>${state.audit.ok?'Έγκυρο και συνεπές':'Χρειάζεται έλεγχο'}</b><br>Pages ${v.stats.pages} · Items ${v.stats.items} · IDs ${state.audit.stableIds.total} · Broken ${state.audit.brokenReferences.length}</div>
    <div class="info-card ${stale?'warn':certified?'good':'warn'}"><b>Σελιδοποίηση: ${stale?'παλιά πιστοποίηση — stale':certified?'πιστοποιημένη':'μη πιστοποιημένη'}</b><br>Τρέχουσες σελίδες ${currentPages}${reportedPages?` · report outputPages ${reportedPages}`:''}${checkedPages?` · audit pagesChecked ${checkedPages}`:''}${ext.paginationStaleReason?`<br>Αιτία: ${esc(ext.paginationStaleReason)}`:''}</div>
    <div class="info-card ${c.ok?'good':'warn'}"><b>Direct v4 renderer</b><br>Figures ${c.stats.figures} · Captions ${c.stats.figuresWithCaptions} · Scenes ${c.stats.scenes} · Scene URLs ${c.stats.scenesWithSources}<br>${esc(c.renderer)} · ${c.directV4?'direct v4':'legacy input'}</div>
    <section class="property-section"><h3>Τύποι</h3><pre class="code-block">${esc(JSON.stringify(v.stats.byType,null,2))}</pre></section>
    <section class="property-section"><h3>Σφάλματα</h3><div class="property-form">${v.errors.length?'<ul>'+v.errors.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>':'<div class="info-card good">Κανένα</div>'}</div></section>
    <section class="property-section"><h3>Προειδοποιήσεις</h3><div class="property-form">${v.warnings.length?'<ul>'+v.warnings.slice(0,100).map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>':'<div class="info-card good">Καμία</div>'}</div></section>`;
}

function renderMigrationProperties(host){
  if(state.mode!=='migration'){
    host.innerHTML='<div class="info-card">Το ανοιχτό βιβλίο δεν είναι ενεργό migration candidate.</div>';
    return;
  }
  const report=state.report||{};
  const counts=report.counts||{};
  host.innerHTML=`
    <div class="info-card warn"><b>${esc(report.sourceSchema)} → ${esc(report.targetSchema)}</b><br>Assigned IDs: ${(report.assignedIds||[]).length}<br>English fields εκτός candidate: ${(report.droppedTranslations||[]).length}<br>Explicit float rules: ${report.layoutInteractionDefaultsAssigned||0}</div>
    <section class="property-section"><h3>Mappings</h3><div class="property-form"><ul>${(report.fieldMappings||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div></section>
    <section class="property-section"><h3>Τύποι</h3><pre class="code-block">${esc(JSON.stringify(counts,null,2))}</pre></section>
    <div class="action-grid"><button id="saveCandidateButton">Αποθήκευση candidate + report + backup</button></div>
    <div class="info-card">Δεν υπάρχει προαγωγή σε production book.json.</div>`;
  $('#saveCandidateButton').onclick=saveCandidate;
}

function renderProperties(){
  const host=$('#propertiesContent');
  host.innerHTML='';
  if(!has()) return;
  $$('.property-tabs button').forEach(button=>button.classList.toggle('active',button.dataset.tab===state.tab));
  if(state.tab==='book') renderBookProperties(host);
  else if(state.tab==='page') renderPageProperties(host);
  else if(state.tab==='item') renderItemProperties(host);
  else if(state.tab==='nav') renderNavProperties(host);
  else if(state.tab==='audit') renderAuditProperties(host);
  else renderMigrationProperties(host);
}

function renderAll(){
  renderChrome();
  if(!has()) return;
  renderTree();
  renderPreview();
  renderProperties();
}

function applyPreviewZoom(announce=false){
  document.documentElement.style.setProperty('--preview-zoom',String(state.previewZoom));
  storage.set('bw-v4_4-zoom',String(state.previewZoom));
  storage.set('bw-v4_4-zoom-mode',state.previewZoomMode);
  requestAnimationFrame(updateSelectionOverlay);
  if(announce) setStatus(`Μεγέθυνση ${Math.round(state.previewZoom*100)}%${state.previewZoomMode==='fit'?' · προσαρμογή στο πλάτος':''}.`);
}
function fitPreviewToWidth(announce=false){
  const scroller=$('#bookPreviewScroller');
  const width=Number(session.book?.layoutDefaults?.pageWidthPx||794);
  if(!scroller||!width)return;
  state.previewZoomMode='fit';
  state.previewZoom=Math.max(.35,Math.min(1.08,(scroller.clientWidth-34)/width));
  applyPreviewZoom(announce);
}
function setPreviewZoom100(){state.previewZoomMode='manual';state.previewZoom=1;applyPreviewZoom(true)}
function changeZoom(delta){
  state.previewZoomMode='manual';
  state.previewZoom=Math.max(.35,Math.min(2,state.previewZoom+delta));
  applyPreviewZoom(true);
}
function addOverflowMarker(wrapper,entry){
  wrapper.classList.add('preview-overflow-page');
  wrapper.dataset.overflowPx=String(entry.overflowPx||0);
  const sheet=wrapper.querySelector('.sheet');if(!sheet)return;
  sheet.querySelector('.render-overflow-marker')?.remove();
  const marker=document.createElement('div');marker.className='render-overflow-marker';
  marker.innerHTML=`<span>Σελίδα ${Number(entry.displayPage||entry.pageIndex+1)} · overflow ${Number(entry.overflowPx||0).toLocaleString('el-GR',{maximumFractionDigits:1})} px</span>`;
  sheet.appendChild(marker);
}
function applyPreviewOverflowMarkers(audit){
  $$('#bookPreviewPages .preview-overflow-page').forEach(node=>{node.classList.remove('preview-overflow-page');delete node.dataset.overflowPx;node.querySelector('.render-overflow-marker')?.remove()});
  (audit?.pages||[]).forEach(entry=>{const wrapper=document.querySelector(`[data-preview-page="${CSS.escape(entry.pageId||'')}"]`);if(wrapper)addOverflowMarker(wrapper,entry)});
}
function schedulePreviewOverflowAudit(delay=180){
  if(!has()||!isPaginationManaged(session.book))return;
  const token=++state.previewOverflowAuditToken;
  setTimeout(async()=>{try{
    const audit=await P.auditOverflow(session.book,{imageCandidates,rejectOverflow:false,tolerancePx:1,assetTimeout:1800});
    if(token!==state.previewOverflowAuditToken||!has())return;
    state.previewOverflowAudit=audit;applyPreviewOverflowMarkers(audit);
    $('#previewCaption').textContent=`BookCore ${BookCore.VERSION} · canonical print DOM · ${Math.round(state.previewZoom*100)}% · ${audit.overflowPages} overflow`;
  }catch(error){console.warn('preview overflow audit',error)}},delay);
}

function toggleProperties(){
  state.propertiesOpen=!state.propertiesOpen;
  storage.set('bw-v4_2-properties',state.propertiesOpen?'open':'closed');
  renderChrome();
  requestAnimationFrame(updateSelectionOverlay);
}

function toggleScenes(){
  state.realScenes=!state.realScenes;
  renderPreview();
  renderChrome();
  setStatus(`Σκηνές ${state.realScenes?'ενεργές':'ανενεργές'}.`);
}

function setTab(tab){
  if(!has()) return;
  state.tab=tab;
  state.propertiesOpen=true;
  storage.set('bw-v4_2-properties','open');
  setView('book');
  renderProperties();
  renderChrome();
}


function migrationTool(){
  if(has()&&state.mode==='migration'){setTab('migration');return;}
  openBook();
}

function unavailable(name){
  modal(name,`<div class="info-card warn">Η εντολή είναι ορατή στο ενιαίο κέλυφος, αλλά δεν συνδέεται ακόμη με τον canonical v4 converter.</div>`);
}

function findOpaqueLegacyDocxItem(book){
  for(const page of book?.pages||[])for(const item of page.items||[]){
    const source=[item.legacySourceHtml,item.html,item.extensions?.docxSourceHtml,item.extensions?.legacySourceHtml].find(value=>typeof value==='string'&&value.trim())||'';
    if(/^\s*<(table|ul|ol)\b/i.test(source))return{pageId:page.id,itemId:item.id,type:RegExp.$1.toLowerCase()};
  }
  return null;
}
async function reflowCurrentBook(){
  if(!session.book||session.book?.importManifest?.sourceType!=='docx'){modal('Canonical reflow','<div class="info-card warn">Η εντολή εφαρμόζεται σε βιβλία που προέρχονται από DOCX.</div>');return;}
  const opaque=findOpaqueLegacyDocxItem(session.book);
  if(opaque){
    modal('Canonical reflow',`<div class="info-card warn"><b>Το βιβλίο προέρχεται από τον παλιό v4.3 importer.</b><br>Περιέχει ${esc(opaque.type)} ως αδιαφανές HTML (${esc(opaque.itemId)}). Δεν θα δημιουργήσουμε μόνιμη legacy δίχαλα ούτε θα επιχειρήσουμε σιωπηρή, επισφαλή μετατροπή.<br><br>Δημιούργησε ξανά το βιβλίο από το αρχικό DOCX με <b>Αρχείο → Νέο βιβλίο από Word…</b>. Ο v4.4 importer εισάγει πίνακες και λίστες ως canonical αντικείμενα και εφαρμόζει μετά πραγματική σελιδοποίηση.</div>`);
    return;
  }
  try{
    state.busy=true;renderChrome();setStatus('Συνένωση της παλιάς σταθερής ροής και νέα canonical σελιδοποίηση…','warn');
    const flow=P.coalesceDocxFlow(session.book);
    const paged=await P.paginateBook(flow,{imageCandidates,rejectOverflow:true,tolerancePx:1,assetTimeout:2500});
    mutate('Canonical reflow και σελιδοποίηση',book=>{for(const key of Object.keys(book))delete book[key];Object.assign(book,clone(paged.book));return{pageId:book.pages[0].id,itemId:book.pages[0].items[0]?.id||null};},{preservePaginationCertification:true});
    state.audit={...(state.audit||{}),pagination:paged.report,overflowAudit:paged.audit};
    setStatus(`Canonical reflow: ${paged.book.pages.length} σελίδες · 0 overflow. Αποθήκευσε για να οριστικοποιηθεί.`,'good');
  }catch(error){console.error(error);setStatus('Αποτυχία canonical reflow: '+error.message,'bad');modal('Canonical reflow',`<div class="info-card bad">${esc(error.message)}</div>`)}finally{state.busy=false;renderChrome()}
}

function overflowEntrySummary(entry){
  const source=entry.sourcePage!==null&&entry.sourcePage!==undefined?` · πηγή ${esc(entry.sourcePage)}`:'';
  const culprit=entry.bottomItemId?`<br><span class="muted">Κάτω item: <code>${esc(entry.bottomItemId)}</code>${entry.bottomItemType?` (${esc(entry.bottomItemType)})`:''}${entry.overflowKind==='float-bottom'?' · float':''}</span>`:'';
  return `<div class="info-card warn"><b>Σελίδα ${Number(entry.displayPage||entry.pageIndex+1)}</b> · <code>${esc(entry.pageId||'')}</code>${source}<br>Υπερχείλιση: <b>${Number(entry.overflowPx||0).toLocaleString('el-GR',{maximumFractionDigits:1})} px</b> · χρησιμοποιούνται ${Number(entry.usedPx||0).toLocaleString('el-GR',{maximumFractionDigits:1})}/${Number(entry.availablePx||0).toLocaleString('el-GR',{maximumFractionDigits:1})} px${culprit}</div>`;
}

async function requestOverflowPrintDecision(overflowAudit,previewWindow){
  const rows=(overflowAudit.pages||[]).map(overflowEntrySummary).join('');
  const decision=await modal('Υπερχείλιση πριν από την εκτύπωση',`<div class="info-card bad"><b>Βρέθηκαν ${overflowAudit.overflowPages} σελίδες με υπερχείλιση.</b><br>Η εκτύπωση μπορεί να κόψει περιεχόμενο ή να το επικαλύψει με το υποσέλιδο.</div>${rows}<div class="info-card">Μπορείς να μεταβείς στην πρώτη προβληματική σελίδα ή να συνεχίσεις συνειδητά στην εκτυπωτική προβολή. Το production <code>book.json</code> δεν τροποποιείται από την παράκαμψη.</div>`,[
    {label:'Ακύρωση',value:'cancel'},
    {label:'Μετάβαση στην πρώτη σελίδα',value:'goto'},
    {label:'Συνέχεια παρά την υπερχείλιση',value:'continue',danger:true}
  ]);
  if(decision==='goto'){
    try{previewWindow?.close()}catch{}
    const first=overflowAudit.pages?.[0];
    if(first?.pageId){setView('book');select(first.pageId,null,'tree');setStatus(`Μετάβαση στη σελίδα ${Number(first.displayPage||first.pageIndex+1)} με υπερχείλιση ${Number(first.overflowPx||0).toFixed(1)} px.`,'warn');}
    return false;
  }
  if(decision!=='continue'){
    try{previewWindow?.close()}catch{}
    setStatus('Η εκτυπωτική προβολή ακυρώθηκε λόγω υπερχείλισης.','warn');
    return false;
  }
  return true;
}

async function openReader(){
  if(!session.directoryHandle || !session.book) return;
  const previewWindow=window.open('about:blank','_blank');
  if(!previewWindow){modal('Εκτυπωτική προβολή','<div class="info-card bad">Ο browser μπλόκαρε το νέο παράθυρο. Επίτρεψε αναδυόμενα παράθυρα για το localhost και ξαναδοκίμασε.</div>');return;}
  previewWindow.document.write('<!doctype html><html lang="el"><meta charset="utf-8"><title>Εκτυπωτική προβολή</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui;background:#f5f2ea;color:#263746"><p>Ετοιμάζεται το αυτόνομο βιβλίο…</p></body></html>');
  previewWindow.document.close();
  try{
    setStatus('Έλεγχος και δημιουργία ζωντανής εκτυπωτικής προβολής…','warn');
    const validation=M.validateBook(session.book);if(!validation.ok)throw new Error(validation.errors.join('\n'));
    const coreValidation=BookCore.validateData(session.book);if(!coreValidation.ok)throw new Error(coreValidation.errors.join('\n'));
    const paginationManaged=isPaginationManaged(session.book);
    const overflowAudit=paginationManaged
      ?await P.auditOverflow(session.book,{imageCandidates,rejectOverflow:false,tolerancePx:1,assetTimeout:2500})
      :{ok:true,skipped:true,reason:'manual-layout-book',pagesChecked:session.book.pages.length,overflowPages:0,pages:[],rendererVersion:BookCore.VERSION,paginationVersion:P.VERSION};
    const printOverride=paginationManaged&&!overflowAudit.ok;
    if(printOverride&&!await requestOverflowPrintDecision(overflowAudit,previewWindow))return;
    const missingFigureAssets=[];
    const previewBook=M.deepClone(session.book);
    const assumedBookBase=new URL(`../books/${encodeURIComponent(session.directoryHandle.name)}/`,location.href);
    for(const currentPage of previewBook.pages||[])for(const current of currentPage.items||[]){
      if(current.type==='figure'&&String(current.src||'').startsWith('images/')){
        const resolved=state.assets.get(String(current.src));
        if(resolved) current.src=resolved;
        else missingFigureAssets.push(String(current.src));
      }
      if(current.type==='scene'&&String(current.src||'').trim()){
        current.src=resolveSceneSource(current.src,assumedBookBase);
      }
    }
    if(missingFigureAssets.length)throw new Error(`Η εκτύπωση σταμάτησε: λείπουν ${missingFigureAssets.length} αρχεία εικόνων από τον φάκελο του βιβλίου:\n${[...new Set(missingFigureAssets)].join('\n')}`);
    const localSceneUrls=[...new Set((previewBook.pages||[]).flatMap(currentPage=>(currentPage.items||[]).filter(current=>current.type==='scene').map(current=>String(current.src||'').trim()).filter(Boolean)).filter(src=>{try{return new URL(src).origin===location.origin}catch{return false}}))];
    const sceneChecks=await Promise.all(localSceneUrls.map(async src=>{try{const response=await fetch(src,{method:'HEAD',cache:'no-store'});return response.ok?null:{src,status:response.status}}catch(error){return{src,status:0,error:error.message}}}));
    const missingScenes=sceneChecks.filter(Boolean);
    if(missingScenes.length)throw new Error(`Η εκτύπωση σταμάτησε: ${missingScenes.length} σκηνές δεν είναι διαθέσιμες από τον ενεργό server. Η πρώτη είναι:\n${missingScenes[0].src}\n\nΤο βιβλίο δεν θα εκτυπωθεί με κενά πλαίσια.`);
    previewBook.meta=previewBook.meta||{};previewBook.meta.updatedAt=new Date().toISOString();previewBook.extensions={...(previewBook.extensions||{}),paginationCertified:paginationManaged?overflowAudit.ok:(previewBook.extensions?.paginationCertified??false),paginationStatus:paginationManaged?(overflowAudit.ok?'print-audit-ok':'print-override-overflow'):(previewBook.extensions?.paginationStatus||'manual-layout'),lastPrintOverflowAudit:overflowAudit,printOverride,printOverrideAt:printOverride?new Date().toISOString():null};
    const runtimeAudit=BookCore.auditData(previewBook);
    previewBook.extensions={...(previewBook.extensions||{}),livePrintReport:{generatedAt:new Date().toISOString(),sourceSchema:session.book.schemaVersion,sourceMode:state.mode,rendererVersion:BookCore.VERSION,paginationVersion:P.VERSION,renderPath:'bookwriter-v4 → in-memory Blob → standalone BookCore reader',productionBookUntouched:true,printOverride,pages:previewBook.pages.length,items:previewBook.pages.reduce((sum,current)=>sum+(current.items||[]).length,0),overflowAudit,runtimeAudit,validation,coreValidation}};
    const blobUrl=URL.createObjectURL(new Blob([JSON.stringify(previewBook)],{type:'application/json'}));
    const url=new URL('../reader/index.html',location.href);url.searchParams.set('book',blobUrl);url.searchParams.set('bookBase',assumedBookBase.href);url.searchParams.set('lang','el');url.searchParams.set('_v4print',String(Date.now()));
    previewWindow.location.replace(url.href);setTimeout(()=>URL.revokeObjectURL(blobUrl),120000);
    setStatus(printOverride?`Εκτυπωτική προβολή με άδεια: ${previewBook.pages.length} σελίδες · ${overflowAudit.overflowPages} overflow.`:paginationManaged?`Ζωντανό αυτόνομο βιβλίο: ${previewBook.pages.length} σελίδες · 0 overflow.`:`Ζωντανό αυτόνομο βιβλίο: ${previewBook.pages.length} σελίδες · χειροκίνητη διάταξη.`,printOverride?'warn':'good');
  }catch(error){console.error(error);try{previewWindow.close()}catch{}setStatus('Απέτυχε η εκτυπωτική προβολή','bad');modal('Εκτύπωση / PDF',`<div class="info-card bad">${esc(error.message)}</div>`)}
}
/* v4.2 Full Editor Core overrides */

function commandEnabled(cmd){
  const canPage=!!page();
  const canItem=!!item();
  const pi=pageIndex();
  const rules={
    'new-book':true,'open-book':true,'open-candidate':true,'choose-library':true,'refresh-library':!!state.library.booksHandle&&!state.library.loading,'home':true,'help':true,'about':true,
    'new-docx':!state.busy,'insert-docx':has()&&!state.busy&&!!session.directoryHandle,'inline-equation':!!(activeRichContext?.surface?.isConnected||(['paragraph','note','side_note'].includes(item()?.type))),'docx-audit':!!state.docx.result,'clean-assets':false,
    'save':has()&&state.mode!=='migration'&&(session.isDirty()||state.mode==='new'||state.mode==='candidate'),
    'save-candidate':has()&&state.mode==='migration',
    'backup':has()&&!!session.directoryHandle,
    'print':has()&&!!session.directoryHandle,
    'close-book':has(),
    'undo':has()&&session.commandStack.undoStack.length>0,
    'redo':has()&&session.commandStack.redoStack.length>0,
    'clone':canItem||canPage,
    'delete':canItem||canPage,
    'move-up':canItem&&itemIndex()>0,
    'move-down':canItem&&itemIndex()<page().items.length-1,
    'move-prev-page':canItem,
    'move-next-page':canItem,
    'split-page':canItem,
    'insert-item':canPage,
    'insert-item-before':canPage,
    'insert-item-after':canPage,
    'insert-page':has(),
    'insert-page-before':has(),
    'insert-page-after':has(),
    'move-page-up':canPage&&pi>0,
    'move-page-down':canPage&&pi<session.book.pages.length-1,
    'clone-page':canPage,
    'merge-page-prev':canPage&&pi>0,
    'merge-page-next':canPage&&pi<session.book.pages.length-1,
    'display-equation':canPage,
    'book-view':has(),
    'tab-book':has(),'tab-page':has(),'tab-item':has(),'tab-nav':has(),
    'audit':has(),'reflow':has()&&session.book?.importManifest?.sourceType==='docx','migration':true,'selftest':has(),
    'toggle-properties':has()&&state.view==='book',
    'toggle-scenes':has(),
    'zoom-in':has(),'zoom-out':has(),'zoom-fit':has(),'zoom-100':has()
  };
  return rules[cmd]!==false&&!!rules[cmd];
}

function valueAtPath(root,path){
  let current=root;
  for(const key of path){
    if(current==null) return undefined;
    current=current[key];
  }
  return current;
}

function optionalNumber(value){
  if(value===''||value===null||value===undefined) return null;
  const number=Number(value);
  return Number.isFinite(number)?number:null;
}

function field(label,value,onChange,type='text',options=null,config={}){
  const row=document.createElement('label');
  row.className='field-row'+(config.wide?' wide-field':'');
  row.innerHTML=`<span>${esc(label)}</span>`;
  let input;
  if(options){
    input=document.createElement('select');
    options.forEach(o=>input.add(new Option(o.label??o.value,o.value)));
    input.value=value??'';
  }else if(type==='textarea'){
    input=document.createElement('textarea');
    input.value=value??'';
    if(config.rows) input.rows=config.rows;
    if(config.monospace) input.classList.add('monospace-input');
  }else{
    input=document.createElement('input');
    input.type=type;
    input.value=value??'';
    if(config.min!==undefined) input.min=config.min;
    if(config.max!==undefined) input.max=config.max;
    if(config.step!==undefined) input.step=config.step;
    if(config.placeholder) input.placeholder=config.placeholder;
  }
  if(config.disabled) input.disabled=true;
  input.addEventListener('change',()=>{
    let next=input.value;
    if(type==='number') next=optionalNumber(input.value);
    onChange(next,input);
  });
  row.appendChild(input);
  return row;
}

function colorField(label,value,onChange){
  const row=document.createElement('label');
  row.className='field-row';
  row.innerHTML=`<span>${esc(label)}</span>`;
  const wrap=document.createElement('div');
  wrap.className='color-field';
  const picker=document.createElement('input');picker.type='color';picker.value=/^#[0-9a-f]{6}$/i.test(value||'')?value:'#000000';
  const text=document.createElement('input');text.type='text';text.value=value||'';text.placeholder='π.χ. #1b506f ή κενό';
  picker.onchange=()=>{text.value=picker.value;onChange(picker.value);};
  text.onchange=()=>onChange(text.value.trim());
  wrap.append(picker,text);row.appendChild(wrap);return row;
}

function prop(label,path,value,options={}){
  mutate(label,book=>{
    C.Operations.setValue(book,path,value);
    return {pageId:page()?.id||book.pages[0].id,itemId:item()?.id||null};
  });
  if(options.tab) state.tab=options.tab;
}

function setRichBodyFromPlain(base,text){
  const nodes=[];
  String(text??'').split(/\r?\n/).forEach((line,index)=>{
    if(index) nodes.push({type:'line_break'});
    if(line) nodes.push(M.createTextRun(line));
  });
  C.Operations.setValue(base.book,base.path,M.createRichText(nodes));
}

function insertItem(forcedType=null,placement='after'){
  return (async()=>{
    const type=forcedType||await chooseItemType();
    if(!type) return;
    mutate(`Νέο ${type} ${placement==='before'?'πριν':'μετά'}`,book=>{
      const p=page();
      let index;
      if(item()) index=itemIndex()+(placement==='after'?1:0);
      else index=placement==='before'?0:p.items.length;
      const result=C.Operations.insertItem(book,p.id,index,type);
      const created=book.pages.find(x=>x.id===result.pageId).items.find(x=>x.id===result.itemId);
      ensureNewItemLayout(created);
      return result;
    });
    state.tab='item';
    renderAll();
  })();
}

function insertPage(placement='after'){
  const current=page();
  const index=Math.max(0,pageIndex()+(placement==='after'?1:0));
  mutate(`Νέα σελίδα ${placement==='before'?'πριν':'μετά'}`,book=>C.Operations.insertPage(book,index,M.createPage({
    header:clone(current?.header||book.pageDefaults.header),
    footer:clone(current?.footer||book.pageDefaults.footer),
    pageNumbering:clone(current?.pageNumbering||book.pageDefaults.pageNumbering)
  })));
  state.tab='page';
}

function movePage(direction){
  if(!page()) return;
  mutate(direction<0?'Σελίδα πάνω':'Σελίδα κάτω',book=>C.Operations.movePage(book,page().id,direction));
}
function clonePage(){if(page())mutate('Κλωνοποίηση σελίδας',book=>C.Operations.clonePage(book,page().id));}
function mergePage(direction){if(page())mutate(direction==='prev'?'Συγχώνευση με προηγούμενη':'Συγχώνευση με επόμενη',book=>C.Operations.mergePage(book,page().id,direction));}

function referencesToId(id){
  if(!has()||!id) return [];
  return (M.scanReferences(session.book)||[]).filter(ref=>String(ref.target||'').replace(/^#/,'')===id);
}

async function deleteSelected(){
  if(item()){
    const refs=referencesToId(item().id).filter(ref=>ref.kind!=='asset');
    const refHtml=refs.length?`<div class="info-card warn"><b>${refs.length} αναφορές</b> δείχνουν σε αυτό το ID και μπορεί να σπάσουν.</div>`:'';
    const ok=await confirmBox('Διαγραφή item',`${refHtml}Να διαγραφεί το <code>${esc(item().id)}</code>;`,'Διαγραφή');
    if(ok) mutate('Διαγραφή item',book=>C.Operations.deleteItem(book,page().id,item().id));
    return;
  }
  const ids=[page().id,...page().items.map(current=>current.id)];
  const refCount=ids.reduce((sum,id)=>sum+referencesToId(id).filter(ref=>ref.kind!=='asset').length,0);
  const buttons=[{label:'Ακύρωση',value:null}];
  if(pageIndex()>0) buttons.push({label:'Μεταφορά περιεχομένων πριν',value:'move-prev'});
  if(pageIndex()<session.book.pages.length-1) buttons.push({label:'Μεταφορά περιεχομένων μετά',value:'move-next'});
  buttons.push({label:'Διαγραφή σελίδας και περιεχομένων',value:'delete',danger:true});
  const warning=refCount?`<div class="info-card warn"><b>${refCount} εσωτερικές αναφορές</b> σχετίζονται με IDs της σελίδας.</div>`:'';
  const mode=await modal('Διαγραφή σελίδας',`${warning}<div class="info-card warn">Η σελίδα έχει ${page().items.length} items.</div>`,buttons);
  if(mode) mutate('Διαγραφή σελίδας',book=>C.Operations.deletePage(book,page().id,mode));
}

function createPreviewPageWrapper(candidatePage,index){
  const wrapper=BookCore.renderPageNode(session.book,candidatePage,index,{
    lang:'el',preview:false,editor:true,
    sceneSource:src=>state.realScenes?resolveSceneSource(src):'',
    imageCandidates
  });
  wrapper.dataset.previewPage=candidatePage.id;
  wrapper.addEventListener('click',()=>select(candidatePage.id,null,'preview'));
  const body=wrapper.querySelector('.sheet-body');
  const renderedItems=body?[...body.children]:[];
  candidatePage.items.forEach((current,itemIdx)=>{
    const rendered=renderedItems[itemIdx];
    if(!rendered) return;
    rendered.dataset.previewItem=current.id;
    rendered.classList.add('preview-hit-target');
    rendered.addEventListener('click',event=>{
      event.stopPropagation();
      select(candidatePage.id,current.id,'preview');
    });
  });
  return wrapper;
}

function renderPreview(){
  const host=$('#bookPreviewPages');
  host.innerHTML='';
  if(state.mode==='migration'){
    const banner=document.createElement('div');
    banner.className='migration-banner';
    banner.innerHTML=`<b>Migration candidate:</b> ${state.candidateSaved?'αποθηκευμένος':'μόνο στη μνήμη'} · το production book.json δεν άλλαξε.`;
    host.appendChild(banner);
  }
  session.book.pages.forEach((candidatePage,index)=>host.appendChild(createPreviewPageWrapper(candidatePage,index)));
  if(state.previewZoomMode==='fit') requestAnimationFrame(()=>fitPreviewToWidth(false)); else applyPreviewZoom(false);
  $('#previewCaption').textContent=`BookCore ${BookCore.VERSION} · canonical print DOM · ${Math.round(state.previewZoom*100)}%`;
  updateSelection();
  requestAnimationFrame(updateSelectionOverlay);
  schedulePreviewOverflowAudit();
}

function renderPreviewPageOnly(pageId){
  const p=session.book.pages.find(x=>x.id===pageId);
  if(!p) return;
  const old=document.querySelector(`[data-preview-page="${CSS.escape(pageId)}"]`);
  if(!old){renderPreview();return;}
  const index=session.book.pages.indexOf(p);
  const replacement=createPreviewPageWrapper(p,index);
  old.replaceWith(replacement);
  updateSelection();
  requestAnimationFrame(updateSelectionOverlay);
}

function updateTreeItemLabel(current){
  const row=document.querySelector(`[data-tree-item="${CSS.escape(current.id)}"]`);
  if(!row) return;
  const summary=itemSummary(current);
  const label=row.querySelector('.tree-label');
  if(label) label.textContent=summary;
  row.dataset.search=(current.type+' '+summary+' '+current.id).toLowerCase();
}

function sanitizeEditorHtml(html){
  const doc=new DOMParser().parseFromString(`<div id="bw-root">${String(html||'')}</div>`,'text/html');
  const root=doc.querySelector('#bw-root');
  root.querySelectorAll('script,style,iframe,object,embed,meta,link').forEach(node=>node.remove());
  root.querySelectorAll('*').forEach(node=>{
    [...node.attributes].forEach(attr=>{
      const name=attr.name.toLowerCase();
      const value=attr.value.trim();
      if(name.startsWith('on')||name==='srcdoc') node.removeAttribute(attr.name);
      if((name==='href'||name==='src')&&/^javascript:/i.test(value)) node.removeAttribute(attr.name);
    });
  });
  return root.innerHTML;
}

function htmlToInlineRich(html){
  const doc=new DOMParser().parseFromString(`<div id="bw-rich-root">${html}</div>`,'text/html');
  const root=doc.querySelector('#bw-rich-root');
  const nodes=[];
  const marksEqual=(a,b)=>['bold','italic','underline','superscript','subscript','color','highlight'].every(k=>(a[k]||'')===(b[k]||''));
  const pushText=(text,marks={},target=nodes)=>{
    if(!text) return;
    const clean=String(text).replace(/\u00a0/g,' ').replace(/\u200b/g,'');
    if(!clean) return;
    const last=target[target.length-1];
    if(last?.type==='text_run'&&marksEqual(last,marks)) last.text+=clean;
    else target.push(M.createTextRun(clean,marks));
  };
  const pushBreak=(target=nodes)=>{if(target.length&&target[target.length-1].type!=='line_break')target.push({type:'line_break'});};
  const blockTags=new Set(['P','DIV','LI','UL','OL','H1','H2','H3','H4','H5','H6','BLOCKQUOTE','TR']);
  const walk=(node,marks={},target=nodes)=>{
    if(node.nodeType===Node.TEXT_NODE){pushText(node.nodeValue||'',marks,target);return;}
    if(node.nodeType!==Node.ELEMENT_NODE) return;
    const tag=node.tagName;
    if(tag==='BR'){pushBreak(target);return;}
    if(tag==='SPAN'&&node.classList.contains('bw-inline-math')){
      const math=node.querySelector('math');
      target.push({type:'math_inline',source:node.dataset.mathSource||'',mathml:math?math.outerHTML:''});
      return;
    }
    if(tag==='MATH'){
      target.push({type:'math_inline',source:node.getAttribute('data-source')||'',mathml:node.outerHTML});
      return;
    }
    const next={...marks};
    if(tag==='B'||tag==='STRONG') next.bold=true;
    if(tag==='I'||tag==='EM') next.italic=true;
    if(tag==='U') next.underline=true;
    if(tag==='SUP') next.superscript=true;
    if(tag==='SUB') next.subscript=true;
    const style=node.style;
    if(style?.color) next.color=style.color;
    if(style?.backgroundColor) next.highlight=style.backgroundColor;
    const isBlock=blockTags.has(tag);
    if(isBlock&&target.length) pushBreak(target);
    if(tag==='LI') pushText(node.parentElement?.tagName==='OL'?'1. ':'• ',next,target);
    if(tag==='A'){
      const children=[];
      [...node.childNodes].forEach(child=>walk(child,next,children));
      target.push({type:'link',href:node.getAttribute('href')||'',...(node.getAttribute('target')?{target:node.getAttribute('target')}:{ }),children});
    }else [...node.childNodes].forEach(child=>walk(child,next,target));
    if(isBlock) pushBreak(target);
  };
  [...root.childNodes].forEach(node=>walk(node,{},nodes));
  while(nodes[0]?.type==='line_break') nodes.shift();
  while(nodes.at(-1)?.type==='line_break') nodes.pop();
  return M.createRichText(nodes);
}

function pushDirectEdit(label,beforeBook,beforeSelection){
  const beforeJson=JSON.stringify(beforeBook);
  const rawAfterJson=JSON.stringify(session.book);
  if(beforeJson===rawAfterJson) return;
  invalidatePaginationCertification(session.book,label);
  const afterBook=clone(session.book);
  const validation=M.validateBook(session.book);
  if(!validation.ok){
    session.book=beforeBook;
    session.selection=beforeSelection;
    renderAll();
    throw new Error(validation.errors.join('\n'));
  }
  session.commandStack.push({label,at:new Date().toISOString(),before:beforeBook,after:afterBook,beforeSelection,afterSelection:clone(session.selection)});
  session.emit('change',{label});
  state.audit=M.auditIntegrity(session.book);
  state.compatibility=BookCore.auditData(session.book);
  renderChrome();
}

function richTextToEditorHtml(rich){
  const renderNodes=nodes=>(nodes||[]).map(node=>{
    if(!node||typeof node!=='object') return esc(node??'');
    if(node.type==='line_break') return '<br>';
    if(node.type==='math_inline'){
      const source=esc(node.source||'');
      const mathml=node.mathml||`<span class="math-inline-source">${source||'εξίσωση'}</span>`;
      return `<span class="bw-inline-math" contenteditable="false" data-math-source="${source}" title="Διπλό κλικ για επεξεργασία">${mathml}</span>`;
    }
    if(node.type==='link'){
      const target=node.target?` target="${esc(node.target)}"`:'';
      return `<a href="${esc(node.href||'')}"${target}>${renderNodes(node.children)}</a>`;
    }
    if(node.type==='text_run'){
      let html=esc(node.text||'');
      if(node.bold) html=`<strong>${html}</strong>`;
      if(node.italic) html=`<em>${html}</em>`;
      if(node.underline) html=`<u>${html}</u>`;
      if(node.superscript) html=`<sup>${html}</sup>`;
      if(node.subscript) html=`<sub>${html}</sub>`;
      const styles=[];
      if(node.color) styles.push(`color:${esc(node.color)}`);
      if(node.highlight) styles.push(`background-color:${esc(node.highlight)}`);
      if(styles.length) html=`<span style="${styles.join(';')}">${html}</span>`;
      return html;
    }
    return '';
  }).join('');
  return renderNodes(rich?.nodes);
}

function createInlineMathElement(source,mathml){
  const span=document.createElement('span');
  span.className='bw-inline-math';
  span.contentEditable='false';
  span.dataset.mathSource=source||'';
  span.title='Διπλό κλικ για επεξεργασία';
  span.innerHTML=mathml||`<span class="math-inline-source">${esc(source||'εξίσωση')}</span>`;
  return span;
}

function saveRichSelection(context){
  if(context?.suspendSelection) return;
  if(!context?.surface?.isConnected) return;
  const selection=getSelection();
  if(!selection?.rangeCount) return;
  const range=selection.getRangeAt(0);
  if(context.surface.contains(range.commonAncestorContainer)) context.savedRange=range.cloneRange();
}

function restoreRichSelection(context){
  if(!context?.surface?.isConnected) return false;
  const selection=getSelection();
  selection.removeAllRanges();
  if(context.savedRange){
    try{selection.addRange(context.savedRange);return true;}catch(_error){}
  }
  const range=document.createRange();
  range.selectNodeContents(context.surface);range.collapse(false);selection.addRange(range);context.savedRange=range.cloneRange();
  return true;
}

function setActiveRichContext(context){
  activeRichContext=context;
  saveRichSelection(context);
  renderChrome();
}

function insertTextAtCursor(textarea,text){
  const start=textarea.selectionStart??textarea.value.length;
  const end=textarea.selectionEnd??start;
  textarea.setRangeText(text,start,end,'end');
  textarea.dispatchEvent(new Event('input',{bubbles:true}));
  textarea.focus();
}

function equationComposer({source='',display='inline',title='Εξίσωση',acceptLabel='Εισαγωγή'}={}){
  return new Promise(resolve=>{
    $('#modalTitle').textContent=title;
    const body=$('#modalBody');body.innerHTML='';
    const shell=document.createElement('div');shell.className='equation-composer';
    const help=document.createElement('div');help.className='info-card';
    help.innerHTML='Γράψε απλή μαθηματική έκφραση ή χρησιμοποίησε σύνταξη όπως <code>x^2</code>, <code>E_{max}</code>, <code>\\frac{a}{b}</code>, <code>\\sqrt{x}</code>, <code>\\Delta</code>, <code>\\omega</code>.';
    const templates=document.createElement('div');templates.className='equation-template-bar';
    const input=document.createElement('textarea');input.className='equation-source-input';input.value=source||'';input.spellcheck=false;
    const preview=document.createElement('div');preview.className='equation-live-preview';
    const status=document.createElement('div');status.className='equation-parse-status';
    X.templates.forEach(template=>{const b=button(template.label,()=>insertTextAtCursor(input,template.insert));b.type='button';templates.appendChild(b);});
    const refresh=()=>{
      try{
        const mathml=X.sourceToMathML(input.value,display);
        preview.innerHTML=mathml;status.textContent='Έγκυρη εξίσωση';status.className='equation-parse-status good';
        input.dataset.mathml=mathml;
      }catch(error){preview.textContent='—';status.textContent=error.message;status.className='equation-parse-status bad';delete input.dataset.mathml;}
    };
    input.addEventListener('input',refresh);
    input.addEventListener('keydown',event=>{if(event.ctrlKey&&event.key==='Enter'){event.preventDefault();accept.click();}});
    shell.append(help,templates,input,preview,status);body.appendChild(shell);
    const host=$('#modalButtons');host.innerHTML='';
    const cancel=button('Ακύρωση',()=>{$('#modalBackdrop').classList.add('hidden');resolve(null);});
    const accept=button(acceptLabel,()=>{
      refresh();
      if(!input.dataset.mathml){status.textContent='Διόρθωσε πρώτα τη σύνταξη της εξίσωσης.';return;}
      $('#modalBackdrop').classList.add('hidden');resolve({source:input.value.trim(),mathml:input.dataset.mathml});
    },'primary');
    accept.classList.add('primary');host.append(cancel,accept);
    $('#modalBackdrop').classList.remove('hidden');
    refresh();setTimeout(()=>{input.focus();input.setSelectionRange(input.value.length,input.value.length);},0);
  });
}

async function editInlineMathElement(context,element){
  saveRichSelection(context);
  const result=await equationComposer({source:element.dataset.mathSource||'',display:'inline',title:'Επεξεργασία inline εξίσωσης',acceptLabel:'Εφαρμογή'});
  if(!result) return;
  const replacement=createInlineMathElement(result.source,result.mathml);
  element.replaceWith(replacement);
  context.surface.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
  const range=document.createRange();range.setStartAfter(replacement);range.collapse(true);context.savedRange=range;
  restoreRichSelection(context);
}

async function insertInlineEquation(){
  let context=activeRichContext;
  if(!context?.surface?.isConnected){
    const current=item();
    if(!current||!['paragraph','note','side_note'].includes(current.type)){
      return modal('Inline εξίσωση','<div class="info-card warn">Επίλεξε παράγραφο, σημείωση ή πλαϊνή σημείωση και τοποθέτησε τον δρομέα στο κείμενο.</div>');
    }
    state.propertiesOpen=true;state.tab='item';renderProperties();renderChrome();
    context={surface:$('.v42-rich-surface'),itemId:current.id};
    if(!context.surface) return;
    setActiveRichContext(context);restoreRichSelection(context);
  }
  saveRichSelection(context);
  const result=await equationComposer({display:'inline',title:'Inline εξίσωση στη θέση του δρομέα'});
  if(!result) return;
  context.suspendSelection=true;context.surface.focus();restoreRichSelection(context);context.suspendSelection=false;
  const selection=getSelection();const range=selection.rangeCount?selection.getRangeAt(0):null;
  const math=createInlineMathElement(result.source,result.mathml);
  if(range&&context.surface.contains(range.commonAncestorContainer)){
    range.deleteContents();range.insertNode(math);
  }else context.surface.appendChild(math);
  const spacer=document.createTextNode('\u200b');math.after(spacer);
  const after=document.createRange();after.setStartAfter(spacer);after.collapse(true);selection.removeAllRanges();selection.addRange(after);context.savedRange=after.cloneRange();
  context.surface.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
}

function execEditorCommand(surface,command,value=null,context=activeRichContext){
  if(context) context.suspendSelection=true;
  surface.focus();
  if(context) restoreRichSelection(context);
  if(context) context.suspendSelection=false;
  document.execCommand(command,false,value);
  if(context) saveRichSelection(context);
  surface.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'format'+command}));
}

function richEditor(current){
  const shell=document.createElement('div');shell.className='v42-rich-editor';
  const toolbar=document.createElement('div');toolbar.className='v42-rich-toolbar';
  const surface=document.createElement('div');surface.className='v42-rich-surface';surface.contentEditable='true';surface.spellcheck=true;
  surface.innerHTML=richTextToEditorHtml(current.body);
  const context={surface,itemId:current.id,savedRange:null,finish:null,suspendSelection:false};
  const commandButton=(label,command,title)=>{
    const b=document.createElement('button');b.type='button';b.textContent=label;b.title=title;
    b.onmousedown=event=>{event.preventDefault();saveRichSelection(context);};b.onclick=()=>execEditorCommand(surface,command,null,context);return b;
  };
  [['B','bold','Έντονα'],['I','italic','Πλάγια'],['U','underline','Υπογράμμιση'],['x₂','subscript','Δείκτης'],['x²','superscript','Εκθέτης'],['Tx','removeFormat','Καθαρισμός μορφοποίησης']].forEach(spec=>toolbar.appendChild(commandButton(...spec)));
  const color=document.createElement('input');color.type='color';color.value='#000000';color.title='Χρώμα κειμένου';color.onpointerdown=()=>saveRichSelection(context);color.oninput=()=>execEditorCommand(surface,'foreColor',color.value,context);toolbar.appendChild(color);
  const highlight=document.createElement('input');highlight.type='color';highlight.value='#fff2a8';highlight.title='Χρώμα επισήμανσης';highlight.onpointerdown=()=>saveRichSelection(context);highlight.oninput=()=>execEditorCommand(surface,'hiliteColor',highlight.value,context);toolbar.appendChild(highlight);
  const math=button('∑',()=>{setActiveRichContext(context);insertInlineEquation();});math.title='Inline εξίσωση στη θέση του δρομέα';math.onmousedown=event=>{event.preventDefault();saveRichSelection(context);};toolbar.appendChild(math);
  const link=button('🔗',()=>{const href=prompt('Διεύθυνση συνδέσμου ή #ID:','https://');if(href)execEditorCommand(surface,'createLink',href,context);});link.title='Σύνδεσμος';link.onmousedown=event=>{event.preventDefault();saveRichSelection(context);};toolbar.appendChild(link);
  const unlink=commandButton('⛓','unlink','Αφαίρεση συνδέσμου');toolbar.appendChild(unlink);
  const status=document.createElement('span');status.className='rich-editor-status';status.textContent='Canonical rich-text-v1';toolbar.appendChild(status);
  shell.append(toolbar,surface);
  const details=document.createElement('details');details.className='advanced-html';details.innerHTML='<summary>Προχωρημένα — παράγωγο HTML</summary>';
  const source=document.createElement('textarea');source.value=richTextToEditorHtml(current.body);source.className='monospace-input';details.appendChild(source);shell.appendChild(details);

  let beforeBook=null,beforeSelection=null,editing=false,timer=null;
  const begin=()=>{if(editing)return;editing=true;beforeBook=clone(session.book);beforeSelection=clone(session.selection);status.textContent='Επεξεργασία…';};
  const apply=()=>{
    if(!editing) begin();
    const clean=sanitizeEditorHtml(surface.innerHTML);
    const target=item();if(!target||target.id!==current.id)return;
    target.body=htmlToInlineRich(clean);
    target.provenance={...(target.provenance||{}),visualEdited:true,contentEdited:true,editedAt:new Date().toISOString()};
    target.extensions={...(target.extensions||{}),preferRichText:true,richTextSync:'canonical-primary-v4.2'};
    session.book.meta.updatedAt=new Date().toISOString();
    source.value=richTextToEditorHtml(target.body);
    updateTreeItemLabel(target);renderPreviewPageOnly(page().id);renderChrome();status.textContent='Μη αποθηκευμένο';
  };
  const finish=()=>{
    clearTimeout(timer);if(!editing)return;apply();editing=false;
    try{pushDirectEdit('Επεξεργασία canonical rich text',beforeBook,beforeSelection);status.textContent='Εφαρμόστηκε';}
    catch(error){status.textContent='Ακυρώθηκε';modal('Άκυρο περιεχόμενο',`<div class="info-card bad">${esc(error.message)}</div>`);}
  };
  context.finish=finish;
  surface.addEventListener('focus',()=>{setActiveRichContext(context);begin();});
  surface.addEventListener('mouseup',()=>saveRichSelection(context));surface.addEventListener('keyup',()=>saveRichSelection(context));
  surface.addEventListener('input',()=>{setActiveRichContext(context);begin();clearTimeout(timer);timer=setTimeout(apply,180);});
  surface.addEventListener('dblclick',event=>{const target=event.target.closest?.('.bw-inline-math');if(target&&surface.contains(target)){event.preventDefault();setActiveRichContext(context);editInlineMathElement(context,target);}});
  surface.addEventListener('keydown',event=>{if(event.altKey&&event.key.toLowerCase()==='m'){event.preventDefault();setActiveRichContext(context);insertInlineEquation();}});
  surface.addEventListener('blur',()=>setTimeout(()=>{if(!shell.contains(document.activeElement)&&!$('#modalBackdrop').classList.contains('hidden'))return;if(!shell.contains(document.activeElement))finish();},0));
  source.addEventListener('focus',begin);
  source.addEventListener('input',()=>{begin();surface.innerHTML=sanitizeEditorHtml(source.value);clearTimeout(timer);timer=setTimeout(apply,180);});
  source.addEventListener('blur',()=>setTimeout(()=>{if(!shell.contains(document.activeElement))finish();},0));
  return shell;
}

async function editSelectedDisplayEquation(){
  const current=item();if(!current||current.type!=='equation')return;
  const result=await equationComposer({source:current.source||'',display:'block',title:'Αυτόνομη εξίσωση',acceptLabel:'Εφαρμογή'});
  if(!result)return;
  mutate('Επεξεργασία αυτόνομης εξίσωσης',book=>{
    const target=book.pages[pageIndex()].items[itemIndex()];target.source=result.source;target.mathml=result.mathml;
    target.extensions={...(target.extensions||{}),mathSyntax:'bookwriter-math-v1'};return{pageId:page().id,itemId:target.id};
  });
}

function renderEquationEditor(cf,current,base){
  const preview=document.createElement('div');preview.className='display-equation-editor-preview';preview.innerHTML=current.mathml||'<span class="muted">Δεν έχει οριστεί εξίσωση.</span>';
  const edit=button(current.source?'Επεξεργασία εξίσωσης…':'Δημιουργία εξίσωσης…',editSelectedDisplayEquation,'primary-action');
  const actions=document.createElement('div');actions.className='asset-actions';actions.appendChild(edit);
  cf.append(preview,actions,
    field('Πηγή',current.source||'',v=>{
      try{const mathml=X.sourceToMathML(v,'block');mutate('Πηγή εξίσωσης',book=>{const target=book.pages[pageIndex()].items[itemIndex()];target.source=v;target.mathml=mathml;return{pageId:page().id,itemId:target.id};});}
      catch(error){modal('Άκυρη εξίσωση',`<div class="info-card bad">${esc(error.message)}</div>`);}
    },'textarea',null,{rows:4,monospace:true}),
    field('Αριθμός',current.number||'',v=>prop('Αριθμός εξίσωσης',[...base,'number'],v)),
    field('Λεζάντα',current.caption||'',v=>prop('Λεζάντα εξίσωσης',[...base,'caption'],v),'textarea'),
    field('Στοίχιση',current.style?.align||'center',v=>prop('Στοίχιση εξίσωσης',[...base,'style','align'],v),'text',[{value:'left',label:'Αριστερά'},{value:'center',label:'Κέντρο'},{value:'right',label:'Δεξιά'}])
  );
  const advanced=document.createElement('details');advanced.className='advanced-html';advanced.innerHTML='<summary>Προχωρημένα — MathML</summary>';
  const mathml=document.createElement('textarea');mathml.value=current.mathml||'';mathml.className='monospace-input';mathml.onchange=()=>prop('MathML εξίσωσης',[...base,'mathml'],mathml.value);advanced.appendChild(mathml);cf.appendChild(advanced);
}

async function chooseFigureAsset(){
  if(!item()||item().type!=='figure') return;
  try{
    if(!session.directoryHandle){
      session.directoryHandle=await showDirectoryPicker({mode:'readwrite'});
      state.name=session.directoryHandle.name;
    }
    if(!session.imagesDirectoryHandle) session.imagesDirectoryHandle=await session.directoryHandle.getDirectoryHandle('images',{create:true});
    let file;
    if(typeof showOpenFilePicker==='function'){
      const [handle]=await showOpenFilePicker({multiple:false,types:[{description:'Εικόνες',accept:{'image/*':['.png','.jpg','.jpeg','.gif','.webp','.svg']}}]});
      file=await handle.getFile();
    }else{
      throw new Error('Η επιλογή εικόνας απαιτεί Chrome ή Edge μέσω localhost.');
    }
    const ext=(file.name.match(/\.[A-Za-z0-9]+$/)||['.png'])[0].toLowerCase();
    const base=M.slugify(file.name.replace(/\.[^.]+$/,''))||'image';
    let name=base+ext,n=2;
    while(await BookServiceV4.exists(session.imagesDirectoryHandle,name)) name=`${base}-${n++}${ext}`;
    await BookServiceV4.writeNamed(session.imagesDirectoryHandle,name,file);
    mutate('Επιλογή εικόνας',book=>{
      C.Operations.setValue(book,['pages',pageIndex(),'items',itemIndex(),'src'],`images/${name}`);
      return {pageId:page().id,itemId:item().id};
    });
    await loadAssets();
    renderAll();
    setStatus(`Αντιγράφηκε images/${name}.`,'good');
  }catch(error){if(error.name!=='AbortError')modal('Επιλογή εικόνας',`<div class="info-card bad">${esc(error.message)}</div>`);}
}

function button(label,handler,className=''){
  const node=document.createElement('button');node.type='button';node.textContent=label;node.className=className;node.onclick=handler;return node;
}

function renderBookProperties(host){
  const book=session.book;
  const meta=section('Ταυτότητα βιβλίου');
  meta.querySelector('.property-form').append(
    field('Υπέρτιτλος',book.meta.eyebrow||'',v=>prop('Υπέρτιτλος',['meta','eyebrow'],v)),
    field('Τίτλος',book.meta.title||'',v=>prop('Αλλαγή τίτλου',['meta','title'],v)),
    field('Υπότιτλος',book.meta.subtitle||'',v=>prop('Αλλαγή υποτίτλου',['meta','subtitle'],v)),
    field('Project ID',book.meta.projectId||'',v=>prop('Project ID',['meta','projectId'],v)),
    field('Κλάδος Βιβλιοθήκης',book.meta.library?.discipline||'',v=>prop('Κλάδος Βιβλιοθήκης',['meta','library','discipline'],v)),
    field('Επίπεδο Βιβλιοθήκης',book.meta.library?.level||'',v=>prop('Επίπεδο Βιβλιοθήκης',['meta','library','level'],v)),
    field('Είδος Βιβλιοθήκης',book.meta.library?.category||'',v=>prop('Είδος Βιβλιοθήκης',['meta','library','category'],v)),
    field('Κατάσταση Βιβλιοθήκης',book.meta.library?.status||'',v=>prop('Κατάσταση Βιβλιοθήκης',['meta','library','status'],v)),
    field('Σύνδεσμος εφαρμογής',book.meta.appHref||'',v=>prop('Σύνδεσμος εφαρμογής',['meta','appHref'],v)),
    field('Όνομα αρχείου',book.meta.fileName||'book.json',v=>prop('Όνομα αρχείου',['meta','fileName'],v))
  );host.appendChild(meta);

  const pageSection=section('Χαρτί και περιθώρια');
  const f=pageSection.querySelector('.property-form'),l=book.layoutDefaults;
  f.append(
    field('Μέγεθος',l.pageSize||'A4',v=>prop('Μέγεθος χαρτιού',['layoutDefaults','pageSize'],v),'text',[{value:'A4',label:'A4'},{value:'Letter',label:'Letter'},{value:'custom',label:'Προσαρμοσμένο'}]),
    field('Προσανατολισμός',l.orientation||'portrait',v=>prop('Προσανατολισμός',['layoutDefaults','orientation'],v),'text',[{value:'portrait',label:'Κατακόρυφος'},{value:'landscape',label:'Οριζόντιος'}]),
    field('Πλάτος px',l.pageWidthPx,v=>prop('Πλάτος σελίδας',['layoutDefaults','pageWidthPx'],v),'number'),
    field('Ύψος px',l.pageHeightPx,v=>prop('Ύψος σελίδας',['layoutDefaults','pageHeightPx'],v),'number'),
    field('Πάνω περιθώριο',l.pagePaddingTopPx,v=>prop('Πάνω περιθώριο',['layoutDefaults','pagePaddingTopPx'],v),'number'),
    field('Δεξί περιθώριο',l.pagePaddingRightPx,v=>prop('Δεξί περιθώριο',['layoutDefaults','pagePaddingRightPx'],v),'number'),
    field('Κάτω περιθώριο',l.pagePaddingBottomPx,v=>prop('Κάτω περιθώριο',['layoutDefaults','pagePaddingBottomPx'],v),'number'),
    field('Αριστερό περιθώριο',l.pagePaddingLeftPx,v=>prop('Αριστερό περιθώριο',['layoutDefaults','pagePaddingLeftPx'],v),'number')
  );host.appendChild(pageSection);

  const type=section('Τυπογραφία σώματος και επικεφαλίδων');
  const tf=type.querySelector('.property-form');
  tf.append(
    field('Γραμματοσειρά σώματος',l.bodyFontFamily||'serif',v=>prop('Γραμματοσειρά σώματος',['layoutDefaults','bodyFontFamily'],v),'text',[{value:'serif',label:'Serif'},{value:'sans',label:'Sans serif'},{value:'classic',label:'Classic'},{value:'Georgia',label:'Georgia'},{value:'Arial',label:'Arial'}]),
    field('Μέγεθος σώματος',l.bodyFontSize,v=>prop('Μέγεθος σώματος',['layoutDefaults','bodyFontSize'],v),'number'),
    field('Διάστιχο',l.lineHeight,v=>prop('Διάστιχο',['layoutDefaults','lineHeight'],v),'number'),
    field('Κενό παραγράφων',l.paragraphGap,v=>prop('Κενό παραγράφων',['layoutDefaults','paragraphGap'],v),'number'),
    field('Γραμματοσειρά headings',l.headingFontFamily||'serif',v=>prop('Γραμματοσειρά headings',['layoutDefaults','headingFontFamily'],v)),
    field('Hero τίτλος',l.heroTitleFontSize,v=>prop('Hero τίτλος',['layoutDefaults','heroTitleFontSize'],v),'number'),
    field('Part τίτλος',l.partTitleFontSize,v=>prop('Part τίτλος',['layoutDefaults','partTitleFontSize'],v),'number'),
    field('Section heading',l.sectionHeadingFontSize,v=>prop('Section heading',['layoutDefaults','sectionHeadingFontSize'],v),'number'),
    field('Note μέγεθος',l.noteFontSize,v=>prop('Note μέγεθος',['layoutDefaults','noteFontSize'],v),'number'),
    field('Callout μέγεθος',l.calloutFontSize,v=>prop('Callout μέγεθος',['layoutDefaults','calloutFontSize'],v),'number'),
    field('Λεζάντα μέγεθος',l.captionFontSize,v=>prop('Λεζάντα μέγεθος',['layoutDefaults','captionFontSize'],v),'number')
  );host.appendChild(type);

  const frame=section('Κεφαλίδα, υποσέλιδο και αρίθμηση');
  const ff=frame.querySelector('.property-form'),pd=book.pageDefaults;
  ff.append(
    field('Header αριστερά',pd.header.left,v=>prop('Header',['pageDefaults','header','left'],v)),
    field('Header κέντρο',pd.header.center,v=>prop('Header',['pageDefaults','header','center'],v)),
    field('Header δεξιά',pd.header.right,v=>prop('Header',['pageDefaults','header','right'],v)),
    field('Footer αριστερά',pd.footer.left,v=>prop('Footer',['pageDefaults','footer','left'],v)),
    field('Footer κέντρο',pd.footer.center,v=>prop('Footer',['pageDefaults','footer','center'],v)),
    field('Footer δεξιά',pd.footer.right,v=>prop('Footer',['pageDefaults','footer','right'],v)),
    field('Header μέγεθος',l.headerFontSize,v=>prop('Header μέγεθος',['layoutDefaults','headerFontSize'],v),'number'),
    field('Footer μέγεθος',l.footerFontSize,v=>prop('Footer μέγεθος',['layoutDefaults','footerFontSize'],v),'number'),
    check('Εμφάνιση αριθμών σελίδας',l.showPageNumbers!==false,v=>prop('Αρίθμηση',['layoutDefaults','showPageNumbers'],v)),
    field('Αρχικός αριθμός',pd.pageNumbering.startAt??1,v=>prop('Αρχικός αριθμός',['pageDefaults','pageNumbering','startAt'],v),'number'),
    field('Θέση αριθμού',pd.pageNumbering.position||'footer-right',v=>prop('Θέση αριθμού',['pageDefaults','pageNumbering','position'],v),'text',[
      {value:'footer-left',label:'Υποσέλιδο αριστερά'},{value:'footer-center',label:'Υποσέλιδο κέντρο'},{value:'footer-right',label:'Υποσέλιδο δεξιά'},
      {value:'header-left',label:'Κεφαλίδα αριστερά'},{value:'header-center',label:'Κεφαλίδα κέντρο'},{value:'header-right',label:'Κεφαλίδα δεξιά'}
    ]),
    check('Απόκρυψη στην πρώτη σελίδα',!!pd.pageNumbering.hideOnFirstPage,v=>prop('Πρώτη σελίδα χωρίς αριθμό',['pageDefaults','pageNumbering','hideOnFirstPage'],v))
  );host.appendChild(frame);
}

function renderPageProperties(host){
  const current=page();
  if(!current){host.innerHTML='<div class="info-card">Δεν υπάρχει επιλεγμένη σελίδα.</div>';return;}
  const settings=section(`Σελίδα ${pageIndex()+1} από ${session.book.pages.length}`);
  settings.querySelector('.property-form').append(
    field('ID',current.id,v=>mutate('Αλλαγή ID σελίδας',book=>{const r=C.Operations.renameId(book,current.id,v);return{pageId:r.id};})),
    field('Source page',current.sourcePage??'',v=>prop('Source page',['pages',pageIndex(),'sourcePage'],v),'number'),
    check('Κληρονομεί header',current.header.inherit,v=>prop('Κληρονομικό header',['pages',pageIndex(),'header','inherit'],v)),
    field('Header αριστερά',current.header.left,v=>prop('Header',['pages',pageIndex(),'header','left'],v)),
    field('Header κέντρο',current.header.center,v=>prop('Header',['pages',pageIndex(),'header','center'],v)),
    field('Header δεξιά',current.header.right,v=>prop('Header',['pages',pageIndex(),'header','right'],v)),
    check('Κληρονομεί footer',current.footer.inherit,v=>prop('Κληρονομικό footer',['pages',pageIndex(),'footer','inherit'],v)),
    field('Footer αριστερά',current.footer.left,v=>prop('Footer',['pages',pageIndex(),'footer','left'],v)),
    field('Footer κέντρο',current.footer.center,v=>prop('Footer',['pages',pageIndex(),'footer','center'],v)),
    field('Footer δεξιά',current.footer.right,v=>prop('Footer',['pages',pageIndex(),'footer','right'],v)),
    check('Κληρονομεί αρίθμηση',current.pageNumbering.inherit!==false,v=>prop('Κληρονομική αρίθμηση',['pages',pageIndex(),'pageNumbering','inherit'],v)),
    check('Ενεργή αρίθμηση',current.pageNumbering.enabled!==false,v=>prop('Ενεργή αρίθμηση',['pages',pageIndex(),'pageNumbering','enabled'],v)),
    check('Απόκρυψη αριθμού',!!current.pageNumbering.hide,v=>prop('Απόκρυψη αριθμού',['pages',pageIndex(),'pageNumbering','hide'],v)),
    field('Offset αριθμού',current.pageNumbering.offset||0,v=>prop('Offset αριθμού',['pages',pageIndex(),'pageNumbering','offset'],v),'number')
  );host.appendChild(settings);

  const actions=section('Πράξεις σελίδας');
  const grid=document.createElement('div');grid.className='action-grid three-cols';
  [
    ['Νέα πριν',()=>insertPage('before')],['Νέα μετά',()=>insertPage('after')],
    ['Πάνω',()=>movePage(-1)],['Κάτω',()=>movePage(1)],['Κλωνοποίηση',clonePage],
    ['Συγχώνευση πριν',()=>mergePage('prev')],['Συγχώνευση μετά',()=>mergePage('next')]
  ].forEach(([label,fn])=>grid.appendChild(button(label,fn)));
  grid.appendChild(button('Διαγραφή σελίδας',deleteSelected,'danger'));
  actions.appendChild(grid);host.appendChild(actions);
}

function appendLayoutProperties(host,current,base){
  const layout=section('Διάταξη');
  const lf=layout.querySelector('.property-form');
  const value=current.layout||{};
  lf.append(
    field('Σχέση με float',value.floatInteraction||defaultFloatInteraction(current.type),v=>prop('Σχέση με float',[...base,'layout','floatInteraction'],v),'text',[
      {value:'wrap',label:'wrap — ροή γύρω'},{value:'avoid',label:'avoid — διαθέσιμη περιοχή'},{value:'clear',label:'clear — κάτω από float'}
    ]),
    field('Τοποθέτηση',value.placement||'',v=>prop('Τοποθέτηση',[...base,'layout','placement'],v),'text',[
      {value:'',label:'Αυτόματο'},{value:'wide',label:'Πλήρες πλάτος'},{value:'left',label:'Αριστερά'},{value:'right',label:'Δεξιά'},{value:'float-left',label:'Float αριστερά'},{value:'float-right',label:'Float δεξιά'}
    ]),
    field('Πλάτος px',value.widthPx??'',v=>prop('Πλάτος',[...base,'layout','widthPx'],v),'number'),
    field('Αναλογία',value.aspectRatio??'',v=>prop('Αναλογία',[...base,'layout','aspectRatio'],v), 'text',null,{placeholder:'natural ή 16/9'}),
    field('Ύψος px',value.heightPx??'',v=>prop('Ύψος',[...base,'layout','heightPx'],v),'number'),
    check('Wrap tight',!!value.wrap,v=>prop('Wrap',[...base,'layout','wrap'],v))
  );host.appendChild(layout);
}

function resizeTable(rows,cols){
  rows=Math.max(1,Math.min(30,Number(rows)||1));cols=Math.max(1,Math.min(12,Number(cols)||1));
  mutate('Αλλαγή διαστάσεων πίνακα',book=>{
    const target=book.pages[pageIndex()].items[itemIndex()];
    target.columns=cols;target.rows=Array.from({length:rows},(_,r)=>{
      const existing=target.rows?.[r]?.cells||[];
      return {cells:Array.from({length:cols},(_,c)=>existing[c]||{body:M.createRichText([])})};
    });
    return {pageId:page().id,itemId:target.id};
  });
}

function renderTableEditor(cf,current,base){
  const rows=current.rows?.length||1,cols=current.columns||Math.max(1,current.rows?.[0]?.cells?.length||1);
  const controls=document.createElement('div');controls.className='inline-controls';
  const rowInput=document.createElement('input');rowInput.type='number';rowInput.min='1';rowInput.max='30';rowInput.value=rows;rowInput.title='Γραμμές';
  const colInput=document.createElement('input');colInput.type='number';colInput.min='1';colInput.max='12';colInput.value=cols;colInput.title='Στήλες';
  controls.append('Γραμμές ',rowInput,' Στήλες ',colInput,button('Εφαρμογή',()=>resizeTable(rowInput.value,colInput.value)));
  cf.appendChild(controls);
  const grid=document.createElement('div');grid.className='table-cell-editor';grid.style.gridTemplateColumns=`repeat(${cols},minmax(110px,1fr))`;
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){
    const cell=current.rows?.[r]?.cells?.[c]||{body:M.createRichText([])};
    const input=document.createElement('textarea');input.value=M.richTextPlain(cell.body);input.placeholder=`R${r+1}C${c+1}`;
    input.onchange=()=>prop(`Κελί ${r+1},${c+1}`,[...base,'rows',r,'cells',c,'body'],M.createRichText(input.value?[M.createTextRun(input.value)]:[]));
    grid.appendChild(input);
  }cf.appendChild(grid);
}

function renderItemProperties(host){
  const current=item();
  if(!current){host.innerHTML='<div class="info-card">Επίλεξε item από τη δομή ή την προεπισκόπηση.</div>';return;}
  const base=['pages',pageIndex(),'items',itemIndex()];
  const identity=section('Ταυτότητα item');
  const form=identity.querySelector('.property-form');
  form.append(
    field('ID',current.id,v=>mutate('Αλλαγή ID item',book=>{const r=C.Operations.renameId(book,current.id,v);return{pageId:page().id,itemId:r.id};})),
    field('Τύπος',current.type,v=>mutate('Αλλαγή τύπου',book=>C.Operations.changeItemType(book,page().id,current.id,v)),'text',M.ITEM_TYPES.map(v=>({value:v,label:v}))),
    check('Εμφάνιση στο μενού',current.nav?.show,v=>prop('Συμμετοχή στο μενού',[...base,'nav','show'],v)),
    field('Ετικέτα μενού',current.nav?.label||'',v=>prop('Ετικέτα μενού',[...base,'nav','label'],v))
  );host.appendChild(identity);
  appendLayoutProperties(host,current,base);

  const content=section('Περιεχόμενο');
  const cf=content.querySelector('.property-form');
  if(current.type==='hero') cf.append(
    field('Eyebrow',current.eyebrow||'',v=>prop('Eyebrow',[...base,'eyebrow'],v)),
    field('Τίτλος',current.title||'',v=>prop('Τίτλος',[...base,'title'],v),'textarea'),
    field('Υπότιτλος',current.subtitle||'',v=>prop('Υπότιτλος',[...base,'subtitle'],v),'textarea')
  );
  else if(current.type==='part_title') cf.append(
    field('Ετικέτα',current.label||'',v=>prop('Ετικέτα μέρους',[...base,'label'],v)),
    field('Τίτλος',current.title||'',v=>prop('Τίτλος μέρους',[...base,'title'],v),'textarea')
  );
  else if(current.type==='section_heading') cf.append(
    field('Τίτλος',current.title||'',v=>prop('Τίτλος ενότητας',[...base,'title'],v),'textarea'),
    field('Επίπεδο',current.level||2,v=>prop('Επίπεδο heading',[...base,'level'],v),'number')
  );
  else if(current.type==='nav_anchor') cf.append(field('Τίτλος σημείου',current.title||'',v=>prop('Τίτλος σημείου',[...base,'title'],v)));
  else if(['paragraph','note','side_note'].includes(current.type)){
    if(current.type!=='paragraph') cf.append(field('Ετικέτα',current.label||'',v=>prop('Ετικέτα',[...base,'label'],v)));
    if(current.type==='side_note') cf.append(field('Τίτλος',current.title||'',v=>prop('Τίτλος σημείωσης',[...base,'title'],v)));
    if(current.type==='paragraph') cf.append(
      field('Στοίχιση',current.style?.align||'left',v=>prop('Στοίχιση',[...base,'style','align'],v),'text',[{value:'left',label:'Αριστερά'},{value:'justify',label:'Πλήρης'},{value:'center',label:'Κέντρο'},{value:'right',label:'Δεξιά'}]),
      check('Συλλαβισμός',current.style?.hyphenate!==false,v=>prop('Συλλαβισμός',[...base,'style','hyphenate'],v))
    );
    cf.appendChild(richEditor(current));
    const notice=document.createElement('div');notice.className='info-card editor-core-note good';notice.innerHTML='Το <code>rich-text-v1</code> είναι πλέον η ενεργή canonical πηγή. Δείκτες, εκθέτες, σύνδεσμοι και inline εξισώσεις αποθηκεύονται ως δομημένοι κόμβοι και όχι ως αδιαφανές HTML.';cf.appendChild(notice);
  }
  else if(current.type==='figure'){
    cf.append(
      field('src',current.src||'',v=>prop('Πηγή εικόνας',[...base,'src'],v),'textarea'),
      field('alt',current.alt||'',v=>prop('Alt',[...base,'alt'],v),'textarea'),
      field('Τίτλος',current.title||'',v=>prop('Τίτλος εικόνας',[...base,'title'],v)),
      field('Λεζάντα',current.caption||'',v=>prop('Λεζάντα',[...base,'caption'],v),'textarea'),
      check('Απόκρυψη λεζάντας',current.hideCaption,v=>prop('Απόκρυψη λεζάντας',[...base,'hideCaption'],v))
    );
    const asset=document.createElement('div');asset.className='asset-actions';asset.appendChild(button('Επιλογή και αντιγραφή εικόνας στον φάκελο images…',chooseFigureAsset,'primary-action'));cf.appendChild(asset);
  }
  else if(current.type==='scene') cf.append(
    field('URL σκηνής',current.src||'',v=>prop('URL σκηνής',[...base,'src'],v),'textarea',null,{rows:5,monospace:true}),
    field('Τίτλος',current.title||'',v=>prop('Τίτλος σκηνής',[...base,'title'],v)),
    field('Λεζάντα',current.caption||'',v=>prop('Λεζάντα σκηνής',[...base,'caption'],v),'textarea'),
    check('Απόκρυψη λεζάντας',current.hideCaption,v=>prop('Απόκρυψη λεζάντας',[...base,'hideCaption'],v)),
    check('Snapshot στην εκτύπωση',current.print?.snapshot!==false,v=>prop('Snapshot εκτύπωσης',[...base,'print','snapshot'],v))
  );
  else if(current.type==='interactive_callout') cf.append(
    field('Τίτλος',current.title||'',v=>prop('Τίτλος callout',[...base,'title'],v)),
    field('Label ρύθμισης',current.setupLabel||'Ρύθμισε',v=>prop('Label ρύθμισης',[...base,'setupLabel'],v)),
    field('Chips ρύθμισης — μία ανά γραμμή',(current.setupChips||[]).join('\n'),v=>prop('Chips ρύθμισης',[...base,'setupChips'],String(v).split(/\r?\n/).map(x=>x.trim()).filter(Boolean)),'textarea'),
    field('Label πλήκτρων',current.pressLabel||'Πίεσε',v=>prop('Label πλήκτρων',[...base,'pressLabel'],v)),
    field('Chips πλήκτρων — μία ανά γραμμή',(current.pressChips||[]).join('\n'),v=>prop('Chips πλήκτρων',[...base,'pressChips'],String(v).split(/\r?\n/).map(x=>x.trim()).filter(Boolean)),'textarea'),
    field('Τίτλος παρατήρησης',current.observeTitle||'Παρατήρησε',v=>prop('Τίτλος παρατήρησης',[...base,'observeTitle'],v)),
    field('Παρατηρήσεις — μία ανά γραμμή',(current.observeItems||[]).join('\n'),v=>prop('Στοιχεία παρατήρησης',[...base,'observeItems'],String(v).split(/\r?\n/).map(x=>x.trim()).filter(Boolean)),'textarea',null,{rows:6})
  );
  else if(current.type==='list') cf.append(
    check('Αριθμημένη λίστα',!!current.ordered,v=>prop('Τύπος λίστας',[...base,'ordered'],v)),
    field('Έναρξη',current.start||1,v=>prop('Έναρξη λίστας',[...base,'start'],v),'number'),
    field('Στοιχεία — μία γραμμή ανά στοιχείο',(current.items||[]).map(entry=>M.richTextPlain(entry.body)).join('\n'),v=>prop('Στοιχεία λίστας',[...base,'items'],String(v).split(/\r?\n/).map(text=>({body:M.createRichText(text?[M.createTextRun(text)]:[])}))),'textarea',null,{rows:10})
  );
  else if(current.type==='table') renderTableEditor(cf,current,base);
  else if(current.type==='equation') renderEquationEditor(cf,current,base);
  else if(current.type==='clear') cf.innerHTML='<div class="info-card">Το clear κλείνει ενεργά floats και δεν έχει περιεχόμενο.</div>';
  else cf.appendChild(document.createTextNode('Δεν υπάρχουν ειδικά πεδία για αυτόν τον τύπο.'));
  host.appendChild(content);

  const actions=section('Δομικές πράξεις item');
  const grid=document.createElement('div');grid.className='action-grid three-cols';
  [
    ['Νέο πριν',()=>insertItem(null,'before')],['Νέο μετά',()=>insertItem(null,'after')],
    ['Πάνω',()=>moveItem(-1)],['Κάτω',()=>moveItem(1)],
    ['Προηγ. σελίδα',()=>moveAcross('prev')],['Επόμ. σελίδα',()=>moveAcross('next')],
    ['Νέα σελίδα από εδώ',splitPage],['Κλωνοποίηση',cloneSelected]
  ].forEach(([label,fn])=>grid.appendChild(button(label,fn)));
  grid.appendChild(button('Διαγραφή item',deleteSelected,'danger'));
  actions.appendChild(grid);host.appendChild(actions);
}

function autoNavTargets(){
  const targets=[];
  session.book.pages.forEach((p,pi)=>p.items.forEach((current,ii)=>{
    if(BookCore.isStructuralNavItem(current)) targets.push({page:p,pageIndex:pi,item:current,itemIndex:ii,title:BookCore.itemNavTitle(current,itemSummary(current),'el')});
  }));
  return targets;
}

function convertAutoNavToManual(){
  const groups=[];let group=null;
  for(const target of autoNavTargets()){
    if(target.item.type==='part_title'||!group){
      group={title:target.title||`Μέρος ${groups.length+1}`,target:target.item.id,hidden:false,entries:[]};groups.push(group);
    }else if(target.item.nav?.show){
      group.entries.push({title:target.title,target:target.item.id,hidden:false});
    }
  }
  mutate('Δημιουργία χειροκίνητου μενού',book=>{
    book.nav=book.nav||{};
    book.nav.groups=structuredClone(groups);
    book.nav.mode='manual';
    return{pageId:page()?.id||book.pages[0]?.id};
  });
}

function renderManualNavGroups(host){
  const groups=session.book.nav?.groups||[];
  const sectionNode=section('Χειροκίνητες ομάδες');
  const form=sectionNode.querySelector('.property-form');
  form.appendChild(button('Δημιουργία από τους αυτόματους στόχους',convertAutoNavToManual,'primary-action'));
  groups.forEach((group,index)=>{
    const card=document.createElement('div');card.className='nav-group-card';
    card.innerHTML=`<div class="nav-group-head"><b>Ομάδα ${index+1}</b></div>`;
    const body=document.createElement('div');body.className='nav-group-body';
    body.append(
      field('Τίτλος',group.title||'',v=>prop('Τίτλος ομάδας',['nav','groups',index,'title'],v)),
      field('Στόχος',group.target||group.id||'',v=>prop('Στόχος ομάδας',['nav','groups',index,'target'],v)),
      check('Κρυφή',!!group.hidden,v=>prop('Απόκρυψη ομάδας',['nav','groups',index,'hidden'],v)),
      field('Επιλογές: τίτλος | target | hidden',(group.entries||[]).map(entry=>`${entry.title||''} | ${entry.target||entry.id||''} | ${entry.hidden?'1':'0'}`).join('\n'),v=>{
        const entries=String(v).split(/\r?\n/).filter(Boolean).map(line=>{const [title,target,hidden]=line.split('|').map(x=>x.trim());return{title,target,hidden:hidden==='1'||hidden==='true'};});
        prop('Επιλογές ομάδας',['nav','groups',index,'entries'],entries);
      },'textarea',null,{rows:6})
    );
    const actions=document.createElement('div');actions.className='inline-controls';
    actions.append(
      button('↑',()=>{if(index>0)mutate('Ομάδα πάνω',book=>{const a=book.nav.groups;[a[index-1],a[index]]=[a[index],a[index-1]];return{pageId:page().id};});}),
      button('↓',()=>{if(index<groups.length-1)mutate('Ομάδα κάτω',book=>{const a=book.nav.groups;[a[index+1],a[index]]=[a[index],a[index+1]];return{pageId:page().id};});}),
      button('Διαγραφή',()=>mutate('Διαγραφή ομάδας',book=>{book.nav.groups.splice(index,1);return{pageId:page().id};}),'danger')
    );
    body.appendChild(actions);card.appendChild(body);form.appendChild(card);
  });
  form.appendChild(button('＋ Νέα ομάδα',()=>mutate('Νέα ομάδα μενού',book=>{book.nav.groups.push({title:'Νέα ομάδα',target:page()?.id||book.pages[0].id,hidden:false,entries:[]});return{pageId:page()?.id||book.pages[0].id};})));
  host.appendChild(sectionNode);
}

function renderNavProperties(host){
  const nav=section('Μενού και πλοήγηση');
  nav.querySelector('.property-form').append(
    field('Τρόπος',session.book.nav?.mode||'auto',v=>prop('Τρόπος μενού',['nav','mode'],v),'text',[{value:'auto',label:'Αυτόματο'},{value:'manual',label:'Χειροκίνητο'}]),
    check('Σύνδεσμος εφαρμογής',session.book.nav?.showApp!==false,v=>prop('Σύνδεσμος εφαρμογής',['nav','showApp'],v)),
    check('Σύνδεσμος εκτύπωσης',session.book.nav?.showPrint!==false,v=>prop('Σύνδεσμος εκτύπωσης',['nav','showPrint'],v))
  );host.appendChild(nav);

  const targets=section('Στόχοι πλοήγησης');
  const tf=targets.querySelector('.property-form');
  autoNavTargets().forEach(target=>{
    const row=document.createElement('div');row.className='nav-target-row';
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=target.item.nav?.show!==false;
    checkbox.onchange=()=>prop('Συμμετοχή στο μενού',['pages',target.pageIndex,'items',target.itemIndex,'nav','show'],checkbox.checked);
    const type=document.createElement('code');type.textContent=target.item.type;
    const label=document.createElement('input');label.value=target.item.nav?.label||target.title||'';label.onchange=()=>prop('Ετικέτα στόχου',['pages',target.pageIndex,'items',target.itemIndex,'nav','label'],label.value);
    const jump=button('↗',()=>select(target.page.id,target.item.id,'tree'));jump.title='Μετάβαση στο item';
    row.append(checkbox,type,label,jump);tf.appendChild(row);
  });host.appendChild(targets);
  renderManualNavGroups(host);
  const raw=section('Προχωρημένα — JSON ομάδων');
  raw.querySelector('.property-form').append(field('JSON',JSON.stringify(session.book.nav?.groups||[],null,2),v=>{
    try{prop('Ομάδες μενού',['nav','groups'],JSON.parse(v));}catch(error){modal('Άκυρο JSON',`<div class="info-card bad">${esc(error.message)}</div>`);}
  },'textarea',null,{rows:12,monospace:true}));host.appendChild(raw);
}



function activeDocxRoot(){return state.view==='insert'?$('#insertView'):$('#docxView')}
function renderActiveDocxWorkspace(){if(state.view==='insert')renderInsertWorkspace();else renderDocxView()}
function setInsertionTarget(pageId,itemId){
  const insertion=state.docx.insertion;if(!insertion)return;
  const targetPage=session.book.pages.find(current=>current.id===pageId)||session.book.pages[0];
  const targetItem=targetPage?.items?.find(current=>current.id===itemId)||targetPage?.items?.[0];
  if(!targetPage||!targetItem)return;
  session.selection={pageId:targetPage.id,itemId:targetItem.id};
  insertion.anchor={pageId:targetPage.id,itemId:targetItem.id,label:itemSummary(targetItem)};
  insertion.draft=null;
  renderInsertWorkspace();
  requestAnimationFrame(()=>{
    const preview=$(`#insertBookPreviewPages [data-insert-preview-item="${CSS.escape(targetItem.id)}"]`);
    preview?.scrollIntoView({block:'center',behavior:'smooth'});
  });
}
function insertTreeHtml(){
  if(!has())return '<div class="tree-empty">Δεν υπάρχει ανοιχτό βιβλίο.</div>';
  const anchor=state.docx.insertion?.anchor,query=String($('#insertBookSearch')?.value||'').trim().toLowerCase();let count=0,html='';
  session.book.pages.forEach((currentPage,pageIdx)=>{
    const pageText=`${pageIdx+1} ${currentPage.id}`.toLowerCase();
    const pageVisible=!query||pageText.includes(query)||(currentPage.items||[]).some(current=>`${current.type} ${itemSummary(current)} ${current.id}`.toLowerCase().includes(query));
    if(!pageVisible)return;
    html+=`<div class="tree-row page" data-insert-tree-page="${esc(currentPage.id)}"><span class="tree-icon">▤</span><span class="tree-label">${pageIdx+1}. ${esc(currentPage.id)}</span></div>`;
    (currentPage.items||[]).forEach(current=>{const text=`${current.type} ${itemSummary(current)} ${current.id}`.toLowerCase();if(query&&!text.includes(query))return;count++;const selected=anchor?.pageId===currentPage.id&&anchor?.itemId===current.id?' anchor-selected':'';html+=`<div class="tree-row item-type${selected}" data-insert-tree-page-id="${esc(currentPage.id)}" data-insert-tree-item="${esc(current.id)}"><span class="tree-type">${esc(current.type)}</span><span class="tree-label">${esc(itemSummary(current))}</span></div>`});
  });
  $('#insertBookCounts').textContent=`${session.book.pages.length} σελίδες · ${count} ορατά items`;
  return html||'<div class="tree-empty">Δεν βρέθηκαν items.</div>';
}
function bindInsertBookTree(){
  $$('[data-insert-tree-item]').forEach(row=>row.onclick=()=>setInsertionTarget(row.dataset.insertTreePageId,row.dataset.insertTreeItem));
  $$('[data-insert-tree-page]').forEach(row=>row.onclick=()=>{const p=session.book.pages.find(current=>current.id===row.dataset.insertTreePage),it=p?.items?.[0];if(it)setInsertionTarget(p.id,it.id)});
}
function insertBookData(){const draft=state.docx.insertion?.draft;return{book:draft?.book||session.book,draft,candidates:draft?insertionImageCandidates(draft):imageCandidates}}
function insertFitBookZoom(announce=false){
  const scroller=$('#insertBookPreviewScroller'),width=Number(session.book?.layoutDefaults?.pageWidthPx||794);if(!scroller||!width)return;
  state.insert.bookZoomMode='fit';state.insert.bookZoom=Math.max(.28,Math.min(1.05,(scroller.clientWidth-28)/width));
  storage.set('bw-v4_4-insert-book-zoom',String(state.insert.bookZoom));storage.set('bw-v4_4-insert-book-zoom-mode',state.insert.bookZoomMode);
  renderInsertBookPreview();if(announce)setStatus(`Προεπισκόπηση βιβλίου ${Math.round(state.insert.bookZoom*100)}%.`);
}
function changeInsertBookZoom(delta=0,mode='manual'){
  state.insert.bookZoomMode=mode;state.insert.bookZoom=mode==='100'?1:Math.max(.28,Math.min(1.6,state.insert.bookZoom+delta));
  if(mode==='100')state.insert.bookZoomMode='manual';
  storage.set('bw-v4_4-insert-book-zoom',String(state.insert.bookZoom));storage.set('bw-v4_4-insert-book-zoom-mode',state.insert.bookZoomMode);renderInsertBookPreview();
}
function renderInsertBookPreview(){
  const host=$('#insertBookPreviewPages');if(!host||!has())return;host.innerHTML='';const {book,draft,candidates}=insertBookData(),inserted=new Set(draft?.insertedIds||[]),anchor=state.docx.insertion?.anchor,zoom=Number(state.insert.bookZoom)||.56;
  $('#insertDraftBanner').hidden=!draft;$('#insertDraftBanner').innerHTML=draft?`<b>Προσωρινή προεπισκόπηση.</b> ${draft.insertedIds.length} νέα blocks εμφανίζονται μέσα στο βιβλίο με πράσινο περίγραμμα. Δεν έχει γίνει αποθήκευση.`:'';
  book.pages.forEach((currentPage,index)=>{
    const node=BookCore.renderPageNode(book,currentPage,index,{lang:'el',preview:false,editor:true,sceneSource:src=>state.realScenes?resolveSceneSource(src):'',imageCandidates:candidates});
    node.dataset.insertPreviewPage=currentPage.id;node.style.setProperty('--screen-scale',String(zoom));
    const body=node.querySelector('.sheet-body'),rendered=body?[...body.children]:[];
    currentPage.items.forEach((current,itemIdx)=>{const el=rendered[itemIdx];if(!el)return;el.dataset.insertPreviewItem=current.id;el.classList.add('preview-hit-target');if(inserted.has(current.id))el.classList.add('flow-inserted-item');if(!draft&&anchor?.pageId===currentPage.id&&anchor?.itemId===current.id){const marker=document.createElement('div');marker.className='insertion-marker';if(state.docx.insertion.position==='before')body.insertBefore(marker,el);else body.insertBefore(marker,el.nextSibling)}if(!inserted.has(current.id)&&session.book.pages.some(p=>(p.items||[]).some(it=>it.id===current.id)))el.onclick=event=>{event.stopPropagation();const p=session.book.pages.find(p=>(p.items||[]).some(it=>it.id===current.id));if(p)setInsertionTarget(p.id,current.id)}});
    host.appendChild(node);
  });
  requestAnimationFrame(()=>{const id=draft?.firstInsertedId||anchor?.itemId;if(id)host.querySelector(`[data-insert-preview-item="${CSS.escape(id)}"]`)?.scrollIntoView({block:'center'})});
}
function docxPreviewHtml(){if(!state.docx.entries.length)return'<div class="tree-empty">Δεν έχει ανοιχτεί DOCX.</div>';const selected=new Set(docxRange().map(x=>x.key));return state.docx.entries.map(e=>{const edge=e.key===state.docx.startKey||e.key===state.docx.endKey,cls=['docx-preview-entry',e.heading?'heading':'',selected.has(e.key)?'range-selected':'',edge?'range-edge':''].filter(Boolean).join(' ');return`<div class="${cls}" data-docx-preview-key="${esc(e.key)}">${docxEntryHtml(e)}</div>`}).join('')}
function renderInsertDocxSource(){
  const tree=$('#insertDocxTree'),preview=$('#insertDocxPreview');if(!tree||!preview)return;tree.innerHTML=docxTreeHtml();preview.innerHTML=docxPreviewHtml();bindDocxPanels($('#insertView'));$('#insertDocxSelectionCaption').textContent=docxRangeSummary();$('#insertDocxFileCaption').textContent=state.docx.file?.name||'Δεν έχει ανοιχτεί DOCX';
}
function renderInsertSummary(){
  const range=docxRange(),insertion=state.docx.insertion,draft=insertion?.draft,ready=!!state.docx.result&&range.length&&!state.busy;$('#insertTargetCaption').textContent=insertion?.anchor?`${insertion.position==='before'?'Πριν':'Μετά'} από: ${insertion.anchor.label}`:'Επίλεξε block αριστερά';$('#insertPosition').value=insertion?.position||'after';$('#insertPreviewButton').disabled=!ready;const duplicate=draft?.duplicateEvidence;$('#insertAllowDuplicateLabel').hidden=!duplicate;$('#insertApplyButton').disabled=!draft||!!duplicate&&!$('#insertAllowDuplicate').checked;$('#insertSummary').innerHTML=!state.docx.result?'<b>1.</b> Επίλεξε σημείο στο βιβλίο αριστερά · <b>2.</b> άνοιξε DOCX δεξιά · <b>3.</b> επίλεξε τμήμα.':`<b>Στόχος:</b> ${esc(insertion?.anchor?.label||'—')} · <b>Πηγή:</b> ${esc(state.docx.file?.name||'—')} · <b>Επιλογή:</b> ${esc(docxRangeSummary())}${draft?` · <b>Προσωρινό αποτέλεσμα:</b> ${draft.insertedIds.length} blocks / ${draft.generatedPages.length} επηρεασμένες σελίδες / ${draft.fullAudit?.overflowPages||0} overflow`:''}`;
}
function renderInsertWorkspace(){
  if(state.view!=='insert'||!has())return;document.documentElement.style.setProperty('--insert-left',`${Math.max(.34,Math.min(.66,state.insert.split))*100}%`);$('#insertBookTree').innerHTML=insertTreeHtml();bindInsertBookTree();renderInsertBookPreview();renderInsertDocxSource();renderInsertSummary();renderChrome();if(state.insert.bookZoomMode==='fit')requestAnimationFrame(()=>{const scroller=$('#insertBookPreviewScroller'),width=Number(session.book?.layoutDefaults?.pageWidthPx||794),next=Math.max(.28,Math.min(1.05,(scroller.clientWidth-28)/width));if(Math.abs(next-state.insert.bookZoom)>.01){state.insert.bookZoom=next;storage.set('bw-v4_4-insert-book-zoom',String(next));renderInsertBookPreview()}})
}
function bindInsertSplitter(){
  const splitter=$('#insertMainSplitter');if(!splitter)return;splitter.addEventListener('pointerdown',event=>{event.preventDefault();splitter.setPointerCapture(event.pointerId);splitter.classList.add('dragging')});splitter.addEventListener('pointermove',event=>{if(!splitter.hasPointerCapture(event.pointerId))return;const host=$('#insertView').getBoundingClientRect(),ratio=(event.clientX-host.left)/host.width;state.insert.split=Math.max(.34,Math.min(.66,ratio));document.documentElement.style.setProperty('--insert-left',`${state.insert.split*100}%`)});const end=event=>{if(splitter.hasPointerCapture(event.pointerId))splitter.releasePointerCapture(event.pointerId);splitter.classList.remove('dragging');storage.set('bw-v4_4-insert-split',String(state.insert.split));if(state.insert.bookZoomMode==='fit')insertFitBookZoom(false)};splitter.addEventListener('pointerup',end);splitter.addEventListener('pointercancel',end)
}

function resetDocxState(options={}){
  for(const url of state.docx.blobUrls.values())if(String(url).startsWith('blob:'))URL.revokeObjectURL(url);
  state.docx={mode:options.mode||'create',file:null,result:null,entries:[],startKey:'',endKey:'',anchorKey:'',focusKey:'',blobUrls:new Map(),report:null,insertion:options.insertion||null};
}
function startDocxCreate(){resetDocxState({mode:'create'});setView('docx');renderDocxView();setStatus('Άνοιξε DOCX. Απλό κλικ επιλέγει block, Shift+κλικ περιοχή, διπλό κλικ ολόκληρη ενότητα.');}
function startDocxInsert(){
  if(!has())return;let targetPage=page()||session.book.pages[0],targetItem=item()||targetPage?.items?.[0];
  if(!targetPage||!targetItem){modal('Παρεμβολή από Word','<div class="info-card warn">Το βιβλίο δεν διαθέτει block προορισμού. Πρόσθεσε πρώτα ένα item.</div>');return}
  const anchor={pageId:targetPage.id,itemId:targetItem.id,label:itemSummary(targetItem)};
  resetDocxState({mode:'insert',insertion:{anchor,position:'after',draft:null}});
  setView('insert');setStatus('Αριστερά επίλεξε το σημείο στο βιβλίο. Δεξιά άνοιξε το DOCX και επίλεξε το τμήμα που θα εισαχθεί.');
}
function docxIndex(key){return state.docx.entries.findIndex(entry=>entry.key===key)}
function docxRange(){return D.entriesInRange(state.docx.result,state.docx.startKey,state.docx.endKey)}
function selectDocx(key,shift=false,origin='tree'){
 const index=docxIndex(key);if(index<0)return;if(shift&&state.docx.anchorKey){const a=docxIndex(state.docx.anchorKey),lo=Math.min(a,index),hi=Math.max(a,index);state.docx.startKey=state.docx.entries[lo].key;state.docx.endKey=state.docx.entries[hi].key}else{state.docx.startKey=key;state.docx.endKey=key;state.docx.anchorKey=key}state.docx.focusKey=key;if(state.docx.insertion)state.docx.insertion.draft=null;renderActiveDocxWorkspace();requestAnimationFrame(()=>syncDocxPanels(key,origin));
}
function selectDocxSection(key){const i=docxIndex(key);if(i<0)return;let start=i;while(start>0&&!state.docx.entries[start].heading)start--;if(!state.docx.entries[start].heading)start=i;const level=state.docx.entries[start].level||99;let end=start;for(let j=start+1;j<state.docx.entries.length;j++){const e=state.docx.entries[j];if(e.heading&&(e.level||99)<=level)break;end=j}state.docx.startKey=state.docx.entries[start].key;state.docx.endKey=state.docx.entries[end].key;state.docx.anchorKey=state.docx.startKey;state.docx.focusKey=state.docx.startKey;if(state.docx.insertion)state.docx.insertion.draft=null;renderActiveDocxWorkspace();requestAnimationFrame(()=>syncDocxPanels(state.docx.startKey,'tree'))}
function syncDocxPanels(key,origin){const root=activeDocxRoot()||document,tree=root.querySelector(`[data-docx-tree-key="${CSS.escape(key)}"]`),preview=root.querySelector(`[data-docx-preview-key="${CSS.escape(key)}"]`);if(origin!=='tree')tree?.scrollIntoView({block:'center'});if(origin!=='preview')preview?.scrollIntoView({block:'center'})}
function docxTreeHtml(){if(!state.docx.entries.length)return'<div class="tree-empty">Δεν έχει ανοιχτεί DOCX.</div>';const selected=new Set(docxRange().map(x=>x.key));return state.docx.entries.map(e=>{const edge=e.key===state.docx.startKey||e.key===state.docx.endKey,level=e.heading?Math.min(4,e.level||2):0,cls=['tree-row',level?'docx-h'+level:'',selected.has(e.key)?'range-selected':'',edge?'range-edge':''].filter(Boolean).join(' '),icon=e.heading?'▸':e.type==='figure'?'▧':e.block?.source==='table'?'▦':e.block?.source==='list'?'☷':'¶';return`<div class="${cls}" data-docx-tree-key="${esc(e.key)}"><span class="tree-icon">${icon}</span><span class="tree-label" title="${esc(D.entryLabel(e))}">${esc(D.entryLabel(e))}</span></div>`}).join('')}
function docxEntryHtml(e){const b=e.block;if(b.type==='part_title')return`<h2>${esc(b.title||'')}</h2>`;if(b.type==='section_heading')return`<h3>${esc(b.title||'')}</h3>`;if(b.type==='paragraph')return D.formattedParagraphHtml(b);if(b.type==='table'){const rows=(b.rows||[]).map(r=>`<tr>${(r.cells||[]).map(c=>`<td colspan="${Number(c.colspan)||1}" rowspan="${Number(c.rowspan)||1}">${c.html||''}</td>`).join('')}</tr>`).join('');return`<table><tbody>${rows}</tbody></table>`}if(b.type==='list'){const tag=b.listType==='ol'?'ol':'ul',start=b.listType==='ol'&&Number.isFinite(Number(b.start))?` start="${Number(b.start)}"`:'',style=b.numFmt==='lowerLetter'?' style="list-style-type:lower-greek"':'';return`<${tag}${start}${style}>${(b.items||[]).map(x=>`<li${Number.isFinite(Number(x.value))?` value="${Number(x.value)}"`:''}>${x.html||''}</li>`).join('')}</${tag}>`}if(b.type==='figure'){const u=state.docx.blobUrls.get(b.srcPath);return`<figure>${u?`<img src="${u}" alt="">`:'<div>Μη διαθέσιμη εικόνα</div>'}${b.caption?`<figcaption>${esc(b.caption)}</figcaption>`:''}</figure>`}return esc(D.blockText(b)||b.type)}
function renderDocxPreview(){const host=$('#docxSourcePreview');if(host)host.innerHTML=docxPreviewHtml()}
function bindDocxPanels(root=document){for(const row of root.querySelectorAll('[data-docx-tree-key]')){row.onclick=event=>selectDocx(row.dataset.docxTreeKey,event.shiftKey,'tree');row.ondblclick=()=>selectDocxSection(row.dataset.docxTreeKey)}for(const row of root.querySelectorAll('[data-docx-preview-key]')){row.onclick=event=>selectDocx(row.dataset.docxPreviewKey,event.shiftKey,'preview');row.ondblclick=()=>selectDocxSection(row.dataset.docxPreviewKey)}}
function docxRangeSummary(){const range=docxRange();if(!range.length)return'Χωρίς επιλογή';return`${range.length} blocks · ${range.filter(x=>x.heading).length} επικεφαλίδες · ${range.filter(x=>x.type==='figure').length} εικόνες · σ.${range[0].page}–${range.at(-1).page}`}
function renderDocxMetrics(){const host=$('#docxMetrics');if(!host)return;const r=state.docx.result;if(!r){host.innerHTML='';return}const range=docxRange(),metrics=[['Σελίδες Word',r.pageCount],['Επιλεγμένα blocks',range.length],['Εξισώσεις',`${r.importedMathObjects} canonical / ${r.mathCount} raw`],['Εικόνες',r.usedImages.length],['Πίνακες',r.tables],['Πλαίσια κειμένου',`${r.textBoxesImported}/${r.textBoxesUnique}`]];host.innerHTML=metrics.map(([label,value])=>`<div class="docx-metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`).join('')}
function updateDocxCreateSummary(){
  const range=docxRange(),title=$('#docxBookTitle')?.value.trim()||'',id=D.safeId($('#docxBookId')?.value||''),ready=!!state.docx.result&&range.length&&!state.busy&&title&&id,summary=$('#docxSummary');
  $('#docxCreateButton').disabled=!ready;$('#docxReportButton').disabled=!state.docx.report;
  if(!state.docx.result){summary.className='info-card';summary.innerHTML='Άνοιξε DOCX και επίλεξε το τμήμα που θα αποτελέσει το νέο βιβλίο.';return}
  const warnings=[];if(state.docx.result.textBoxesUnique)warnings.push(`${state.docx.result.textBoxesImported} πλαίσια μεταφέρονται ως canonical notes και ${state.docx.result.textBoxCaptions} λεζάντες συνδέονται με εικόνες.`);if(state.docx.result.unsupportedMath?.length)warnings.push('Μερικώς χαρτογραφημένα OMML: '+state.docx.result.unsupportedMath.join(', '));
  summary.className='info-card '+(ready?'good':'warn');
  summary.innerHTML=`<b>Πηγή:</b> ${esc(state.docx.file.name)}<br><b>Επιλογή:</b> ${esc(docxRangeSummary())}<br><b>Νέος φάκελος:</b> books/${esc(id||'…')}/<br><b>Έξοδος:</b> canonical bookwriter-v4 με πραγματική reflow σελιδοποίηση${warnings.length?`<div class="docx-warning-list">${warnings.map(x=>'• '+esc(x)).join('<br>')}</div>`:''}`;
}
function renderDocxView(){
  if(state.view!=='docx')return;
  const tree=$('#docxSourceTree');tree.innerHTML=docxTreeHtml();renderDocxPreview();bindDocxPanels($('#docxView'));$('#docxSelectionCaption').textContent=docxRangeSummary();renderDocxMetrics();updateDocxCreateSummary();renderChrome();
}
async function analyzeDocxFile(file){
  state.busy=true;renderChrome();setStatus('Ανάλυση DOCX: '+file.name+'…','warn');
  const workflow={mode:state.docx.mode,insertion:state.docx.insertion};
  try{const result=await D.parseDocx(file),entries=D.flattenEntries(result);resetDocxState(workflow);state.docx.file=file;state.docx.result=result;state.docx.entries=entries;if(entries.length){state.docx.startKey=entries[0].key;state.docx.endKey=entries.at(-1).key;state.docx.anchorKey=entries[0].key;state.docx.focusKey=entries[0].key}for(const[path,blob]of result.imageBlobs)state.docx.blobUrls.set(path,URL.createObjectURL(blob));state.docx.report=D.audit(result,docxRange());const raw=D.safeId(file.name.replace(/\.docx$/i,'').toLowerCase());$('#docxBookTitle').value=file.name.replace(/\.docx$/i,'');$('#docxBookId').value=/[A-Za-z]/.test(raw)?raw:'book_'+(raw||'new');setStatus(`Η ανάλυση ολοκληρώθηκε: ${entries.length} blocks, ${result.pageCount} σελίδες Word.`,'good')}catch(error){console.error(error);setStatus('Αποτυχία ανάλυσης DOCX: '+error.message,'bad');modal('Αποτυχία DOCX',`<div class="info-card bad">${esc(error.message)}</div>`)}finally{state.busy=false;renderActiveDocxWorkspace()}
}
async function createBookFromDocx(){
  if(!await allowReplaceCurrentBook('δημιουργία και άνοιγμα νέου βιβλίου από Word'))return;
  const entries=docxRange(),title=$('#docxBookTitle').value.trim(),id=D.safeId($('#docxBookId').value);if(!entries.length||!title||!id)return;
  try{
    const root=await requireLibraryBooksHandle(true);if(!root)return;
    let exists=true;try{await root.getDirectoryHandle(id)}catch{exists=false}if(exists)throw Error('Υπάρχει ήδη ο φάκελος '+id+'. Δεν έγινε αντικατάσταση.');
    state.busy=true;renderChrome();setStatus('Canonical μετατροπή και πραγματική σελιδοποίηση books/'+id+'/…','warn');
    const built=D.makeBook(state.docx.result,entries,id,title,{generateToc:$('#docxGenerateToc').checked,tocDepth:Number($('#docxTocDepth').value),preserveHeadingColors:$('#docxPreserveColors').checked});
    built.book.meta={...(built.book.meta||{}),library:{discipline:$('#docxBookDiscipline').value.trim(),level:$('#docxBookLevel').value.trim(),category:$('#docxBookCategory').value.trim(),status:$('#docxBookStatus').value.trim(),tags:$('#docxBookTags').value.split(',').map(x=>x.trim()).filter(Boolean)},authoringVersion:APP_AUTHORING_VERSION};
    const localAssets=new Map();for(const[path,name]of built.imageMap){const url=state.docx.blobUrls.get(path);if(url)localAssets.set('images/'+name,url)}
    const paged=await P.paginateBook(built.book,{imageCandidates:src=>localAssets.has(src)?[localAssets.get(src)]:src?[src]:[],rejectOverflow:true,tolerancePx:1,assetTimeout:2500});
    const finalValidation=M.validateBook(paged.book);if(!finalValidation.ok)throw Error(finalValidation.errors.join('\n'));
    const dir=await root.getDirectoryHandle(id,{create:true}),images=await dir.getDirectoryHandle('images',{create:true});
    for(const[path,name]of built.imageMap){const blob=state.docx.result.imageBlobs.get(path);if(blob)await BookServiceV4.writeNamed(images,name,blob)}
    const fh=await BookServiceV4.writeNamed(dir,'book.json',JSON.stringify(paged.book,null,2));
    await BookServiceV4.writeNamed(dir,'index.html',D.launcher(title,id,false));await BookServiceV4.writeNamed(dir,'Editor.html',D.launcher(title,id,true));
    const report={...D.audit(state.docx.result,entries),bookId:id,title,createdAt:new Date().toISOString(),canonicalValidation:finalValidation,pagination:paged.report,overflowAudit:paged.audit};
    await BookServiceV4.writeNamed(dir,'docx_import_report.json',JSON.stringify(report,null,2));
    resetDocxState();await attachOpened(dir,fh,paged.book,'canonical',id);await scanLibrary();setStatus(`Το βιβλίο δημιουργήθηκε στη Βιβλιοθήκη: ${paged.book.pages.length} σελίδες · 0 overflow.`,'good');
  }catch(error){if(error.name==='AbortError')return;console.error(error);setStatus('Αποτυχία δημιουργίας: '+error.message,'bad');modal('Αποτυχία δημιουργίας',`<div class="info-card bad">${esc(error.message)}</div>`)}finally{state.busy=false;renderChrome()}
}
function rebuildGeneratedToc(book){
  let toc=null;for(const p of book.pages||[])for(const it of p.items||[])if(it?.extensions?.generatedActiveToc){toc=it;break}if(!toc)return 0;
  const headings=[];for(const p of book.pages||[])for(const it of p.items||[]){if(!['part_title','section_heading'].includes(it.type))continue;if(it?.sourceRef?.kind==='generated-toc'||it?.nav?.show===false&&String(it.title||'').toLowerCase()==='περιεχόμενα')continue;headings.push({id:it.id,title:String(it.nav?.label||it.title||'').trim(),level:Number(it.level||it.provenance?.sourceHeadingLevel||(it.type==='part_title'?1:2))})}
  if(!headings.length)return 0;const min=Math.min(...headings.map(h=>h.level)),nodes=[];for(const h of headings){const indent=' '.repeat(Math.max(0,h.level-min)*4);nodes.push({type:'link',href:'#'+h.id,children:[M.createTextRun(indent+h.title,{bold:h.level===min})]},{type:'line_break'})}if(nodes.at(-1)?.type==='line_break')nodes.pop();toc.body=M.createRichText(nodes);toc.extensions={...(toc.extensions||{}),generatedActiveToc:true,regeneratedAt:new Date().toISOString(),headingCount:headings.length};return headings.length
}
function certifyInsertionAudit(book,audit,localReport){
  const now=new Date().toISOString();book.importManifest={...(book.importManifest||{}),pagination:{...(book.importManifest?.pagination||{}),generatedAt:now,outputPages:(book.pages||[]).length,audit,localInsertionPagination:localReport||null}};book.extensions={...(book.extensions||{}),paginationRequired:true,paginationCertified:!!audit?.ok,paginationStatus:audit?.ok?'insertion-audit-ok':'insertion-overflow',paginationCertifiedAt:audit?.ok?now:null,paginationCurrentPageCount:(book.pages||[]).length,lastInsertionAudit:audit};return book
}
function insertionImageCandidates(draft){
  const inserted=new Map();for(const[path,name]of draft.imageMap){const url=state.docx.blobUrls.get(path);if(url)inserted.set('images/'+name,url)}
  return src=>inserted.has(src)?[inserted.get(src)]:imageCandidates(src);
}
function uniqueInsertionPageIds(draft,pages){
  const used=new Set(draft.book.pages.filter((_,index)=>index!==draft.pageIndex).map(current=>current.id));
  for(const current of pages)current.id=M.uniqueId(current.id||'page',used);
}
async function previewDocxInsertion(){
  const insertion=state.docx.insertion,entries=docxRange();if(!insertion||!entries.length)return;
  state.busy=true;renderChrome();setStatus('Δημιουργία προσωρινής canonical παρεμβολής και τοπική ανασελιδοποίηση…','warn');
  try{
    insertion.position=$('#insertPosition').value;
    const draft=D.buildInsertionDraft(session.book,state.docx.result,entries,insertion.anchor,insertion.position,{preserveHeadingColors:$('#docxPreserveColors').checked});
    const sourcePage=draft.book.pages[draft.pageIndex],temporary=clone(draft.book);temporary.pages=[clone(sourcePage)];
    const paged=await P.paginateBook(temporary,{imageCandidates:insertionImageCandidates(draft),rejectOverflow:true,tolerancePx:1,assetTimeout:2500});
    uniqueInsertionPageIds(draft,paged.book.pages);draft.generatedPages=paged.book.pages;draft.generatedPageIds=paged.book.pages.map(current=>current.id);draft.localPagination={report:paged.report,audit:paged.audit};
    draft.book.pages.splice(draft.pageIndex,1,...paged.book.pages);draft.tocHeadingCount=rebuildGeneratedToc(draft.book);
    draft.fullAudit=await P.auditOverflow(draft.book,{imageCandidates:insertionImageCandidates(draft),tolerancePx:1,assetTimeout:2500});certifyInsertionAudit(draft.book,draft.fullAudit,draft.localPagination);
    insertion.draft=draft;renderInsertWorkspace();setStatus(`Η προσωρινή παρεμβολή είναι έτοιμη: ${draft.insertedIds.length} blocks σε ${draft.generatedPages.length} επηρεασμένες σελίδες · ${draft.fullAudit.overflowPages} overflow. Δεν έγινε εγγραφή.`,draft.fullAudit.ok?'good':'warn');
  }catch(error){console.error(error);insertion.draft=null;setStatus('Αποτυχία προεπισκόπησης παρεμβολής: '+error.message,'bad');modal('Παρεμβολή από Word',`<div class="info-card bad">${esc(error.message)}</div>`)}finally{state.busy=false;renderActiveDocxWorkspace()}
}
async function applyDocxInsertion(){
  const draft=state.docx.insertion?.draft;if(!draft)return;
  const allowDuplicate=$('#insertAllowDuplicate')?.checked;if(draft.duplicateEvidence&&!allowDuplicate)return;
  const overflowNote=draft.fullAudit&&!draft.fullAudit.ok?`<div class="info-card warn"><b>${draft.fullAudit.overflowPages} σελίδες εμφανίζουν overflow:</b><br>${draft.fullAudit.pages.slice(0,8).map(x=>`σελ. ${x.displayPage} · ${x.overflowPx}px`).join('<br>')}<br>Η παρεμβολή επιτρέπεται, αλλά η πιστοποίηση θα μείνει σε κατάσταση insertion-overflow.</div>`:'<div class="info-card good">Ο έλεγχος όλου του βιβλίου ολοκληρώθηκε χωρίς overflow.</div>';
  const approved=await confirmBox('Παρεμβολή από Word',`<b>Πηγή:</b> ${esc(state.docx.file.name)}<br><b>Επιλογή:</b> ${esc(docxRangeSummary())}<br><b>Θέση:</b> ${draft.position==='after'?'μετά':'πριν'} από «${esc(draft.anchor.label)}»<br><b>Περιεχόμενα:</b> ${draft.tocHeadingCount?draft.tocHeadingCount+' επικεφαλίδες επανυπολογίστηκαν':'δεν υπάρχει ενεργός generated TOC'}<br><br>${overflowNote}<br>Θα δημιουργηθεί backup, θα αντιγραφούν οι νέες εικόνες και θα αποθηκευτεί το canonical αποτέλεσμα.`,'Παρεμβολή με backup');
  if(!approved)return;
  state.busy=true;renderChrome();setStatus('Backup, αντιγραφή εικόνων και ασφαλής εγγραφή παρεμβολής…','warn');
  try{
    const beforeBook=clone(session.book),beforeSelection=clone(session.selection),images=session.imagesDirectoryHandle||await session.directoryHandle.getDirectoryHandle('images',{create:true});let added=0;
    for(const[path,name]of draft.imageMap){const blob=state.docx.result.imageBlobs.get(path);if(blob){await BookServiceV4.writeNamed(images,name,blob);added++}}
    D.finalizeInsertionManifest(draft,{sourceFile:state.docx.result.fileName,imagesAdded:added});
    const saved=await S.saveBook(session.directoryHandle,draft.book,{backup:true,targetName:'book.json'});
    const bookFile=await session.directoryHandle.getFileHandle('book.json');const first=draft.firstInsertedId;
    await attachOpened(session.directoryHandle,bookFile,draft.book,'canonical',session.directoryHandle.name);
    const foundPage=session.book.pages.find(current=>current.items.some(candidate=>candidate.id===first));if(foundPage)session.selection={pageId:foundPage.id,itemId:first};
    session.commandStack.push({label:'Παρεμβολή από Word',at:new Date().toISOString(),before:beforeBook,after:clone(session.book),beforeSelection,afterSelection:clone(session.selection)});await scanLibrary();
    resetDocxState({mode:'create'});renderAll();setStatus(`Η παρεμβολή ολοκληρώθηκε: ${draft.insertedIds.length} canonical blocks · ${added} εικόνες · ${draft.fullAudit?.overflowPages||0} overflow · διαθέσιμη Αναίρεση.`,draft.fullAudit?.ok?'good':'warn');
  }catch(error){console.error(error);setStatus('Αποτυχία παρεμβολής: '+error.message,'bad');modal('Παρεμβολή από Word',`<div class="info-card bad">${esc(error.message)}</div>`)}finally{state.busy=false;renderChrome()}
}
function downloadDocxReport(){if(!state.docx.report)return;const blob=new Blob([JSON.stringify({...state.docx.report,selection:docxRange().map(x=>x.key)},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='docx_import_v4_4_report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function cancelDocxWorkflow(){resetDocxState();setView(has()?'book':'home');setStatus('Η εργασία DOCX ακυρώθηκε.')}

async function selftest(){
  try{
    const test=new C.WorkspaceSession(M.deepClone(session.book));
    const p0=test.book.pages[0].id;
    let paragraphId;
    test.execute('paragraph',book=>{const r=C.Operations.insertItem(book,p0,0,'paragraph');paragraphId=r.itemId;});
    test.execute('figure',book=>C.Operations.insertItem(book,p0,1,'figure'));
    test.execute('table',book=>C.Operations.insertItem(book,p0,2,'table'));
    test.execute('list',book=>C.Operations.insertItem(book,p0,3,'list'));
    test.execute('equation',book=>{const r=C.Operations.insertItem(book,p0,4,'equation');const eq=book.pages[0].items.find(x=>x.id===r.itemId);eq.source='E=mc^2';eq.mathml=X.sourceToMathML(eq.source,'block');});
    test.execute('page',book=>C.Operations.insertPage(book,1));
    test.execute('move',book=>C.Operations.moveItemToPage(book,p0,paragraphId,'next'));
    test.execute('clone page',book=>C.Operations.clonePage(book,book.pages[1].id));
    const before=JSON.stringify(test.book);test.undo();test.redo();
    const validation=M.validateBook(test.book),core=BookCore.validateData(test.book);
    const ok=before===JSON.stringify(test.book)&&validation.ok&&core.ok&&BookCore.isV4Data(test.book);
    modal('Rich Text & Math self-test',`<div class="info-card ${ok?'good':'bad'}"><b>${ok?'Επιτυχία':'Αποτυχία'}</b><br>paragraph + figure + table + list + equation + page + cross-page move + clone + undo/redo + direct-v4 validation</div>`);
  }catch(error){modal('Self-test',`<div class="info-card bad">${esc(error.message)}</div>`);}
}

async function executeCommand(cmd){
  if(!commandEnabled(cmd)){
    if(cmd==='clean-assets') unavailable('Εκκρεμής σύνδεση v4');
    return;
  }
  const map={
    'new-docx':startDocxCreate,'insert-docx':startDocxInsert,'docx-audit':()=>state.view==='docx'?downloadDocxReport():startDocxCreate,
    'new-book':newBook,'choose-library':chooseLibrary,'refresh-library':scanLibrary,'open-book':openBook,'open-candidate':openCandidate,'save':saveBook,'save-candidate':saveCandidate,'backup':manualBackup,'print':openReader,'close-book':closeBook,
    'undo':undo,'redo':redo,'clone':cloneSelected,'delete':deleteSelected,'move-up':()=>moveItem(-1),'move-down':()=>moveItem(1),'move-prev-page':()=>moveAcross('prev'),'move-next-page':()=>moveAcross('next'),'split-page':splitPage,
    'insert-item':()=>insertItem(null,'after'),'insert-item-before':()=>insertItem(null,'before'),'insert-item-after':()=>insertItem(null,'after'),
    'insert-page':()=>insertPage('after'),'insert-page-before':()=>insertPage('before'),'insert-page-after':()=>insertPage('after'),
    'move-page-up':()=>movePage(-1),'move-page-down':()=>movePage(1),'clone-page':clonePage,'merge-page-prev':()=>mergePage('prev'),'merge-page-next':()=>mergePage('next'),
    'inline-equation':insertInlineEquation,'display-equation':async()=>{await insertItem('equation','after');await editSelectedDisplayEquation();},
    'book-view':()=>setView('book'),'tab-book':()=>setTab('book'),'tab-page':()=>setTab('page'),'tab-item':()=>setTab('item'),'tab-nav':()=>setTab('nav'),'audit':()=>setTab('audit'),
    'home':()=>setView('home'),'toggle-properties':toggleProperties,'toggle-scenes':toggleScenes,'zoom-in':()=>changeZoom(.08),'zoom-out':()=>changeZoom(-.08),'zoom-fit':()=>fitPreviewToWidth(true),'zoom-100':setPreviewZoom100,
    'migration':migrationTool,'reflow':reflowCurrentBook,'selftest':selftest,
    'help':()=>modal('Οδηγίες BookWriter v4.5.0 RC1',`<div class="info-card"><b>Βιβλιοθήκη:</b> σύνδεσε τον φάκελο <code>BookWriter/books</code>. Τα βιβλία εμφανίζονται σε ταξινομήσιμο πίνακα και ανοίγουν στον Συγγραφέα ή ως τελικό βιβλίο.<br><b>Νέο βιβλίο από Word:</b> μετατροπή επιλεγμένου τμήματος DOCX σε canonical bookwriter-v4.<br><b>Παρεμβολή από Word:</b> βιβλίο αριστερά, DOCX δεξιά. Η προσωρινή παρεμβολή εμφανίζεται μέσα στην προεπισκόπηση του βιβλίου πριν από την εγγραφή.<br><b>Εκτύπωση / PDF:</b> χρησιμοποίησε το εμφανές κουμπί ή Ctrl+P. Εκτυπώνεται η τρέχουσα κατάσταση του ανοιχτού βιβλίου, με έλεγχο υπερχείλισης και ρητή άδεια συνέχισης.<br><b>Αποθήκευση:</b> Ctrl+S στο canonical <code>book.json</code> με backup και επαλήθευση.</div>`),
    'about':()=>modal('ΣΥΓΓΡΑΦΕΑΣ',`<div class="info-card good"><b>BookWriter v4.5.0 RC1</b><br>Υποψήφια ενοποιημένη έκδοση: Βιβλιοθήκη πίνακα, split παρεμβολή DOCX, κοινή απόδοση Συγγραφέα/βιβλίου/εκτύπωσης και μία επίσημη διαδρομή Εκτύπωσης / PDF.</div>`)
  };
  if(map[cmd]) await map[cmd]();
}



bindInsertSplitter();
$('#insertDocxOpenButton').onclick=()=>$('#docxFileInput').click();
$('#insertBookSearch').addEventListener('input',()=>{if(state.view==='insert')renderInsertWorkspace()});
$('#insertPosition').addEventListener('change',()=>{if(state.docx.insertion){state.docx.insertion.position=$('#insertPosition').value;state.docx.insertion.draft=null}renderInsertWorkspace()});
$('#insertPreviewButton').onclick=previewDocxInsertion;$('#insertApplyButton').onclick=applyDocxInsertion;$('#insertCancelButton').onclick=cancelDocxWorkflow;
$('#insertAllowDuplicate').addEventListener('change',renderInsertSummary);
$('#insertBookZoomOut').onclick=()=>changeInsertBookZoom(-.06);$('#insertBookZoomIn').onclick=()=>changeInsertBookZoom(.06);$('#insertBookZoom100').onclick=()=>changeInsertBookZoom(0,'100');$('#insertBookZoomFit').onclick=()=>insertFitBookZoom(true);
$('#insertBookPreviewScroller').addEventListener('wheel',event=>{if(!(event.ctrlKey||event.metaKey))return;event.preventDefault();changeInsertBookZoom(event.deltaY<0?.06:-.06)},{passive:false});
$('#docxOpenButton').onclick=()=>$('#docxFileInput').click();
$('#docxFileInput').onchange=event=>{const file=event.target.files?.[0];event.target.value='';if(file)analyzeDocxFile(file)};
$('#librarySearch').addEventListener('input',event=>{state.library.filters.query=event.target.value;renderLibrary()});
$('#libraryDisciplineFilter').addEventListener('change',event=>{state.library.filters.discipline=event.target.value;renderLibrary()});
$('#libraryLevelFilter').addEventListener('change',event=>{state.library.filters.level=event.target.value;renderLibrary()});
$('#libraryStatusFilter').addEventListener('change',event=>{state.library.filters.status=event.target.value;renderLibrary()});
$('#libraryClearFilters').addEventListener('click',()=>{state.library.filters={query:'',discipline:'',level:'',status:''};renderLibrary()});
$('#docxBookTitle').addEventListener('input',updateDocxCreateSummary);$('#docxBookId').addEventListener('input',updateDocxCreateSummary);
['docxGenerateToc','docxTocDepth','docxPreserveColors','docxBookDiscipline','docxBookLevel','docxBookCategory','docxBookStatus','docxBookTags'].forEach(id=>$('#'+id).addEventListener('change',updateDocxCreateSummary));
$('#docxCreateButton').onclick=createBookFromDocx;$('#docxReportButton').onclick=downloadDocxReport;$('#docxCancelButton').onclick=cancelDocxWorkflow;

document.addEventListener('selectionchange',()=>saveRichSelection(activeRichContext));

$$('[data-command]').forEach(button=>{
  if(button.dataset.command==='inline-equation') button.addEventListener('mousedown',()=>saveRichSelection(activeRichContext));
  button.addEventListener('click',()=>executeCommand(button.dataset.command));
});
$$('.property-tabs button').forEach(button=>button.onclick=()=>setTab(button.dataset.tab));
$('#closePropertiesButton').onclick=toggleProperties;
$('#bookPreviewScroller').addEventListener('scroll',()=>requestAnimationFrame(updateSelectionOverlay));
window.addEventListener('resize',()=>{if(state.previewZoomMode==='fit'&&state.view==='book')fitPreviewToWidth(false);if(state.insert.bookZoomMode==='fit'&&state.view==='insert')insertFitBookZoom(false);requestAnimationFrame(updateSelectionOverlay)});
$('#bookPreviewScroller').addEventListener('wheel',event=>{if(!(event.ctrlKey||event.metaKey))return;event.preventDefault();changeZoom(event.deltaY<0?.08:-.08)},{passive:false});

window.addEventListener('keydown',event=>{
  const key=event.key.toLowerCase();
  if(event.key==='Escape'&&has()){event.preventDefault();if(state.view==='insert')cancelDocxWorkflow();else setView('book');}
  if(event.key==='F1'){event.preventDefault();executeCommand('help');}
  if(event.key==='F2'){event.preventDefault();executeCommand('tab-item');}
  if(event.key==='F4'){event.preventDefault();executeCommand('toggle-properties');}
  if(event.ctrlKey&&key==='n'){event.preventDefault();executeCommand('new-book');}
  if(event.ctrlKey&&key==='o'){event.preventDefault();executeCommand('open-book');}
  if(event.ctrlKey&&key==='s'){event.preventDefault();executeCommand(state.mode==='migration'?'save-candidate':'save');}
  if(event.ctrlKey&&key==='p'){event.preventDefault();executeCommand('print');}
  if(event.ctrlKey&&key==='z'){event.preventDefault();executeCommand('undo');}
  if(event.ctrlKey&&key==='y'){event.preventDefault();executeCommand('redo');}
  if(event.ctrlKey&&key==='d'){event.preventDefault();executeCommand('clone');}
  if(event.altKey&&key==='m'){event.preventDefault();executeCommand('inline-equation');}
  if(event.ctrlKey&&(event.key==='+'||event.key==='=')){event.preventDefault();executeCommand('zoom-in');}
  if(event.ctrlKey&&event.key==='-'){event.preventDefault();executeCommand('zoom-out');}
  if(event.ctrlKey&&event.key==='0'){event.preventDefault();executeCommand('zoom-fit');}
  if(event.ctrlKey&&event.key==='1'){event.preventDefault();executeCommand('zoom-100');}
  if(event.key==='Insert'&&!document.activeElement?.isContentEditable){event.preventDefault();executeCommand(event.shiftKey?'insert-item-before':'insert-item-after');}
  if(event.key==='Delete'&&!document.activeElement?.isContentEditable&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();executeCommand('delete');}
});

function testLoadBookObject(book){session.attachBook(book,{});state.mode='canonical';state.name=book.meta?.projectId||'test-book';state.audit=M.auditIntegrity(book);state.compatibility=BookCore.auditData(book);setView('book');return{pages:book.pages.length,items:book.pages.reduce((sum,p)=>sum+(p.items||[]).length,0)}}
async function testLoadBookUrl(url){const book=await fetch(url).then(response=>{if(!response.ok)throw Error('HTTP '+response.status);return response.json()});return testLoadBookObject(book)}

window.addEventListener('beforeunload',event=>{if(has()&&session.isDirty()){event.preventDefault();event.returnValue='';}});

window.BookWriterApp={version:APP_VERSION,getBook:()=>clone(session.book),getSelection:()=>clone(session.selection),getMode:()=>state.mode,getDocx:()=>({mode:state.docx.mode,file:state.docx.file?.name||'',entries:state.docx.entries.length,range:docxRange().map(x=>x.key),report:clone(state.docx.report),insertion:clone(state.docx.insertion)}),testLoadBookUrl,testLoadBookObject,startInsertWorkspace:startDocxInsert,math:X,docx:D};

renderChrome();
applyPreviewZoom(false);
restoreLibrary();
})();
