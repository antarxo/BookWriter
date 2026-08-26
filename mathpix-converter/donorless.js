(function(){
'use strict';
const $=s=>document.querySelector(s);
let timer=null,started=0,current=null;

function setStatus(text,kind=''){
  $('#statusText').textContent=text;
  $('#statusText').className=kind;
  $('#statusMessage').textContent=text;
  $('#statusMessage').className=kind;
}
function setHint(html,kind=''){
  $('#formHint').className=`notice small ${kind}`.trim();
  $('#formHint').innerHTML=html;
}
function tick(){
  if(!started)return;
  const elapsed=Math.max(1,Math.round((Date.now()-started)/1000));
  $('#elapsedText').textContent=`${elapsed}s`;
  const pct=Math.min(94,6+elapsed*1.2);
  $('#progressBar').style.width=`${pct}%`;
}
function startProgress(){
  started=Date.now();
  $('#runBadge').textContent='τρέχει';
  $('#runBadge').className='run-badge warn';
  $('#stageText').textContent='PDF + Markdown maps → Word build';
  tick();
  timer=setInterval(tick,1000);
}
function stopProgress(ok){
  if(timer)clearInterval(timer);
  timer=null;
  $('#progressBar').style.width='100%';
  $('#stageText').textContent=ok?'ολοκληρώθηκε':'αποτυχία';
  $('#runBadge').textContent=ok?'ολοκληρώθηκε':'αποτυχία';
  $('#runBadge').className=`run-badge ${ok?'good':'bad'}`;
}
async function refreshIdentity(){
  try{
    const r=await fetch('/api/donorless-status',{cache:'no-store'});
    const j=await r.json();
    $('#gatewayBuildText').textContent=j.build||'άγνωστο';
    $('#gatewayBuildBadge').textContent=`πύλη: ${j.build||'άγνωστο'}`;
    $('#gatewayBuildBadge').className='build-badge';
  }catch(e){
    $('#gatewayBuildText').textContent='μη διαθέσιμη';
    $('#gatewayBuildBadge').textContent='πύλη: μη διαθέσιμη';
    $('#gatewayBuildBadge').className='build-badge build-badge-bad';
  }
}
function renderResult(result){
  const report=result.report||{};
  const preflight=report.mappingPreflight||{};
  const layout=report.pageLayoutSummary||{};
  const skipped=(report.stagesSkipped||[]).join(', ');
  $('#resultPanel').className='result-panel';
  $('#resultPanel').innerHTML=`
    <div class="notice good"><b>Το donorless run ολοκληρώθηκε.</b><br>Το DOCX δημιουργήθηκε χωρίς Mathpix DOCX donor.</div>
    <div class="summary-grid">
      <span>Mode</span><b>${report.mode||'pdf-markdown-donorless-baseline'}</b>
      <span>Markdown στοιχεία</span><b>${report.markdownElementCount??'—'}</b>
      <span>Mapping preflight</span><b>${preflight.status||'—'}</b>
      <span>Layout rows</span><b>${layout.rowCount??layout.rows??'—'}</b>
      <span>Παραλείφθηκαν</span><b>${skipped||'—'}</b>
    </div>
    <div class="button-row">
      <a class="primary" href="${result.downloadUrl}">Λήψη reconstructed DOCX</a>
      <a href="${result.reportUrl}" target="_blank" rel="noopener">Άνοιγμα report JSON</a>
    </div>`;
}
async function handleSubmit(event){
  event.preventDefault();
  const pdf=$('#pdfInput').files?.[0];
  const md=$('#markdownInput').files?.[0];
  const pages=String($('#pagesInput').value||'').trim();
  if(!pdf){setHint('Επίλεξε το αρχικό PDF.','bad');return;}
  if(!md){setHint('Επίλεξε το Mathpix Markdown ZIP.','bad');return;}
  if(!pages){setHint('Δώσε εύρος σελίδων, π.χ. <code>17-20</code>.','bad');return;}
  $('#startButton').disabled=true;
  $('#resetButton').disabled=true;
  setHint('Τρέχει καθαρό baseline χωρίς DOCX donor.','warn');
  setStatus('Ανάλυση PDF/Markdown και δημιουργία maps.','warn');
  startProgress();
  try{
    const fd=new FormData();
    fd.append('pdf',pdf,pdf.name);
    fd.append('markdown',md,md.name);
    fd.append('pages',pages);
    const r=await fetch('/api/donorless-convert',{method:'POST',body:fd});
    const j=await r.json();
    if(!r.ok||!j.ok)throw new Error(j.error||`HTTP ${r.status}`);
    current=j;
    stopProgress(true);
    setStatus('Το donorless baseline ολοκληρώθηκε.','good');
    setHint('Έτοιμο. Τώρα βλέπουμε τι παράγει μόνο το PDF + Markdown.','good');
    renderResult(j);
  }catch(e){
    console.error(e);
    stopProgress(false);
    setStatus('Η donorless μετατροπή απέτυχε.','bad');
    setHint(String(e.message||e),'bad');
  }finally{
    $('#startButton').disabled=false;
    $('#resetButton').disabled=false;
  }
}
function reset(){
  $('#converterForm').reset();
  current=null;started=0;
  if(timer)clearInterval(timer);timer=null;
  $('#progressBar').style.width='0';
  $('#elapsedText').textContent='0s';
  $('#stageText').textContent='αναμονή';
  $('#runBadge').textContent='αναμονή';
  $('#runBadge').className='run-badge';
  $('#resultPanel').className='result-panel empty';
  $('#resultPanel').innerHTML='<div class="empty-state"><b>Δεν υπάρχει ακόμη αποτέλεσμα.</b><span>Μετά το run θα εμφανιστούν DOCX και report.</span></div>';
  setStatus('Δεν έχει ξεκινήσει run.');
  setHint('Για την πρώτη δοκιμή προτίμησε μικρό εύρος, π.χ. <code>17-20</code>.');
}
$('#converterForm').addEventListener('submit',handleSubmit);
$('#resetButton').addEventListener('click',reset);
refreshIdentity();
})();
