(function(global){
'use strict';
const VERSION='1.0.0';
const NS='http://www.w3.org/1998/Math/MathML';
const GREEK={
  alpha:'α',beta:'β',gamma:'γ',delta:'δ',epsilon:'ε',varepsilon:'ϵ',zeta:'ζ',eta:'η',theta:'θ',vartheta:'ϑ',iota:'ι',kappa:'κ',lambda:'λ',mu:'μ',nu:'ν',xi:'ξ',omicron:'ο',pi:'π',varpi:'ϖ',rho:'ρ',varrho:'ϱ',sigma:'σ',varsigma:'ς',tau:'τ',upsilon:'υ',phi:'φ',varphi:'ϕ',chi:'χ',psi:'ψ',omega:'ω',
  Gamma:'Γ',Delta:'Δ',Theta:'Θ',Lambda:'Λ',Xi:'Ξ',Pi:'Π',Sigma:'Σ',Upsilon:'Υ',Phi:'Φ',Psi:'Ψ',Omega:'Ω'
};
const OPERATORS={
  cdot:'⋅',times:'×',div:'÷',pm:'±',mp:'∓',le:'≤',leq:'≤',ge:'≥',geq:'≥',ne:'≠',neq:'≠',approx:'≈',equiv:'≡',propto:'∝',to:'→',rightarrow:'→',leftarrow:'←',leftrightarrow:'↔',infty:'∞',partial:'∂',nabla:'∇',sum:'∑',prod:'∏',int:'∫',oint:'∮',therefore:'∴',because:'∵',perp:'⟂',parallel:'∥',angle:'∠',degree:'°'
};
const FUNCTIONS=new Set(['sin','cos','tan','cot','sec','csc','sinh','cosh','tanh','ln','log','exp','lim','max','min']);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":'&apos;'}[ch]));
const stripMath=value=>String(value||'').replace(/^\s*\$+|\$+\s*$/g,'').trim();
function tokenize(input){
  const source=stripMath(input);const tokens=[];let i=0;
  while(i<source.length){
    const ch=source[i];
    if(/\s/.test(ch)){i++;continue;}
    if(ch==='\\'){
      let j=i+1;while(j<source.length&&/[A-Za-z]/.test(source[j]))j++;
      if(j===i+1&&j<source.length)j++;
      tokens.push({type:'command',value:source.slice(i+1,j)});i=j;continue;
    }
    if('{}_^'.includes(ch)){tokens.push({type:ch,value:ch});i++;continue;}
    if(/[0-9]/.test(ch)||((ch==='.'||ch===',')&&/[0-9]/.test(source[i+1]||''))){
      let j=i+1;while(j<source.length&&/[0-9.,]/.test(source[j]))j++;
      tokens.push({type:'number',value:source.slice(i,j)});i=j;continue;
    }
    if(/[A-Za-zΑ-Ωα-ωΆ-ώϐ-ϖ]/.test(ch)){
      let j=i+1;while(j<source.length&&/[A-Za-zΑ-Ωα-ωΆ-ώϐ-ϖ]/.test(source[j]))j++;
      tokens.push({type:'identifier',value:source.slice(i,j)});i=j;continue;
    }
    if('+-=<>/⋅×·±∓≈≠≤≥→←↔∞∂∇∑∫∏()[]|,:;'.includes(ch)){tokens.push({type:'operator',value:ch});i++;continue;}
    tokens.push({type:'identifier',value:ch});i++;
  }
  return tokens;
}
function parse(source,display='inline'){
  const tokens=tokenize(source);let pos=0;
  const peek=()=>tokens[pos];const take=()=>tokens[pos++];
  const node=(tag,content='',attrs='')=>`<${tag}${attrs}>${content}</${tag}>`;
  const row=parts=>node('mrow',parts.join(''));
  function parseGroup(){
    if(peek()?.type==='{'){
      take();const content=parseSequence('}');
      if(peek()?.type!=='}')throw new Error('Λείπει κλείσιμο αγκύλης }');
      take();return content;
    }
    return parseAtom();
  }
  function parseCommand(command){
    if(command==='left'||command==='right')return parseAtom();
    if(command==='frac')return node('mfrac',parseGroup()+parseGroup());
    if(command==='sqrt')return node('msqrt',parseGroup());
    if(command==='text'){
      if(peek()?.type!=='{')throw new Error('Η \\text απαιτεί {...}');
      take();let text='';let depth=1;
      while(pos<tokens.length&&depth){const t=take();if(t.type==='{')depth++;else if(t.type==='}')depth--;if(depth)text+=t.value;}
      if(depth)throw new Error('Λείπει κλείσιμο αγκύλης στη \\text');
      return node('mtext',esc(text));
    }
    if(GREEK[command])return node('mi',GREEK[command]);
    if(OPERATORS[command])return node('mo',OPERATORS[command]);
    if(FUNCTIONS.has(command))return node('mi',esc(command),' mathvariant="normal"');
    if(command==='mathrm'||command==='mathbf'||command==='mathit'){
      const variant=command==='mathbf'?'bold':command==='mathit'?'italic':'normal';
      return node('mstyle',parseGroup(),` mathvariant="${variant}"`);
    }
    return node('mi',esc(command));
  }
  function parseAtom(){
    const t=take();if(!t)return node('mrow','');
    if(t.type==='{'){pos--;return parseGroup();}
    if(t.type==='command')return parseCommand(t.value);
    if(t.type==='number')return node('mn',esc(t.value.replace(',','.')));
    if(t.type==='identifier')return node('mi',esc(t.value));
    if(t.type==='operator'){
      if(t.value==='('||t.value==='['||t.value==='|')return node('mo',esc(t.value),' stretchy="true"');
      if(t.value===')'||t.value===']')return node('mo',esc(t.value),' stretchy="true"');
      return node('mo',esc(t.value));
    }
    if(t.type==='}')throw new Error('Απρόσμενο }');
    return node('mi',esc(t.value));
  }
  function parseScripted(){
    let base=parseAtom(),sub=null,sup=null;
    while(peek()&&(peek().type==='_'||peek().type==='^')){
      const kind=take().type;const script=parseGroup();if(kind==='_')sub=script;else sup=script;
    }
    if(sub&&sup)return node('msubsup',base+sub+sup);
    if(sub)return node('msub',base+sub);
    if(sup)return node('msup',base+sup);
    return base;
  }
  function parseSequence(stop=null){
    const parts=[];
    while(pos<tokens.length&&(!stop||peek().type!==stop))parts.push(parseScripted());
    return row(parts);
  }
  const body=parseSequence();
  if(pos<tokens.length)throw new Error('Η εξίσωση δεν αναλύθηκε πλήρως.');
  const mode=display==='block'?'block':'inline';
  return `<math xmlns="${NS}" display="${mode}">${body}</math>`;
}
function validate(source){try{return{ok:true,mathml:parse(source)}}catch(error){return{ok:false,error:error.message,mathml:''}}}
const templates=[
  {label:'Κλάσμα',insert:'\\frac{a}{b}'},{label:'Ρίζα',insert:'\\sqrt{x}'},{label:'Δείκτης',insert:'x_{i}'},{label:'Εκθέτης',insert:'x^{2}'},
  {label:'Άθροισμα',insert:'\\sum_{i=1}^{n}'},{label:'Ολοκλήρωμα',insert:'\\int_{a}^{b}'},{label:'Διάνυσμα',insert:'\\mathbf{E}'},
  {label:'α',insert:'\\alpha'},{label:'β',insert:'\\beta'},{label:'γ',insert:'\\gamma'},{label:'Δ',insert:'\\Delta'},
  {label:'λ',insert:'\\lambda'},{label:'μ',insert:'\\mu'},{label:'π',insert:'\\pi'},{label:'ω',insert:'\\omega'},
  {label:'±',insert:'\\pm'},{label:'×',insert:'\\times'},{label:'⋅',insert:'\\cdot'},{label:'→',insert:'\\to'},{label:'∞',insert:'\\infty'}
];
const api={VERSION,tokenize,sourceToMathML:parse,validate,templates};
global.MathExpressionV4=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
