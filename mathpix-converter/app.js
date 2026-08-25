(function(){
'use strict';

const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;"}[ch]));
const storage={get(key,fallback=''){try{return localStorage.getItem(key)??fallback}catch{return fallback}},set(key,value){try{localStorage.setItem(key,value)}catch{}}};
const storedJson=(key,fallback={})=>{try{return JSON.parse(storage.get(key,'')||JSON.stringify(fallback))}catch{return fallback}};
const state={
  startedAt:0,
  timer:null,
  progressPollTimer:null,
  liveProgress:null,
  current:null,
  decisionFilter:'all',
  decisionTab:'decisions',
  workspaceTab:'conversion',
  pdfZoom:Number(storage.get('bw-mathpix-pdf-zoom','1.45'))||1.45,
  hiddenColumns:storedJson('bw-mathpix-hidden-columns',{}),
  gatewayIdentity:null
};

function renderGatewayIdentity(identity=null,error=''){
  const badge=$('#gatewayBuildBadge');
  const serverNode=$('#gatewayBuildText');
  const pipelineNode=$('#pipelineBuildText');
  const serverBuild=identity?.build||'';
  const pipelineBuild=identity?.pipelineBuild||identity?.pipeline||'';
  if(error){
    if(badge){
      badge.textContent='πύλη: μη διαθέσιμη';
      badge.title=error;
      badge.className='build-badge build-badge-bad';
    }
    if(serverNode)serverNode.textContent='μη διαθέσιμο';
    if(pipelineNode)pipelineNode.textContent='μη διαθέσιμο';
    return;
  }
  if(badge){
    badge.textContent=serverBuild?`πύλη: ${serverBuild}`:'πύλη: άγνωστο build';
    badge.title=[
      serverBuild&&`Server: ${serverBuild}`,
      pipelineBuild&&`Pipeline: ${pipelineBuild}`,
      identity?.canonicalProfile&&`Profile: ${identity.canonicalProfile}`,
      identity?.serverTopology&&`Topology: ${identity.serverTopology}`
    ].filter(Boolean).join('\n');
    badge.className='build-badge';
  }
  if(serverNode)serverNode.textContent=serverBuild||'άγνωστο';
  if(pipelineNode)pipelineNode.textContent=pipelineBuild||'άγνωστο';
}

async function refreshGatewayIdentity(){
  try{
    const response=await fetch('/api/status',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const identity=await response.json();
    state.gatewayIdentity=identity;
    renderGatewayIdentity(identity);
    return identity;
  }catch(error){
    state.gatewayIdentity=null;
    renderGatewayIdentity(null,error.message||String(error));
    return null;
  }
}

function activateWorkspaceTab(tab){
  state.workspaceTab=tab;
  document.querySelectorAll('[data-workspace-tab]').forEach(button=>{
    button.classList.toggle('active',button.dataset.workspaceTab===tab);
    button.setAttribute('aria-selected',button.dataset.workspaceTab===tab?'true':'false');
  });
  document.querySelectorAll('.workspace-pane').forEach(pane=>{
    pane.classList.toggle('active',pane.id===`${tab}Pane`);
  });
}

function closeMenus(){
  document.querySelectorAll('.menu').forEach(menu=>menu.blur?.());
}

function estimatedStage(elapsed,explicitStage=''){
  if(explicitStage&&explicitStage!=='Ανέβασμα και έλεγχος πακέτου ZIP')return explicitStage;
  if(elapsed<8)return 'Ανέβασμα και αρχικός έλεγχος πηγών';
  if(elapsed<18)return 'Δημιουργία εσωτερικού Mathpix working package';
  if(elapsed<35)return 'Ανάλυση PDF geometry, κειμένου και περιοχών σελίδας';
  if(elapsed<60)return 'Ανάλυση Markdown και Mathpix DOCX donor';
  if(elapsed<95)return 'Αντιστοίχιση PDF, DOCX και Markdown';
  if(elapsed<150)return 'Ανακατασκευή DOCX και έλεγχος υποψήφιων σελιδοποιήσεων';
  if(elapsed<240)return 'Word/PDF render calibration και οπτική σύγκριση σελιδοποίησης';
  return 'Μεγάλο run: συνεχίζεται render calibration, report και δημιουργία DOCX';
}

function updateProgress(stage=''){
  if(!state.startedAt)return;
  const elapsed=Math.max(1,Math.round((Date.now()-state.startedAt)/1000));
  const percent=Math.min(94,Math.max(6,elapsed<240?elapsed/240*88:94));
  $('#elapsedText').textContent=`${elapsed}s`;
  $('#stageText').textContent=estimatedStage(elapsed,stage||state.liveProgress?.stage||'');
  $('#progressBar').style.width=`${percent}%`;
}

function makeProgressToken(){
  if(window.crypto?.randomUUID)return window.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,ch=>{
    const value=Math.random()*16|0;
    return (ch==='x'?value:(value&0x3|0x8)).toString(16);
  });
}

function liveProgressText(progress={}){
  const bits=[];
  if(progress.stage)bits.push(progress.stage);
  if(progress.phase)bits.push(progress.phase);
  if(progress.candidateBodySizePt)bits.push(`${progress.candidateBodySizePt}pt`);
  if(progress.fontScale)bits.push(`scale ${progress.fontScale}`);
  if(progress.outputPages&&progress.targetPages)bits.push(`${progress.outputPages}/${progress.targetPages} σελίδες`);
  if(progress.bestOutputPages)bits.push(`καλύτερο μέχρι τώρα ${progress.bestOutputPages} σελίδες`);
  if(progress.selectedFontScale)bits.push(`επιλεγμένο scale ${progress.selectedFontScale}`);
  if(progress.reason)bits.push(progress.reason);
  return bits.join(' · ')||'Η μετατροπή συνεχίζεται.';
}

function applyLiveProgress(progress={}){
  state.liveProgress=progress;
  const text=liveProgressText(progress);
  updateProgress(progress.stage||'');
  setStatus(text,progress.status==='failed'?'bad':'warn');
}

function startProgressPolling(token){
  stopProgressPolling();
  if(!token)return;
  state.progressPollTimer=setInterval(async()=>{
    try{
      const response=await fetch('/api/mathpix-progress/'+encodeURIComponent(token),{cache:'no-store'});
      if(!response.ok)return;
      const progress=await response.json();
      applyLiveProgress(progress);
    }catch(error){
      console.warn('Live progress polling failed',error);
    }
  },1500);
}

function stopProgressPolling(){
  if(state.progressPollTimer)clearInterval(state.progressPollTimer);
  state.progressPollTimer=null;
  state.liveProgress=null;
}

function startProgress(){
  stopProgress();
  state.startedAt=Date.now();
  const badge=$('#runBadge');
  badge.textContent='τρέχει';
  badge.className='run-badge warn';
  updateProgress();
  state.timer=setInterval(()=>updateProgress(),1000);
}

function stopProgress(finalStage='έτοιμο',percent=100){
  if(state.timer)clearInterval(state.timer);
  state.timer=null;
  stopProgressPolling();
  if(state.startedAt){
    $('#stageText').textContent=finalStage;
    $('#progressBar').style.width=`${percent}%`;
    const badge=$('#runBadge');
    badge.textContent=finalStage;
    badge.className=`run-badge ${finalStage==='αποτυχία'?'bad':finalStage==='ολοκληρώθηκε'?'good':''}`.trim();
  }
}

function setStatus(text,kind=''){
  const node=$('#statusText');
  node.textContent=text;
  node.className=kind;
  const status=$('#statusMessage');
  if(status){
    status.textContent=text;
    status.className=kind;
  }
}

function showHint(html,kind=''){
  const node=$('#formHint');
  node.className=`notice small ${kind}`.trim();
  node.innerHTML=html;
}

function downloadBlob(blob,name){
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download=name||'download';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function downloadCurrentDocx(){
  if(!state.current?.blob){
    setStatus('Δεν υπάρχει ακόμη DOCX για λήψη.','warn');
    activateWorkspaceTab('conversion');
    return;
  }
  const mapsFailure=mapsFirstFailureReason(state.current.report||{});
  if(mapsFailure){
    setStatus('Δεν γίνεται λήψη DOCX: '+mapsFailure,'bad');
    activateWorkspaceTab('report');
    return;
  }
  downloadBlob(state.current.blob,state.current.outputName);
}

function downloadCurrentReport(){
  if(!state.current?.report){
    setStatus('Δεν υπάρχει ακόμη report για λήψη.','warn');
    activateWorkspaceTab('conversion');
    return;
  }
  downloadBlob(new Blob([JSON.stringify(state.current.report,null,2)],{type:'application/json'}),'pdf_mathpix_docx_report.json');
}

function updateInterventionCount(){
  const report=state.current?.report||{};
  const count=queueForReport(report).length+actionableQueueForReport(report).length+diagnosticQueueForReport(report).length;
  const node=$('#interventionCount');
  if(node)node.textContent=String(count);
}

function visualWitnessSummary(report={}){
  const all=[
    ...queueForReport(report),
    ...actionableQueueForReport(report),
    ...diagnosticQueueForReport(report)
  ];
  const unique=new Map();
  all.forEach((item,index)=>unique.set(item.id||`${item.kind}-${item.page}-${index}`,item));
  const items=[...unique.values()];
  const pageImages=items.filter(item=>item.pdfPagePreviewUrl).length;
  const crops=items.filter(item=>item.previewUrl).length;
  const missing=items.filter(item=>!item.pdfPagePreviewUrl&&!item.previewUrl).length;
  return{total:items.length,pageImages,crops,missing};
}

function mapsFirstFailureReason(report={}){
  const guard=report?.architectureGuard||null;
  if(guard?.status==='fail'){
    const details=(guard.violations||[]).map(item=>item.message||item.code||String(item)).filter(Boolean).slice(0,4).join(' · ');
    return details?`Architecture guard failed: ${details}`:'Architecture guard failed.';
  }
  if(!guard)return 'Λείπει architecture_guard.json. Η μετατροπή δεν πιστοποιεί ότι ακολούθησε τη νέα maps-first λογική.';
  const mapping=report?.mappingFidelity||null;
  if(mapping?.status==='fail'){
    const details=(mapping.violations||[]).map(item=>item.message||item.code||String(item)).filter(Boolean).slice(0,4).join(' · ');
    return details?`Mapping fidelity failed: ${details}`:'Mapping fidelity failed.';
  }
  if(!mapping)return 'Λείπει mapping_fidelity.json. Η μετατροπή δεν πιστοποιεί την πιστότητα των χαρτογραφήσεων.';
  if(!report?.conversionSpine)return 'Λείπει conversionSpine. Η διαδικασία δεν συνεχίζει σε παλιές ουρές/fallbacks.';
  if(!report?.markdownPdfSpine)return 'Λείπει markdownPdfSpine. Δεν υπάρχει χαρτογραφημένος οδηγός PDF για το Markdown.';
  if(!report?.pageLayoutSpine)return 'Λείπει pageLayoutSpine. Δεν υπάρχει χάρτης θέσεων/σελιδοποίησης για το DOCX.';
  if(!report?.docxDonorMap)return 'Λείπει docxDonorMap. Δεν υπάρχει ελεγχόμενος χάρτης δότη από το Mathpix DOCX.';
  return '';
}

async function readGatewayFailure(response,fallbackMessage){
  let payload=null,detail='';
  const clone=response.clone();
  try{
    payload=await response.json();
    detail=payload.error||'';
  }catch(_error){
    detail=await clone.text();
  }
  if(response.status===404){
    detail='Η τρέχουσα τοπική πύλη δεν γνωρίζει ακόμη το /api/convert-mathpix-docx. Κλείσε το παράθυρο CMD του BookWriter και άνοιξέ το ξανά με το Start_BookWriter_Author.cmd.';
  }
  const error=new Error(detail||fallbackMessage||`Η μετατροπή απέτυχε (${response.status}).`);
  error.gatewayPayload=payload;
  throw error;
}

async function gatewayDocxFromResponse(response,fallbackName){
  const token=response.headers.get('X-BookWriter-Report-Token')||'';
  const blob=await response.blob();
  const outputName=response.headers.get('X-BookWriter-Mathpix-Reconstructed-Filename')||fallbackName||'mathpix_RECONSTRUCTED.docx';
  let report={reportToken:token};
  if(token){
    try{
      const reportResponse=await fetch('/api/normalization-report/'+encodeURIComponent(token));
      if(reportResponse.ok)report=await reportResponse.json();
    }catch(error){
      console.warn('Normalization report fetch failed',error);
    }
  }
  return{blob,outputName,report,token};
}

async function convertMathpix(pdfFile,markdownFile,docxFile,pages,options){
  const form=new FormData();
  form.append('pdf',pdfFile,pdfFile.name);
  form.append('markdown',markdownFile,markdownFile.name);
  form.append('docx',docxFile,docxFile.name);
  form.append('pages',pages);
  if(options.renderFidelity)form.append('renderFidelity','1');
  if(options.calibration)form.append('calibration',options.calibration);
  const headers={};
  if(options.progressToken)headers['X-BookWriter-Progress-Token']=options.progressToken;
  let response;
  try{
    response=await fetch('/api/convert-mathpix-docx',{method:'POST',body:form,headers});
  }catch(_error){
    if(location.protocol==='file:'){
      throw new Error('Η σελίδα άνοιξε ως αρχείο και όχι μέσα από τον τοπικό server. Άνοιξέ τη από http://127.0.0.1:8766/mathpix-converter/index.html ή από το port που γράφει το παράθυρο CMD.');
    }
    throw new Error('Δεν υπάρχει ενεργή σύνδεση με την τοπική πύλη PDF/Mathpix. Αν το BookWriter ήταν ήδη ανοικτό πριν τις αλλαγές, κλείσε το παράθυρο CMD και άνοιξέ το ξανά με το Start_BookWriter_Author.cmd.');
  }
  if(!response.ok)await readGatewayFailure(response,`Η ανακατασκευή PDF/Mathpix απέτυχε (${response.status}).`);
  const baseName=pdfFile.name.replace(/\.pdf$/i,'');
  const gateway=await gatewayDocxFromResponse(response,baseName+'_PDF_MATHPIX_RECONSTRUCTED.docx');
  const mapsFailure=mapsFirstFailureReason(gateway.report||{});
  if(mapsFailure){
    const error=new Error(mapsFailure);
    error.gatewayPayload={reportUrl:gateway.token?'/api/normalization-report/'+encodeURIComponent(gateway.token):''};
    throw error;
  }
  return gateway;
}

function mathpixFidelitySummary(report={}){
  const summary=report?.fidelityFallbackReport?.summary||{};
  const counts=summary.finalEquationStatusCounts||{};
  const inputPackage=report?.pdfPipelineManifest?.inputPackage||{};
  const elements=Number(inputPackage.markdownElementCount||summary.markdownElementCount||0);
  const matched=Number(summary.markdownEquationMatchedCount||0);
  const total=Number(summary.markdownEquationCount||0);
  const native=Number(counts['native-word-math']||0);
  const raster=Number(summary.rasterEquationFallbacks||counts['visual-fallback-latex-conversion-failed']||0);
  const actionable=Number(summary.actionableReviewQueueCount||summary.humanReviewQueueCount||0);
  const diagnostic=Number(summary.diagnosticReviewQueueCount||0);
  const markdownChecked=Number(summary.markdownSurvivalChecked||0);
  const markdownMissing=Number(summary.markdownSurvivalMissing||0);
  const markdownWeak=Number(summary.markdownSurvivalWeak||0);
  const markdownCoverage=Number(summary.markdownSurvivalCoverage||0);
  const spineCoverage=Number(summary.markdownPdfSpineCoverage||0);
  const spineItems=Number(summary.markdownPdfSpineItems||0);
  const spinePlaced=Number(summary.markdownPdfSpinePlaced||0);
  const spineWeak=Number(summary.markdownPdfSpineWeak||0);
  const spineUnplaced=Number(summary.markdownPdfSpineUnplaced||0);
  const spineScope=summary.markdownPdfSpineScope||'';
  const conversionSummary=report?.conversionSpine?.summary||{};
  const hasConversionSpine=!!report?.conversionSpine;
  const conversionRows=Number(conversionSummary.selectedRowCount||0);
  const conversionCoverage=Number(conversionSummary.coverage||0);
  const conversionDecisions=Number(conversionSummary.decisionRequiredCount||0);
  const layoutSummary=report?.pageLayoutSpine?.summary||{};
  const layoutRows=Number(layoutSummary.rowCount||0);
  const layoutCoverage=Number(layoutSummary.coverage||0);
  const layoutContractCoverage=Number(layoutSummary.contractCoverage||0);
  const layoutUsable=Number(layoutSummary.contractUsableCount||0);
  const layoutSafeFlow=Number(layoutSummary.safeFlowOrderingSlotCount||0);
  const layoutUnplaced=Number(layoutSummary.unplacedLayoutSlotCount||0);
  const userDecisions=conversionDecisions;
  const donorSummary=report?.docxDonorMap?.summary||{};
  const donorMath=Number(donorSummary.mathCandidateCount||0);
  const donorMarkdownLinks=Number(donorSummary.markdownLinkedParagraphCount||0);
  const donorPdfLinks=Number(donorSummary.pdfLinkedParagraphCount||0);
  const benchmark=report?.architectureBenchmark||{};
  const benchmarkTiming=benchmark.timing||{};
  const benchmarkQuality=benchmark.quality||{};
  const benchmarkMaps=benchmark.maps||{};
  const benchmarkSeconds=Number(benchmarkTiming.totalSeconds||0);
  const benchmarkNative=Number(benchmarkQuality.nativeWordMath||0);
  const benchmarkVisual=Number(benchmarkQuality.visualEquationFallbacks||0);
  const benchmarkOmmlUsed=Number(benchmarkMaps.docxOmmlUsed||0);
  const benchmarkOmmlCandidates=Number(benchmarkMaps.docxOmmlCandidates||0);
  const guard=report?.architectureGuard||{};
  const guardStatus=guard.status||'';
  const guardViolations=Number(guard.violationCount||0);
  const guardWarnings=Number(guard.warningCount||0);
  const mapping=report?.mappingFidelity||{};
  const mappingStatus=mapping.status||'';
  const mappingViolations=Number(mapping.violationCount||0);
  const mappingWarnings=Number(mapping.warningCount||0);
  const bits=[];
  if(elements)bits.push(`${elements} στοιχεία Markdown`);
  if(guardStatus)bits.push(`Architecture guard ${guardStatus}${guardViolations||guardWarnings?` · ${guardViolations} violations · ${guardWarnings} warnings`:''}`);
  if(mappingStatus)bits.push(`Mapping fidelity ${mappingStatus}${mappingViolations||mappingWarnings?` · ${mappingViolations} violations · ${mappingWarnings} warnings`:''}`);
  if(benchmarkSeconds||benchmarkNative||benchmarkVisual)bits.push(`Benchmark ${benchmarkSeconds?Math.round(benchmarkSeconds)+'s · ':''}${benchmarkNative} native · ${benchmarkVisual} εικόνα · OMML ${benchmarkOmmlUsed}/${benchmarkOmmlCandidates}`);
  if(donorMath||donorMarkdownLinks||donorPdfLinks)bits.push(`DOCX donor map ${donorMath} OMML · ${donorMarkdownLinks} MD links · ${donorPdfLinks} PDF links`);
  if(conversionRows)bits.push(`Conversion spine ${Math.round(conversionCoverage*100)}% · ${conversionRows} rows · ${conversionDecisions} decisions`);
  if(layoutRows)bits.push(`Layout spine ${Math.round(layoutCoverage*100)}% · contract ${Math.round(layoutContractCoverage*100)}% · ${layoutUsable}/${layoutRows} usable · ${layoutSafeFlow} safe order · ${layoutUnplaced} no slot`);
  if(spineItems)bits.push(`PDF-guided spine ${Math.round(spineCoverage*100)}% · ${spinePlaced}/${spineItems} placed · ${spineWeak} weak · ${spineUnplaced} unplaced`);
  if(markdownChecked)bits.push(`Markdown survival ${Math.round(markdownCoverage*100)}% · ${markdownMissing} missing · ${markdownWeak} weak`);
  if(total||matched)bits.push(`εξισώσεις ${matched}/${total||matched} matched`);
  if(native||raster)bits.push(`${native} native Word math · ${raster} εικόνα`);
  if(userDecisions)bits.push(`${userDecisions} αποφάσεις χρήστη`);
  if(!hasConversionSpine&&actionable)bits.push(`${actionable} ουσιαστικά σημεία ελέγχου`);
  if(!hasConversionSpine&&diagnostic)bits.push(`${diagnostic} διαγνωστικά χαμηλής προτεραιότητας`);
  return{summary,elements,matched,total,native,raster,userDecisions,actionable:hasConversionSpine?0:actionable,diagnostic:hasConversionSpine?0:diagnostic,markdownChecked,markdownMissing,markdownWeak,markdownCoverage,spineCoverage,spineItems,spinePlaced,spineWeak,spineUnplaced,spineScope,conversionRows,conversionCoverage,conversionDecisions,layoutRows,layoutCoverage,layoutContractCoverage,layoutUsable,layoutSafeFlow,layoutUnplaced,donorMath,donorMarkdownLinks,donorPdfLinks,benchmarkSeconds,benchmarkNative,benchmarkVisual,benchmarkOmmlUsed,benchmarkOmmlCandidates,guardStatus,guardViolations,guardWarnings,mappingStatus,mappingViolations,mappingWarnings,text:bits.join(' · ')};
}

function pageFidelitySummary(report={}){
  const calibration=report.reconstructedCalibration||{};
  const comparison=calibration.selected_comparison||{};
  const countExact=calibration.selected_page_count_exact===true;
  const boundaryPass=calibration.selected_page_boundary_pass!==false;
  const exact=calibration.selected_page_fidelity_exact!==undefined
    ? calibration.selected_page_fidelity_exact===true
    : countExact;
  const sourcePages=Number(comparison.source_page_count||calibration.source_page_count||0);
  const outputPages=Number(comparison.output_page_count||0);
  const objective=Number.isFinite(Number(comparison.objective))?Number(comparison.objective).toFixed(2):'';
  const renderer=calibration.renderer||'';
  const boundaryBits=[];
  if(comparison.page_boundary_failure_count!==undefined)boundaryBits.push(`${comparison.page_boundary_failure_count} boundary failures`);
  if(comparison.average_text_end_delta_pt!==undefined)boundaryBits.push(`text-end Δ ${Number(comparison.average_text_end_delta_pt).toFixed(1)}pt`);
  const label=exact?'πιστή σελιδοποίηση':(countExact&&!boundaryPass?'ίδιες σελίδες · θέλει έλεγχο πλήρωσης':'θέλει έλεγχο σελιδοποίησης');
  const text=calibration.status
    ? `${label}${sourcePages||outputPages?` · PDF ${sourcePages||'—'} / DOCX ${outputPages||'—'} σελίδες`:''}${boundaryBits.length?` · ${boundaryBits.join(' · ')}`:''}${objective?` · score ${objective}`:''}${renderer?` · ${renderer}`:''}`
    : 'δεν έτρεξε οπτικός έλεγχος σελιδοποίησης';
  return{exact,countExact,boundaryPass,sourcePages,outputPages,objective,renderer,text};
}

function docxContributionSummary(report={}){
  const summary=report?.fidelityFallbackReport?.summary||{};
  const contribution=summary.docxContribution||{};
  const itemSourceCounts=summary.itemSourceCounts||{};
  const itemTypeCounts=summary.itemTypeCounts||{};
  const total=Number(contribution.totalItems||Object.values(itemTypeCounts).reduce((sum,value)=>sum+Number(value||0),0));
  const strong=Number(contribution.strongMatches||itemSourceCounts['docx-strong']||0);
  const omml=Number(contribution.nativeOmmlItems||itemSourceCounts['docx-native-omml']||0);
  const supported=Number(contribution.supportedItems||strong+omml);
  const ratio=Number.isFinite(Number(contribution.ratio))?Number(contribution.ratio):(total?supported/total:0);
  const grade=contribution.grade||(ratio>=.65?'high':ratio>=.35?'medium':ratio>0?'low':'none');
  const label=({high:'υψηλή',medium:'μεσαία',low:'χαμηλή',none:'μηδενική'})[grade]||grade;
  const text=total?`${label} · ${supported}/${total} στοιχεία (${Math.round(ratio*100)}%) · ${strong} strong · ${omml} OMML`:`${label} συνεισφορά DOCX`;
  return{grade,label,total,strong,omml,supported,ratio,text,note:contribution.note||''};
}

function reviewKindLabel(kind=''){
  return ({
    equation:'εξίσωση',
    alignment:'αντιστοίχιση',
    'alignment-cluster':'πλαίσιο / ομάδα',
    'pdf-text-fallback':'κείμενο από PDF',
    'callout-fallback':'πλαίσιο από PDF',
    'content-missing':'πιθανή απώλεια',
    'markdown-missing':'πιθανό κενό Markdown',
    'markdown-weak-match':'αδύναμο Markdown match',
    'markdown-pdf-spine':'Markdown/PDF spine',
    'formula-text-review':'μαθηματικό κείμενο',
    'layout-join-review':'ένωση γραμμών'
  })[kind]||kind||'έλεγχος';
}

function reviewPreviewText(item={}){
  const parts=[];
  if(item.latex)parts.push(String(item.latex));
  if(item.text)parts.push(String(item.text));
  if(item.pdfText)parts.push('PDF: '+String(item.pdfText));
  if(item.docxText)parts.push('DOCX: '+String(item.docxText));
  return parts.join('\n');
}

function latexPreviewHtml(latex=''){
  const source=String(latex||'').trim();
  if(!source||!window.MathExpressionV4?.sourceToMathML)return '';
  try{
    return `<div class="equation-preview">${window.MathExpressionV4.sourceToMathML(source,'block')}</div>`;
  }catch(error){
    return `<div class="equation-preview bad"><b>Δεν δημιουργήθηκε προεπισκόπηση.</b><br><code>${esc(source)}</code><br>${esc(error.message||error)}</div>`;
  }
}

function markdownSourceText(item={}){
  if(item.markdownText)return String(item.markdownText).trim();
  if(item.latex)return String(item.latex).trim();
  if(String(item.kind||'').startsWith('markdown')&&item.text)return String(item.text).trim();
  if(item.matchedMarkdownDonor?.latex)return String(item.matchedMarkdownDonor.latex).trim();
  return '';
}

function markdownPreviewHtml(item={}){
  const source=markdownSourceText(item);
  if(!source)return '<div class="comparison-empty">Δεν έχει συνδεθεί ακόμη Markdown στοιχείο με αυτό το σημείο παρέμβασης.</div>';
  if(item.latex)return latexPreviewHtml(item.latex)||`<div class="comparison-text">${esc(source)}</div>`;
  const meta=item.markdownId?`<div class="markdown-evidence-meta">Markdown: ${esc(item.markdownId)} · ${esc(item.markdownStatus||'status άγνωστο')}${item.markdownEvidenceScore?` · score ${esc(item.markdownEvidenceScore)}`:''}</div>`:'';
  return `<div class="markdown-preview-text">${esc(source)}</div>${meta}`;
}

function queueForReport(report={}){
  const spineQueue=report.conversionSpine?.decisionQueue||[];
  return spineQueue.map(item=>({
    ...item,
    kind:item.type||item.outcome||'conversion-spine',
    status:item.outcome||'decision-required',
    message:item.question||item.reason||'Χρειάζεται απόφαση από το conversion spine.',
    text:item.markdownText||'',
    pdfText:item.pdfText||''
  }));
}

function actionableQueueForReport(report={}){
  return [];
}

function diagnosticQueueForReport(report={}){
  return [];
}

function queueForActiveTab(report={}){
  if(state.decisionTab==='review')return actionableQueueForReport(report);
  if(state.decisionTab==='diagnostics')return diagnosticQueueForReport(report);
  return queueForReport(report);
}

function tabLabel(tab){
  return ({decisions:'Αποφάσεις',review:'Έλεγχος',diagnostics:'Διαγνωστικά'})[tab]||tab;
}

function severityLabel(value=''){
  return ({confirm:'χρειάζεται απόφαση',review:'χρειάζεται έλεγχο',diagnostic:'διαγνωστικό',ok:'OK'})[value]||value||'έλεγχος';
}

function outputStateText(item={}){
  const bits=[];
  if(item.outputEvidenceLabel)bits.push(`παραγόμενο: ${item.outputEvidenceLabel}`);
  if(item.status)bits.push(`status: ${item.status}`);
  if(item.finalSource)bits.push(`έξοδος: ${item.finalSource}`);
  if(item.lossStatus)bits.push(`πληρότητα: ${item.lossStatus}`);
  if(item.score!==undefined&&item.score!==null)bits.push(`score: ${item.score}`);
  if(item.resolution)bits.push(String(item.resolution));
  if(item.failure)bits.push(`failure: ${item.failure}`);
  return bits.join('\n');
}

function columnHidden(name){
  return !!state.hiddenColumns[name];
}

function comparisonColumnClass(name=''){
  return name&&columnHidden(name)?' hidden-column':'';
}

function sourcePanelHtml(title,body,extra='',column=''){
  const content=String(body||'').trim();
  return `<section class="comparison-source${comparisonColumnClass(column)}">
    <h3>${esc(title)}</h3>
    ${content?`<div class="comparison-text">${esc(content)}</div>`:'<div class="comparison-empty">Το τρέχον report δεν έχει συνδεδεμένο περιεχόμενο για αυτό το σημείο.</div>'}
    ${extra||''}
  </section>`;
}

function pdfWitnessHtml(item={}){
  const bbox=Array.isArray(item.bbox)&&item.bbox.length===4?item.bbox.map(Number):null;
  const size=item.pdfPageSize||{};
  const width=Number(size.widthPt||0);
  const height=Number(size.heightPt||0);
  const zoom=Math.max(0.8,Math.min(3,Number(state.pdfZoom)||1.45));
  if(item.pdfPagePreviewUrl){
    const overlay=bbox&&width&&height?`<span class="pdf-highlight" style="left:${bbox[0]/width*100}%;top:${bbox[1]/height*100}%;width:${(bbox[2]-bbox[0])/width*100}%;height:${(bbox[3]-bbox[1])/height*100}%"></span>`:'';
    return `<section class="comparison-source pdf-witness">
      <h3>PDF μάρτυρας · σελ. ${esc(item.page||'—')}</h3>
      <div class="pdf-page-frame"><div class="pdf-page-canvas" style="width:${Math.round(zoom*100)}%"><img src="${esc(item.pdfPagePreviewUrl)}" alt="PDF page preview">${overlay}</div></div>
      ${item.previewUrl?`<div class="pdf-crop-strip"><b>Crop:</b><img src="${esc(item.previewUrl)}" alt="PDF crop preview"></div>`:''}
    </section>`;
  }
  if(item.previewUrl){
    return `<section class="comparison-source pdf-witness">
      <h3>PDF μάρτυρας · σελ. ${esc(item.page||'—')}</h3>
      <div class="pdf-crop-only"><img src="${esc(item.previewUrl)}" alt="PDF crop preview"></div>
      <div class="comparison-empty">Δεν έχει αποθηκευτεί πλήρης σελίδα PDF για highlight σε αυτό το report.</div>
    </section>`;
  }
  const extracted=item.pdfText||item.text||'';
  return `<section class="comparison-source pdf-witness missing-visual">
    <h3>PDF μάρτυρας · σελ. ${esc(item.page||'—')}</h3>
    <div class="notice small bad">Δεν υπάρχει οπτική προεπισκόπηση PDF για αυτό το σημείο στο τρέχον report. Το παρακάτω είναι μόνο extracted text και δεν αρκεί για σίγουρη κρίση.</div>
    ${extracted?`<div class="comparison-text">${esc(extracted)}</div>`:'<div class="comparison-empty">Δεν δίνεται ούτε extracted text στο report.</div>'}
  </section>`;
}

function reviewComparisonHtml(item={}){
  const docx=item.docxText||'';
  const markdownSource=markdownSourceText(item);
  return `<div class="comparison-grid">
    ${pdfWitnessHtml(item)}
    ${sourcePanelHtml('Markdown εκδοχή',markdownSource,'','markdown')}
    <section class="comparison-source${comparisonColumnClass('preview')}">
      <h3>Προεπισκόπηση Markdown</h3>
      ${markdownPreviewHtml(item)}
      ${item.kind==='equation'&&!item.latex?'<div class="notice small warn">Δεν υπάρχει ασφαλές Markdown LaTeX donor. Η τρέχουσα οπτική απόδοση είναι σημείο εκκίνησης για χειροκίνητη διόρθωση.</div>':''}
    </section>
    <section class="comparison-source docx-donor-panel${comparisonColumnClass('docx')}">
      <h3>Mathpix DOCX donor</h3>
      ${docx?`<div class="comparison-text">${esc(docx)}</div>`:'<div class="comparison-empty">Δεν υπάρχει χρήσιμο DOCX donor για αυτό το σημείο.</div>'}
      ${item.rejectedDocxCandidate?'<div class="notice small warn">Ο αυτοματισμός απέρριψε αυτόν τον DOCX candidate ως αδύναμο. Παραμένει χρήσιμη βοήθεια για το μάτι, όχι πηγή αλήθειας.</div>':''}
    </section>
    <section class="comparison-source intervention-panel${comparisonColumnClass('intervention')}">
      <h3>Επέμβαση</h3>
      <div class="intervention-actions">
        <button type="button" data-quick-choice="keep-pdf-markdown">Αποδοχή PDF/Markdown</button>
        <button type="button" data-quick-choice="manual-docx-link">Χρήση DOCX donor</button>
        <button type="button" data-quick-choice="ignore">Απόρριψη ένστασης</button>
        <button type="button" data-quick-choice="ok">OK</button>
      </div>
      <div class="comparison-text output-state">${esc(outputStateText(item)||'Δεν υπάρχει ξεχωριστό σήμα εξόδου στο report.')}</div>
    </section>
  </div>`;
}

function interventionViewControls(){
  const zoom=Math.round((Number(state.pdfZoom)||1.45)*100);
  const toggles=[
    ['markdown','Markdown'],
    ['preview','Preview'],
    ['docx','DOCX donor'],
    ['intervention','Επέμβαση']
  ];
  return `<div class="view-controls">
    <span>PDF zoom <b>${esc(zoom)}%</b></span>
    <button type="button" data-pdf-zoom="out">−</button>
    <button type="button" data-pdf-zoom="in">+</button>
    ${toggles.map(([key,label])=>`<label><input type="checkbox" data-toggle-column="${esc(key)}"${columnHidden(key)?' checked':''}> κρύψε ${esc(label)}</label>`).join('')}
  </div>`;
}

function decisionStorageKey(report={},gateway={}){
  return `bw-mathpix-converter-decisions-${gateway.token||report.reportToken||'latest'}`;
}

function loadDecisions(report,gateway){
  try{
    const parsed=JSON.parse(storage.get(decisionStorageKey(report,gateway),'{}'));
    return {format:'mathpix-converter-user-decisions-v1',items:{},...parsed,items:parsed.items||{}};
  }catch{
    return {format:'mathpix-converter-user-decisions-v1',items:{}};
  }
}

function saveDecisions(report,gateway,decisions){
  decisions.updatedAt=new Date().toISOString();
  storage.set(decisionStorageKey(report,gateway),JSON.stringify(decisions));
}

function itemId(item,index){
  return String(item.id||`${item.kind||'review'}-${item.page||'x'}-${index}`);
}

function decisionOptions(value='pending'){
  const labels={
    pending:'εκκρεμεί',
    'keep-pdf-markdown':'κρατάμε PDF/Markdown',
    'manual-docx-link':'χειροκίνητη σύνδεση DOCX',
    'edit-equation-source':'διόρθωση εξίσωσης',
    'add-missing-content':'προσθήκη περιεχομένου',
    'fix-in-word':'διόρθωση στο Word',
    ok:'σωστό',
    ignore:'αγνόηση'
  };
  return Object.entries(labels).map(([key,label])=>`<option value="${esc(key)}"${key===value?' selected':''}>${esc(label)}</option>`).join('');
}

function renderDecisions(){
  const current=state.current;
  if(!current)return;
  const report=current.report||{};
  const decisionQueue=queueForReport(report);
  const actionableQueue=actionableQueueForReport(report);
  const diagnosticQueue=diagnosticQueueForReport(report);
  const queue=queueForActiveTab(report);
  const panel=$('#decisionPanel');
  if(!decisionQueue.length&&!actionableQueue.length&&!diagnosticQueue.length){
    panel.className='decision-panel empty';
    panel.innerHTML='<div class="empty-state"><b>Δεν υπάρχουν σημεία επέμβασης.</b><span>Το τελευταίο run δεν ζήτησε ανθρώπινη απόφαση ή έλεγχο.</span></div>';
    return;
  }
  const kinds=[...new Set(queue.map(item=>item.kind||'review'))];
  const decisions=loadDecisions(report,current);
  if(state.decisionFilter!=='all'&&!kinds.includes(state.decisionFilter))state.decisionFilter='all';
  const filtered=(state.decisionFilter==='all'?queue:queue.filter(item=>(item.kind||'review')===state.decisionFilter))
    .map(item=>({item,index:queue.indexOf(item)}));
  panel.classList.remove('hidden','empty');
  panel.innerHTML=`
    <div class="decision-toolbar">
      <b>Περιβάλλον επέμβασης</b>
      <div class="decision-tabs">
        ${[
          ['decisions',decisionQueue.length],
          ['review',actionableQueue.length],
          ['diagnostics',diagnosticQueue.length]
        ].map(([tab,count])=>`<button type="button" data-decision-tab="${esc(tab)}" class="${state.decisionTab===tab?'active':''}">${esc(tabLabel(tab))} <b>${esc(count)}</b></button>`).join('')}
      </div>
      <select id="decisionFilter">
        <option value="all">όλα</option>
        ${kinds.map(kind=>`<option value="${esc(kind)}"${state.decisionFilter===kind?' selected':''}>${esc(reviewKindLabel(kind))}</option>`).join('')}
      </select>
      <button id="exportDecisionsButton" type="button">Λήψη αποφάσεων JSON</button>
      ${interventionViewControls()}
    </div>
    <div class="notice small ${state.decisionTab==='decisions'?'warn':''}">
      ${state.decisionTab==='decisions'
        ? 'Εδώ εμφανίζονται μόνο τα σημεία όπου ο μετατροπέας ζητά πραγματική κρίση/διόρθωση πριν θεωρηθεί ώριμο το DOCX.'
        : state.decisionTab==='review'
          ? 'Εδώ φαίνονται ουσιαστικά τεκμήρια ελέγχου. Δεν είναι όλα αποφάσεις, αλλά βοηθούν να καταλάβεις γιατί ο μετατροπέας κράτησε PDF/Markdown ή απέρριψε DOCX donor.'
          : 'Εδώ μπαίνουν χαμηλής προτεραιότητας διαγνωστικά όταν υπάρχουν. Δεν πρέπει να βαραίνουν την πρώτη κρίση του χρήστη.'}
    </div>
    <div class="decision-list">
      ${filtered.map(({item,index})=>{
        const id=itemId(item,index);
        const saved=decisions.items[id]||{};
        return `<article class="decision-card" data-decision-id="${esc(id)}">
          <header><span>#${index+1} · ${esc(reviewKindLabel(item.kind))}</span><span class="decision-meta">σελ. ${esc(item.page||'—')} · ${esc(severityLabel(item.severity))}</span></header>
          <div class="decision-question">${esc(item.question||item.message||'Χρειάζεται έλεγχος.')}</div>
          ${item.recommendedAction?`<div class="decision-recommendation">${esc(item.recommendedAction)}</div>`:''}
          ${reviewComparisonHtml(item)}
          <label><span>Απόφαση</span><select data-decision-choice>${decisionOptions(saved.choice||'pending')}</select></label>
          <label><span>Σημείωση</span><textarea data-decision-note rows="2">${esc(saved.note||'')}</textarea></label>
          <div class="rich-edit-shell">
            <div class="rich-edit-toolbar">
              <button type="button" data-rich-command="bold"><b>B</b></button>
              <button type="button" data-rich-command="italic"><i>I</i></button>
              <button type="button" data-rich-command="insertUnorderedList">• λίστα</button>
            </div>
            <div class="rich-edit-surface" contenteditable="true" data-decision-rich>${saved.correctionHtml||esc(saved.correction||'')}</div>
          </div>
        </article>`;
      }).join('')}
    </div>`;
  panel.querySelectorAll('[data-decision-tab]').forEach(button=>{
    button.onclick=()=>{
      state.decisionTab=button.dataset.decisionTab||'decisions';
      state.decisionFilter='all';
      renderDecisions();
    };
  });
  $('#decisionFilter').onchange=event=>{state.decisionFilter=event.target.value;renderDecisions()};
  $('#exportDecisionsButton').onclick=()=>downloadBlob(new Blob([JSON.stringify(decisions,null,2)],{type:'application/json'}),'mathpix_user_decisions.json');
  panel.querySelectorAll('[data-pdf-zoom]').forEach(button=>{
    button.onclick=()=>{
      const delta=button.dataset.pdfZoom==='in'?0.2:-0.2;
      state.pdfZoom=Math.max(0.8,Math.min(3,(Number(state.pdfZoom)||1.45)+delta));
      storage.set('bw-mathpix-pdf-zoom',String(state.pdfZoom));
      renderDecisions();
    };
  });
  panel.querySelectorAll('[data-toggle-column]').forEach(input=>{
    input.onchange=()=>{
      state.hiddenColumns[input.dataset.toggleColumn]=input.checked;
      storage.set('bw-mathpix-hidden-columns',JSON.stringify(state.hiddenColumns));
      renderDecisions();
    };
  });
  panel.querySelectorAll('.decision-card').forEach(card=>{
    const id=card.dataset.decisionId;
    const persist=()=>{
      decisions.items[id]={
        choice:card.querySelector('[data-decision-choice]').value,
        note:card.querySelector('[data-decision-note]').value,
        correctionHtml:card.querySelector('[data-decision-rich]').innerHTML,
        updatedAt:new Date().toISOString()
      };
      saveDecisions(report,current,decisions);
    };
    card.querySelector('[data-decision-choice]').onchange=persist;
    card.querySelector('[data-decision-note]').oninput=persist;
    card.querySelector('[data-decision-rich]').oninput=persist;
    card.querySelectorAll('[data-quick-choice]').forEach(button=>{
      button.onclick=()=>{
        card.querySelector('[data-decision-choice]').value=button.dataset.quickChoice||'pending';
        persist();
      };
    });
    card.querySelectorAll('[data-rich-command]').forEach(button=>{
      button.onclick=()=>{
        card.querySelector('[data-decision-rich]').focus();
        document.execCommand(button.dataset.richCommand,false,null);
        persist();
      };
    });
  });
}

function renderResult(gateway,sourceLabel,pages){
  const report=gateway.report||{};
  const mapsFailure=mapsFirstFailureReason(report);
  const fidelity=mathpixFidelitySummary(report);
  const pageFidelity=pageFidelitySummary(report);
  const contribution=docxContributionSummary(report);
  const decisions=queueForReport(report);
  const actionable=actionableQueueForReport(report);
  const diagnostics=diagnosticQueueForReport(report);
  const visualWitness=visualWitnessSummary(report);
  const draft=report.reconstructedDocxArtifact?.requiresUserReview||decisions.length||!pageFidelity.exact;
  const previewQueue=decisions.length?decisions:actionable;
  const rows=previewQueue.slice(0,14).map((item,index)=>`<tr><td>${index+1}</td><td>${esc(reviewKindLabel(item.kind))}</td><td>${esc(item.page||'')}</td><td>${esc(item.question||item.message||'')}</td><td>${esc(reviewPreviewText(item))}</td></tr>`).join('');
  const panel=$('#resultPanel');
  panel.classList.remove('hidden','empty');
  if(mapsFailure){
    panel.innerHTML=`
      <div class="notice bad"><b>Η μετατροπή σταμάτησε ως μη έγκυρη maps-first έξοδος.</b><br>${esc(mapsFailure)}</div>
      <div class="button-row">
        <button id="downloadReportButton" type="button">Λήψη report JSON</button>
      </div>`;
    $('#downloadReportButton').onclick=downloadCurrentReport;
    updateInterventionCount();
    return;
  }
  panel.innerHTML=`
    <div class="notice ${pageFidelity.exact?'good':'warn'}"><b>Η μετατροπή ολοκληρώθηκε.</b><br>Το προϊόν είναι reconstructed DOCX για άνοιγμα, διόρθωση και συμπλήρωση στο Word.</div>
    <div class="summary-grid">
      <span>Πηγή</span><b>${esc(sourceLabel)}</b>
      <span>Σελίδες</span><b>${esc(pages)}</b>
      <span>Σελιδοποίηση</span><b>${esc(pageFidelity.text)}</b>
      <span>Markdown</span><b>${esc(fidelity.text||'χωρίς σύνοψη')}</b>
      <span>Λογική αλήθειας</span><b>${esc(fidelity.spineItems?`Markdown first · PDF οδηγός · DOCX δότης (${fidelity.spineScope||'spine'})`:'Markdown first · περιμένει νέο report spine')}</b>
      <span>Συνεισφορά Mathpix DOCX</span><b>${esc(contribution.text)}</b>
      <span>Status</span><b>${esc(draft?'draft προς έλεγχο':'approved candidate')}</b>
      <span>DOCX</span><b>${esc(gateway.outputName)}</b>
      <span>PDF μάρτυρες</span><b>${esc(`${visualWitness.pageImages} σελίδες με highlight · ${visualWitness.crops} crops · ${visualWitness.missing} χωρίς εικόνα`)}</b>
    </div>
    ${visualWitness.total&&visualWitness.pageImages===0&&visualWitness.crops===0?'<div class="notice bad"><b>Το τρέχον report δεν έχει οπτικούς PDF μάρτυρες για την Παρέμβαση.</b><br>Τα σημεία μπορούν να διαβαστούν ως extracted text, αλλά δεν είναι αρκετό για ασφαλή ανθρώπινη κρίση. Χρειάζεται νέο run με τον ενημερωμένο server ώστε να αποθηκευτούν εικόνες σελίδων/crops.</div>':''}
    ${decisions.length||actionable.length||diagnostics.length?`<div class="notice warn"><b>${decisions.length}</b> αποφάσεις · <b>${actionable.length}</b> ουσιαστικά σημεία ελέγχου · <b>${diagnostics.length}</b> διαγνωστικά.</div>
      <div class="review-table-wrap"><table class="review-table">
        <thead><tr><th>#</th><th>Είδος</th><th>Σελ.</th><th>Ερώτημα</th><th>Στοιχείο</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`:''}
    <div class="button-row">
      <button id="downloadReportButton" type="button">Λήψη report JSON</button>
      <button id="openDecisionButton" type="button"${decisions.length||actionable.length||diagnostics.length?'':' disabled'}>Περιβάλλον επέμβασης</button>
      <button id="downloadDocxButton" class="primary" type="button">${draft?'Λήψη draft DOCX':'Λήψη DOCX'}</button>
    </div>`;
  $('#downloadReportButton').onclick=downloadCurrentReport;
  $('#downloadDocxButton').onclick=downloadCurrentDocx;
  $('#openDecisionButton').onclick=()=>{activateWorkspaceTab('intervention');renderDecisions()};
  updateInterventionCount();
}

async function handleSubmit(event){
  event.preventDefault();
  await refreshGatewayIdentity();
  const pdfFile=$('#pdfInput').files?.[0];
  const markdownFile=$('#markdownInput').files?.[0];
  const docxFile=$('#docxInput').files?.[0];
  const pages=String($('#pagesInput').value||'').trim();
  if(!pdfFile){showHint('Επίλεξε πρώτα το αρχικό PDF.','bad');return}
  if(!markdownFile){showHint('Επίλεξε το Mathpix Markdown ZIP με τις εικόνες.','bad');return}
  if(!docxFile){showHint('Επίλεξε το Mathpix DOCX donor. Σήμερα ο pipeline το χρειάζεται για OMML/μορφοποίηση και αντιστοίχιση.','bad');return}
  if(!pages){showHint('Δώσε ρητό εύρος σελίδων, π.χ. <code>1-3</code> ή <code>17-64</code>.','bad');return}
  storage.set('bw-v5-mathpix-pages',pages);
  $('#resultPanel').classList.add('hidden');
  $('#decisionPanel').classList.add('hidden');
  $('#startButton').disabled=true;
  $('#resetButton').disabled=true;
  showHint('Η μετατροπή ξεκίνησε. Μην κλείσεις το παράθυρο CMD όσο δουλεύει η τοπική πύλη.','warn');
  setStatus('Η πύλη δουλεύει τις πηγές PDF/Mathpix.','warn');
  startProgress();
  const progressToken=makeProgressToken();
  startProgressPolling(progressToken);
  try{
    const gateway=await convertMathpix(pdfFile,markdownFile,docxFile,pages,{
      renderFidelity:$('#renderFidelityInput').checked,
      calibration:$('#calibrationInput').value,
      progressToken
    });
    state.current=gateway;
    stopProgress('ολοκληρώθηκε',100);
    setStatus('Η μετατροπή ολοκληρώθηκε. Έτοιμο reconstructed DOCX και report.','good');
    showHint('Έτοιμο. Το DOCX είναι προϊόν του ανεξάρτητου μετατροπέα.','good');
    renderResult(gateway,pdfFile.name,pages);
    activateWorkspaceTab('report');
  }catch(error){
    console.error(error);
    stopProgress('αποτυχία',100);
    setStatus('Η μετατροπή απέτυχε.','bad');
    const payload=error.gatewayPayload||{};
    const report=payload.reportUrl?`<br>Report: <code>${esc(location.origin+payload.reportUrl)}</code>`:'';
    const dir=payload.failureArtifactDir?`<br>Διαγνωστικός φάκελος: <code>${esc(payload.failureArtifactDir)}</code>`:'';
    const reviewArtifact=payload.reconstructedDocxArtifact||{};
    const reviewUrl=reviewArtifact.downloadUrl?new URL(reviewArtifact.downloadUrl,location.origin).href:'';
    const review=reviewUrl?`<br><a href="${esc(reviewUrl)}">Λήψη fast review DOCX για οπτικό έλεγχο</a><br><span>Δεν είναι τελικό προϊόν: το strict page gate απέτυχε.</span>`:'';
    showHint(`${esc(error.message||'Η μετατροπή απέτυχε.')}${review}${report}${dir}`,'bad');
  }finally{
    $('#startButton').disabled=false;
    $('#resetButton').disabled=false;
  }
}

function resetForm(){
  $('#converterForm').reset();
  state.current=null;
  state.decisionFilter='all';
  state.decisionTab='decisions';
  state.startedAt=0;
  $('#renderFidelityInput').checked=true;
  $('#calibrationInput').value='fast';
  $('#pagesInput').value=storage.get('bw-v5-mathpix-pages','');
  $('#resultPanel').className='result-panel empty';
  $('#resultPanel').innerHTML='<div class="empty-state"><b>Δεν υπάρχει ακόμη αναφορά.</b><span>Η σύνοψη πιστότητας και οι λήψεις θα εμφανιστούν μετά τη μετατροπή.</span></div>';
  $('#decisionPanel').className='decision-panel empty';
  $('#decisionPanel').innerHTML='<div class="empty-state"><b>Δεν υπάρχει ακόμη run για επέμβαση.</b><span>Μετά τη μετατροπή εδώ θα εμφανιστούν οι αποφάσεις και τα σημεία ελέγχου.</span></div>';
  $('#progressBar').style.width='0';
  $('#stageText').textContent='αναμονή';
  $('#elapsedText').textContent='0s';
  $('#runBadge').textContent='αναμονή';
  $('#runBadge').className='run-badge';
  renderGatewayIdentity(state.gatewayIdentity);
  updateInterventionCount();
  setStatus('Ο μετατροπέας δεν έχει ξεκινήσει.');
  showHint('Για πρώτη δοκιμή προτίμησε μικρό εύρος, όπως <code>1-3</code> ή <code>17-20</code>.');
  activateWorkspaceTab('conversion');
}

function init(){
  $('#pagesInput').value=storage.get('bw-v5-mathpix-pages','');
  $('#converterForm').addEventListener('submit',handleSubmit);
  $('#resetButton').addEventListener('click',resetForm);
  document.querySelectorAll('[data-workspace-tab]').forEach(button=>{
    button.addEventListener('click',()=>activateWorkspaceTab(button.dataset.workspaceTab||'conversion'));
  });
  document.querySelectorAll('[data-app-command]').forEach(button=>{
    button.addEventListener('click',()=>{
      const command=button.dataset.appCommand||'';
      if(command==='start')$('#converterForm').requestSubmit();
      else if(command==='reset')resetForm();
      else if(command==='download-docx')downloadCurrentDocx();
      else if(command==='download-report')downloadCurrentReport();
      else if(command==='tab-conversion')activateWorkspaceTab('conversion');
      else if(command==='tab-intervention'){activateWorkspaceTab('intervention');renderDecisions()}
      else if(command==='tab-report')activateWorkspaceTab('report');
      closeMenus();
    });
  });
  activateWorkspaceTab('conversion');
  updateInterventionCount();
  refreshGatewayIdentity();
}

init();
})();
