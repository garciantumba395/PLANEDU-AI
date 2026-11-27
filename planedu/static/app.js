(()=>{'use strict';
/* ---------- utilitaires ---------- */
const NS='http://www.w3.org/2000/svg';
const add=(e,k)=>{for(const c of k.flat(Infinity)){if(c==null||c===false)continue;e.append(c.nodeType?c:String(c));}return e;};
function h(t,a,...k){const e=document.createElement(t);for(const n in a||{}){const v=a[n];if(v==null||v===false)continue;if(n==='class')e.className=v;else if(n.slice(0,2)==='on')e.addEventListener(n.slice(2),v);else if(n==='value')e.value=v;else e.setAttribute(n,v===true?'':v);}return add(e,k);}
function sv(t,a,...k){const e=document.createElementNS(NS,t);for(const n in a||{}){if(a[n]!=null)e.setAttribute(n,a[n]);}return add(e,k);}
const fm=n=>n==null||n===''?'—':Number(n).toLocaleString('fr-FR',{maximumFractionDigits:1});
const fp=p=>p==null?'—':Math.round(p*100)+' %';
const arr=(r,k)=>Array.isArray(r)?r:(r&&(r[k]||r.items))||[];
const ls={get(k){try{return localStorage.getItem(k);}catch(_){return null;}},set(k,v){try{localStorage.setItem(k,v);}catch(_){}} };
const ss={get(k){try{return sessionStorage.getItem(k);}catch(_){return null;}},set(k,v){try{v==null?sessionStorage.removeItem(k):sessionStorage.setItem(k,v);}catch(_){}} };
const S={token:ss.get('pl_tok'),user:null,geo:null,lite:ls.get('pl_lite')==='1',f:{},rid:0};
const PAGE=()=>S.lite||innerWidth<640?20:50;
const $=s=>document.querySelector(s);
const nameOf=x=>x.nom||x.enseignant_id||'—';
const ROLES={admin:'Administrateur',gestionnaire:'Gestionnaire',chef:"Chef d'établissement",decideur:'Décideur'};

function toast(m,e){const t=h('div',{class:'toast'+(e?' e':'')},m);$('#toasts').append(t);setTimeout(()=>t.remove(),5500);}
function say(m){$('#status').textContent=m;}
function errBox(e){return h('div',{class:'err',role:'alert'},e.message||'Erreur inconnue');}

/* ---------- API ---------- */
async function api(path,o={}){
  const init={method:o.method||'GET',headers:{}};
  if(S.token)init.headers.Authorization='Bearer '+S.token;
  if(o.body!==undefined){init.headers['Content-Type']='application/json';init.body=JSON.stringify(o.body);}
  let r;
  try{r=await fetch(path,init);}catch(_){const x=new Error('Serveur injoignable. Vérifiez la connexion.');x.net=true;throw x;}
  if(!r.ok){
    let d={};try{d=await r.json();}catch(_){}
    if(r.status===401&&!o.noauth){endSession('Session expirée ou invalide, veuillez vous reconnecter.');const x=new Error('Session expirée');x.status=401;throw x;}
    const x=new Error(r.status===403?'Accès refusé : hors de votre périmètre ou de votre rôle.':(d.error||('Erreur HTTP '+r.status)));
    x.status=r.status;x.data=d;throw x;
  }
  if(o.raw)return r;
  const t=await r.text();try{return t?JSON.parse(t):{};}catch(_){return {};}
}
const qs=o=>Object.entries(o).filter(([,v])=>v!==''&&v!=null).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
function saveBlob(b,name){const u=URL.createObjectURL(b);const a=h('a',{href:u,download:name});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),3000);}
async function exportFile(table,fmt,query){
  try{
    const r=await api('/api/export/'+table+'.'+fmt+(query?'?'+query:''),{raw:true});const b=await r.blob();
    if(fmt==='csv'){b.text().then(t=>{if(t.length<400000)ls.set('pl_exp_'+table,JSON.stringify({d:new Date().toLocaleString('fr-FR'),t}));});}
    saveBlob(b,'planedu_'+table+'.'+fmt);toast('Export '+fmt.toUpperCase()+' téléchargé.');
  }catch(e){
    if(e.net){let c=null;try{c=JSON.parse(ls.get('pl_exp_'+table));}catch(_){}
      if(c){saveBlob(new Blob([c.t],{type:'text/csv'}),'planedu_'+table+'_hors_ligne.csv');toast('Hors ligne : dernier export CSV en cache ('+c.d+').');return;}}
    if(e.status!==401)toast(e.message,true);
  }
}
const exportBar=(table,getQ,fmts)=>h('div',{class:'row',role:'group','aria-label':'Exporter'},h('span',{class:'mut'},'Exporter :'),(fmts||['csv','xlsx','pdf']).map(f=>h('button',{type:'button',class:'btn sec sm',onclick:()=>exportFile(table,f,getQ())},f.toUpperCase())));

/* ---------- session ---------- */
function endSession(msg){S.token=null;S.user=null;S.geo=null;ss.set('pl_tok',null);if(msg)toast(msg,true);if(location.hash!=='#/login')location.hash='#/login';else route();}
const can=roles=>S.user&&roles.includes(S.user.role);
const MENU=[['dashboard','Tableau de bord',['admin','gestionnaire','chef','decideur']],['previsions','Prévisions',['gestionnaire','chef']],['besoins','Besoins et écarts',['gestionnaire','chef','decideur']],['redeploiements','Redéploiements',['gestionnaire']],['donnees','Données',['admin','gestionnaire']],['modeles','Modèles',['admin','gestionnaire','decideur']],['alertes','Alertes',['gestionnaire']],['admin','Administration',['admin']]];
function chrome(){
  const on=!!S.user;$('#top').hidden=!on;$('#nav').hidden=!on;
  if(!on)return;
  $('#who').textContent=S.user.login+' — '+(ROLES[S.user.role]||S.user.role)+(S.user.province_nom?' ('+S.user.province_nom+')':'');
  const cur=location.hash.slice(2).split('?')[0];
  $('#nav').replaceChildren(...MENU.filter(m=>can(m[2])).map(m=>h('a',{href:'#/'+m[0],'aria-current':m[0]===cur?'page':null},m[1])));
}
async function geo(){if(!S.geo){const g=await api('/api/geo');S.geo={provinces:g.provinces||[],sd:g.sous_divisions||[]};}return S.geo;}

/* ---------- composants ---------- */
function load(box,fn){const t=box._t=(box._t||0)+1;box.replaceChildren(h('p',{class:'mut'},'Chargement…'));
  return fn().then(n=>{if(box._t===t)box.replaceChildren(...[n].flat(Infinity).filter(x=>x!=null&&x!==false));},e=>{if(box._t===t&&e.status!==401)box.replaceChildren(errBox(e));});}
function tbl(cols,rows,cap){
  if(!rows.length)return h('p',{class:'empty'},'Aucun résultat.');
  return h('div',{class:'tw'},h('table',{class:'st'},cap&&h('caption',null,cap),h('thead',null,h('tr',null,cols.map(c=>h('th',{scope:'col',class:c.n?'n':null},c.l)))),
    h('tbody',null,rows.map(r=>h('tr',null,cols.map(c=>h('td',{'data-l':c.l,class:c.n?'n':null},c.f?c.f(r):(r[c.k]??'—'))))))));
}
function pager(total,off,lim,go){
  if(total<=lim)return h('span',{class:'mut'},fm(total)+' résultat(s)');
  const p=Math.floor(off/lim)+1,n=Math.ceil(total/lim);
  return h('div',{class:'row'},h('button',{type:'button',class:'btn sec sm',disabled:off<=0,onclick:()=>go(off-lim)},'Précédent'),h('span',null,'Page '+p+' / '+n+' — '+fm(total)+' résultats'),h('button',{type:'button',class:'btn sec sm',disabled:off+lim>=total,onclick:()=>go(off+lim)},'Suivant'));
}
function fld(label,input){const id='f'+Math.random().toString(36).slice(2,8);input.id=id;return h('div',{class:'fld'},h('label',{for:id},label),input);}
function sel(opts,val,onch){const s=h('select',{onchange:e=>onch(e.target.value)},opts.map(([v,l])=>h('option',{value:v,selected:String(v)===String(val)},l)));return s;}
function filters(f,want,geoD,onch,rerender){
  const bar=h('div',{class:'filters',role:'search','aria-label':'Filtres'});
  const set=(k,v)=>{f[k]=v;if(k==='province_id')f.sous_division_id='';onch();};
  if(want.includes('province')&&geoD.provinces.length>0)bar.append(fld('Province',sel([['','Toutes'],...geoD.provinces.map(p=>[p.id,p.nom])],f.province_id,v=>set('province_id',v))));
  if(want.includes('sd'))bar.append(fld('Sous-division',sel([['','Toutes'],...geoD.sd.filter(s=>!f.province_id||String(s.province_id)===String(f.province_id)).map(s=>[s.id,s.nom])],f.sous_division_id,v=>set('sous_division_id',v))));
  if(want.includes('niveau'))bar.append(fld('Niveau',sel([['','Tous'],['primaire','Primaire'],['secondaire','Secondaire']],f.niveau,v=>set('niveau',v))));
  if(want.includes('horizon'))bar.append(fld('Horizon',sel([['6','6 mois'],['12','12 mois'],['24','24 mois']],f.horizon,v=>set('horizon',v))));
  if(want.includes('ecart'))bar.append(fld('Écart',sel([['tous','Tous'],['manque','Manque'],['surplus','Surplus']],f.ecart,v=>set('ecart',v))));
  if(want.includes('statut'))bar.append(fld('Statut',sel([['en_attente','En attente'],['accepte','Acceptées'],['modifie','Modifiées'],['refuse','Refusées'],['tous','Toutes']],f.statut,v=>set('statut',v))));
  return bar;
}
function modal(title,body,o){
  const err=h('div',{role:'alert'});
  const ok=h('button',{type:'submit',class:'btn '+(o.cls||'')},o.ok||'Valider');
  const d=h('dialog',{'aria-labelledby':'mdt'});
  const form=h('form',{method:'dialog',onsubmit:async e=>{e.preventDefault();if(!form.reportValidity())return;ok.disabled=true;err.replaceChildren();
    try{await o.onOk();d.close();}catch(x){if(x.status!==401)err.replaceChildren(errBox(x));ok.disabled=false;}}},
    h('h2',{id:'mdt'},title),body,err,h('div',{class:'row',style:'margin-top:12px;justify-content:flex-end'},h('button',{type:'button',class:'btn sec',onclick:()=>d.close()},'Annuler'),ok));
  d.append(form);d.addEventListener('close',()=>d.remove());document.body.append(d);d.showModal();return d;
}
function kv(o){
  if(o==null)return '—';
  if(typeof o!=='object')return typeof o==='number'?fm(o):typeof o==='boolean'?(o?'oui':'non'):String(o);
  if(Array.isArray(o))return o.every(x=>x==null||typeof x!=='object')?o.join(', '):h('ul',{class:'tight'},o.map(x=>h('li',null,kv(x))));
  return h('div',{class:'tw'},h('table',null,h('tbody',null,Object.entries(o).map(([k,v])=>h('tr',null,h('th',{scope:'row'},k.replace(/_/g,' ')),h('td',null,kv(v)))))));
}
const stBadge=s=>h('span',{class:'badge '+({accepte:'b-ok',refuse:'b-bad',modifie:'b-mod'}[s]||'b-wait')},{accepte:'Acceptée',refuse:'Refusée',modifie:'Modifiée',en_attente:'En attente'}[s]||s);
const ecBadge=e=>h('span',{class:'badge '+(e>0?'b-bad':e<0?'b-mod':'b-ok')},(e>0?'Manque ':e<0?'Surplus ':'Équilibre ')+(e?fm(Math.abs(e)):''));

/* ---------- graphiques SVG ---------- */
function bars(rows,lk,series,label){
  if(!rows.length)return h('p',{class:'empty'},'Aucune donnée.');
  const W=560,LM=96,RM=44,sh=17,rh=series.length*sh+10,H=rows.length*rh+8;
  const vals=rows.flatMap(r=>series.map(s=>+r[s.k]||0));const mn=Math.min(0,...vals),mx=Math.max(0,...vals),rg=(mx-mn)||1;
  const sc=v=>(Math.abs(v)/rg)*(W-LM-RM),x0=LM+(-mn/rg)*(W-LM-RM);
  const g=sv('svg',{viewBox:`0 0 ${W} ${H}`,width:'100%',role:'img','aria-label':label});
  rows.forEach((r,i)=>{const y=i*rh+4;g.append(sv('text',{x:LM-6,y:y+sh,'text-anchor':'end',class:'bar-l'},String(r[lk])));
    series.forEach((s,j)=>{const v=+r[s.k]||0,w=sc(v),yy=y+j*sh;g.append(sv('rect',{x:v<0?x0-w:x0,y:yy,width:Math.max(w,1),height:sh-3,fill:s.c,rx:2}),sv('text',{x:v<0?x0-w-4:x0+w+4,y:yy+sh-5,'text-anchor':v<0?'end':'start',class:'bar-l'},fm(v)));});});
  g.append(sv('line',{x1:x0,x2:x0,y1:0,y2:H,stroke:'#52646b'}));
  return h('div',null,g,h('div',{class:'legend'},series.map(s=>h('span',null,h('i',{style:'background:'+s.c}),s.l))));
}
function mapSvg(items,mode){
  const pts=items.filter(i=>i.lat!=null&&i.lon!=null);
  if(!pts.length)return h('p',{class:'empty'},'Aucune coordonnée disponible.');
  const W=620,H=360,P=46,k=Math.cos(pts.reduce((a,p)=>a+p.lat,0)/pts.length*Math.PI/180);
  const xs=pts.map(p=>p.lon*k),ys=pts.map(p=>-p.lat),mnx=Math.min(...xs),mxx=Math.max(...xs),mny=Math.min(...ys),mxy=Math.max(...ys);
  const sc=Math.min((W-2*P)/((mxx-mnx)||1e9),(H-2*P)/((mxy-mny)||1e9));const s=isFinite(sc)&&sc<1e8?sc:1;
  const ox=(W-(mxx-mnx)*s)/2,oy=(H-(mxy-mny)*s)/2;
  const val=p=>mode==='ecart'?Math.abs(p.ecart||0):(p.departs||0),mv=Math.max(...pts.map(val),1);
  const g=sv('svg',{viewBox:`0 0 ${W} ${H}`,width:'100%',role:'img','aria-label':'Carte schématique des sous-divisions : cercle proportionnel aux '+(mode==='ecart'?'écarts':'départs prévus')+', rouge si manque, vert-bleu si surplus ou équilibre'});
  g.append(sv('rect',{x:0,y:0,width:W,height:H,fill:'#eef4fb',rx:8}));
  pts.forEach((p,i)=>{const x=ox+(p.lon*k-mnx)*s,y=oy+(-p.lat-mny)*s,r=8+20*Math.sqrt(val(p)/mv),bad=(p.ecart||0)>0;
    const c=sv('g',null,sv('title',null,`${p.nom} : ${fm(p.departs)} départs prévus, écart ${fm(p.ecart)}, ${fm(p.manque)} école(s) en manque`),
      sv('circle',{cx:x,cy:y,r,fill:bad?'#b3261e':'#0b4fa8','fill-opacity':.72,stroke:'#fff','stroke-width':2}),
      sv('text',{x,y:y+(i%2?r+13:-r-5),'text-anchor':'middle','font-size':12,fill:'#1c2a30',stroke:'#fff','stroke-width':3,'paint-order':'stroke',class:'bar-l'},p.nom));g.append(c);});
  return h('div',null,g,h('div',{class:'legend'},h('span',null,h('i',{style:'background:#b3261e'}),'Manque d’enseignants (écart > 0)'),h('span',null,h('i',{style:'background:#0b4fa8'}),'Surplus ou équilibre'),h('span',null,'Taille : '+(mode==='ecart'?'écart absolu':'départs prévus'))));
}

/* ---------- vues ---------- */
const V={};
V.login=async main=>{
  let needOtp=false;
  const msg=h('div',{role:'alert'});
  const lg=h('input',{type:'text',autocomplete:'username',required:true}),pw=h('input',{type:'password',autocomplete:'current-password',required:true}),otp=h('input',{type:'text',inputmode:'numeric',autocomplete:'one-time-code',pattern:'[0-9]{6}',maxlength:6});
  const otpF=fld('Code à usage unique (6 chiffres)',otp);otpF.hidden=true;
  const btn=h('button',{type:'submit',class:'btn'},'Se connecter');
  const form=h('form',{class:'card login',onsubmit:async e=>{e.preventDefault();msg.replaceChildren();btn.disabled=true;
    try{const r=await api('/api/login',{method:'POST',noauth:true,body:{login:lg.value.trim(),password:pw.value,...(otp.value?{otp:otp.value.trim()}:{})}});
      S.token=r.token;S.user=r.user;ss.set('pl_tok',r.token);location.hash='#/dashboard';}
    catch(x){if(x.data&&(x.data.otp_required||x.data.error==='otp_required')){needOtp=true;otpF.hidden=false;otp.required=true;otp.focus();msg.replaceChildren(h('div',{class:'okm'},'Ce compte exige un code à usage unique (TOTP). Saisissez le code de votre application.'));}
      else msg.replaceChildren(errBox(x.status===401?new Error('Identifiants ou code incorrects.'):x));}
    btn.disabled=false;}},
    h('img',{class:'login-logo',src:'logo.png',alt:'PLANEDU-IA — Anticiper. Planifier. Agir.'}),h('h1',{tabindex:'-1'},'Connexion'),h('p',{class:'mut'},'Accès réservé aux agents habilités. L’authentification à deux facteurs est obligatoire pour les administrateurs et gestionnaires.'),
    fld('Identifiant',lg),fld('Mot de passe',pw),otpF,msg,btn);
  main.replaceChildren(form);
  try{const d=await (await fetch('/api/demo-info')).json();
    if(d.demo&&d.hint)form.append(h('div',{class:'okm'},'Mode démo — comptes : admin, gest_kc, gest_kw, decideur, chef_e01… ; mot de passe commun : ',h('b',{},d.hint),d.no_otp?' (2FA désactivée en démo).':'.'));
    else if(d.demo&&d.no_otp)form.append(h('div',{class:'okm'},'Mode démo : 2FA désactivée. Le mot de passe est fourni par l’organisateur de la démo.'));}catch(e){}
};

V.dashboard=async(main)=>{
  const f=S.f.dash||(S.f.dash={province_id:'',sous_division_id:'',niveau:'',horizon:'12'});let mode='departs';
  const g=await geo();const out=h('div'),head=h('div');
  const q=()=>qs({province_id:f.province_id,sous_division_id:f.sous_division_id,niveau:f.niveau,horizon:f.horizon});
  const draw=()=>{head.replaceChildren(filters(f,['province','sd','niveau','horizon'],g,refresh));};
  const refresh=()=>{draw();load(out,async()=>{
    const d=await api('/api/dashboard?'+q());const k=d.kpis||{};
    const K=(l,v,bad)=>h('div',{class:'kpi'+(bad?' bad':'')},h('b',null,v),h('span',null,l));
    const psd=d.par_sous_division||[];
    const radios=h('div',{class:'row',role:'radiogroup','aria-label':'Mesure de la carte'},[['departs','Départs prévus'],['ecart','Écart']].map(([v,l])=>h('label',{style:'font-weight:400;display:inline-flex;gap:4px;align-items:center'},h('input',{type:'radio',name:'mm',value:v,checked:v===mode,style:'min-height:0',onchange:()=>{mode=v;mapBox.replaceChildren(mapSvg(psd,mode));}}),l)));
    const mapBox=h('div',null,mapSvg(psd,mode));
    return [
      h('div',{class:'grid kpis'},K('Enseignants',fm(k.enseignants)),K('Départs prévus ('+f.horizon+' mois)',fm(k.departs_prevus)),K('Écart total',fm(k.ecart_total),k.ecart_total>0),K('Écoles en manque',fm(k.ecoles_en_manque),k.ecoles_en_manque>0),K('Qualité des données',k.qualite_donnees_pct==null?'—':fm(k.qualite_donnees_pct)+' %'),K('Recommandations traitées',k.reco_traitees_pct==null?'—':fm(k.reco_traitees_pct)+' %')),
      h('div',{class:'grid two',style:'margin-top:14px'},
        h('section',{class:'card'},h('h2',null,'Carte des sous-divisions'),radios,mapBox),
        h('section',{class:'card'},h('h2',null,'Départs et écarts par niveau'),bars(d.par_niveau||[],'niveau',[{k:'departs',l:'Départs prévus',c:'#0b4fa8'},{k:'ecart',l:'Écart (manque > 0)',c:'#c25800'}],'Départs et écart par niveau'),
          h('h2',{style:'margin-top:16px'},'Départs prévus par horizon'),bars((d.serie_departs||[]).map(r=>({...r,horizon:r.horizon+' mois'})),'horizon',[{k:'retraite',l:'Retraite',c:'#0b4fa8'},{k:'abandon_absence',l:'Abandon / absence prolongée',c:'#0177a1'}],'Départs par horizon')),
      ),
      h('details',{class:'card'},h('summary',null,'Données de la carte (tableau)'),tbl([{l:'Sous-division',k:'nom'},{l:'Départs',n:1,f:r=>fm(r.departs)},{l:'Écart',n:1,f:r=>fm(r.ecart)},{l:'Écoles',n:1,f:r=>fm(r.ecoles)},{l:'En manque',n:1,f:r=>fm(r.manque)}],psd)),
      h('p',{class:'mut'},d.calcul_ms!=null?'Calculé en '+fm(d.calcul_ms)+' ms. ':'',d.banniere||'')];
  });};
  const acts=h('div',{class:'row'},exportBar('dashboard',q),can(['gestionnaire','admin'])&&h('button',{type:'button',class:'btn',onclick:async e=>{e.target.disabled=true;try{const r=await api('/api/calcul',{method:'POST',body:{}});toast('Calcul terminé'+(r.duree_ms!=null?' en '+fm(r.duree_ms)+' ms':'')+'.');refresh();}catch(x){if(x.status!==401)toast(x.message,true);}e.target.disabled=false;}},'Recalculer prévisions et besoins'));
  main.append(h('div',{class:'row sp'},h('h1',{tabindex:'-1'},'Tableau de bord'),acts),h('div',{class:'card'},head),out);refresh();
};

V.previsions=async main=>{
  const f=S.f.prev||(S.f.prev={sous_division_id:'',niveau:'',horizon:'12',off:0});const g=await geo();const out=h('div'),head=h('div');
  const q=()=>qs({horizon:f.horizon,niveau:f.niveau,sous_division_id:f.sous_division_id});
  const refresh=()=>{head.replaceChildren(filters(f,['sd','niveau','horizon'],g,()=>{f.off=0;refresh();}));
    load(out,async()=>{const lim=PAGE(),d=await api('/api/previsions?'+q()+'&limit='+lim+'&offset='+f.off);
      return [h('div',{class:'row sp'},pager(d.total||0,f.off,lim,o=>{f.off=o;refresh();}),exportBar('previsions',q)),
        tbl([{l:'Enseignant',f:r=>nameOf(r)},{l:'Niveau',k:'niveau'},{l:'Matière',k:'matiere'},{l:'École',k:'ecole_nom'},{l:'Retraite',k:'retraite_date'},{l:'P 6 m',n:1,f:r=>fp(r.p6)},{l:'P 12 m',n:1,f:r=>fp(r.p12)},{l:'P 24 m',n:1,f:r=>fp(r.p24)},{l:'P totale',n:1,f:r=>h('b',null,fp(r.probabilite_totale))},
          {l:'Pourquoi',f:r=>h('div',null,(r.raisons||[]).length?h('ul',{class:'tight'},r.raisons.map(x=>h('li',null,x))):'—',(r.facteurs||[]).length?h('div',{class:'mut'},'Facteurs : '+r.facteurs.map(x=>x.facteur+' ('+(x.poids>0?'+':'')+fm(x.poids)+')').join(', ')):null)}],d.items||[],'Prévisions de départs (tri par probabilité décroissante)')];});};
  main.append(h('h1',{tabindex:'-1'},'Prévisions de départs'),h('p',{class:'mut'},'Les probabilités sont des estimations statistiques d’aide à la décision. Les noms ne sont visibles que des gestionnaires habilités ; sinon un pseudonyme est affiché.'),h('div',{class:'card'},head),out);refresh();
};

V.besoins=async main=>{
  const f=S.f.bes||(S.f.bes={sous_division_id:'',niveau:'',ecart:'tous',off:0});const g=await geo();const out=h('div'),head=h('div');
  const q=()=>qs({sous_division_id:f.sous_division_id,niveau:f.niveau,ecart:f.ecart});
  const refresh=()=>{head.replaceChildren(filters(f,['sd','niveau','ecart'],g,()=>{f.off=0;refresh();}));
    load(out,async()=>{const lim=PAGE(),d=await api('/api/besoins?'+q()),all=arr(d,'items'),page=all.slice(f.off,f.off+lim);
      return [h('div',{class:'row sp'},pager(all.length,f.off,lim,o=>{f.off=o;refresh();}),exportBar('besoins',q)),
        tbl([{l:'École',k:'ecole_nom'},{l:'Sous-division',k:'sous_division'},{l:'Niveau',k:'niveau'},{l:'Matière',k:'matiere'},{l:'Requis',n:1,f:r=>fm(r.requis)},{l:'Actuel',n:1,f:r=>fm(r.actuel)},{l:'Prévu',n:1,f:r=>fm(r.prevu)},{l:'Écart',n:1,f:r=>ecBadge(r.ecart)}],page,'Besoins en enseignants et écarts (écart > 0 : manque)')];});};
  main.append(h('h1',{tabindex:'-1'},'Besoins et écarts'),h('p',{class:'mut'},'Requis = élèves ÷ norme élèves/enseignant. Prévu = effectif actuel moins les départs prévus.'),h('div',{class:'card'},head),out);refresh();
};

V.redeploiements=async main=>{
  const f=S.f.red||(S.f.red={statut:'en_attente',off:0});const out=h('div'),head=h('div');
  const q=()=>qs({statut:f.statut});
  const refresh=()=>{head.replaceChildren(filters(f,['statut'],null,()=>{f.off=0;refresh();}));
    load(out,async()=>{const lim=PAGE(),d=await api('/api/recommandations?'+q()+'&limit='+lim+'&offset='+f.off),items=d.items||[];
      return [h('div',{class:'row sp'},pager(d.total||0,f.off,lim,o=>{f.off=o;refresh();}),exportBar('recommandations',q)),
        items.length?items.map(r=>reco(r,refresh)):h('p',{class:'empty'},'Aucune recommandation pour ce filtre. Lancez un calcul depuis le tableau de bord.')];});};
  main.append(h('h1',{tabindex:'-1'},'Redéploiements recommandés'),h('p',{class:'mut'},'Propositions d’aide à la décision. Toute décision exige un motif, est journalisée, et ne modifie ni la paie ni la carrière.'),h('div',{class:'card'},head),out);refresh();
};
function reco(r,refresh){
  const d=r.decision;
  return h('article',{class:'card reco','aria-label':'Recommandation '+r.rang},
    h('div',{class:'row sp'},h('h2',{style:'margin:0'},'Proposition n° '+r.rang+' — '+nameOf(r)),stBadge(r.statut)),
    h('p',{class:'mut'},[r.matiere,r.niveau].filter(Boolean).join(' · ')+' · gain d’écart : '+fm(r.gain_ecart)+' · distance : '+fm(r.km)+' km'),
    h('div',{class:'route'},h('div',{class:'sch'},h('small',null,'École de départ (surplus)'),r.ecole_depart||'—'),
      h('div',{class:'arr'},fm(r.km)+' km',sv('svg',{viewBox:'0 0 90 14','aria-hidden':'true'},sv('line',{x1:0,y1:7,x2:80,y2:7,stroke:'#0b4fa8','stroke-width':2}),sv('path',{d:'M78 2 L89 7 L78 12 Z',fill:'#0b4fa8'}))),
      h('div',{class:'sch'},h('small',null,'École d’arrivée (manque)'),r.ecole_arrivee||'—')),
    (r.raisons||[]).length?h('ul',{class:'tight'},r.raisons.map(x=>h('li',null,x))):null,
    (r.contraintes_respectees||[]).length?h('div',{class:'row'},r.contraintes_respectees.map(c=>h('span',{class:'badge b-ok'},'✓ '+c))):null,
    d?h('p',{class:'mut'},'Décision : ',h('b',null,d.choix),' par ',d.utilisateur||'—',' le ',d.date||'—',' — motif : ',d.motif||'—'):null,
    r.statut==='en_attente'&&can(['gestionnaire'])?h('div',{class:'row',style:'margin-top:8px'},
      h('button',{type:'button',class:'btn',onclick:()=>decide(r,'accepte',refresh)},'Accepter'),
      h('button',{type:'button',class:'btn sec',onclick:()=>decide(r,'modifie',refresh)},'Modifier'),
      h('button',{type:'button',class:'btn bad',onclick:()=>decide(r,'refuse',refresh)},'Refuser')):null);
}
async function decide(r,choix,refresh){
  const L={accepte:'Accepter',modifie:'Modifier la destination',refuse:'Refuser'}[choix];
  const motif=h('textarea',{required:true,maxlength:500,'aria-describedby':'mh'});let ecole=null;const body=h('div',null,h('p',null,nameOf(r)+' : '+(r.ecole_depart||'—')+' → '+(r.ecole_arrivee||'—')));
  if(choix==='modifie'){
    ecole=h('select',{required:true},h('option',{value:''},'Chargement des écoles…'));
    body.append(fld('Nouvelle école d’arrivée (écoles en manque)',ecole));
    api('/api/besoins?ecart=manque').then(b=>{const it=arr(b,'items');const seen=new Map();
      it.filter(x=>(!r.niveau||x.niveau===r.niveau)&&(!r.matiere||x.matiere===r.matiere)).forEach(x=>{if(!seen.has(x.ecole_id))seen.set(x.ecole_id,x.ecole_nom);});
      if(!seen.size)it.forEach(x=>{if(!seen.has(x.ecole_id))seen.set(x.ecole_id,x.ecole_nom);});
      ecole.replaceChildren(h('option',{value:''},'— Choisir —'),...[...seen].map(([id,n])=>h('option',{value:id},n)));},e=>ecole.replaceChildren(h('option',{value:''},'Liste indisponible : '+e.message)));
  }
  body.append(fld('Motif (obligatoire)',motif),h('p',{id:'mh',class:'mut'},'Le motif est conservé dans le journal d’audit.'));
  modal(L,body,{ok:L,cls:choix==='refuse'?'bad':'',onOk:async()=>{
    if(!motif.value.trim())throw new Error('Le motif est obligatoire.');
    const b={choix,motif:motif.value.trim()};if(choix==='modifie'){if(!ecole.value)throw new Error('Choisissez une école d’arrivée.');b.ecole_arrivee_id=isNaN(+ecole.value)?ecole.value:+ecole.value;}
    try{await api('/api/recommandations/'+r.id+'/decision',{method:'POST',body:b});}catch(e){if(e.status===409){toast('Cette recommandation a déjà été décidée.',true);refresh();throw new Error('Déjà décidée par un autre utilisateur.');}throw e;}
    toast('Décision enregistrée.');refresh();}});
}

const b64=async f=>{const u=new Uint8Array(await f.arrayBuffer());let s='';for(let i=0;i<u.length;i+=8192)s+=String.fromCharCode.apply(null,u.subarray(i,i+8192));return btoa(s);};
V.donnees=async main=>{
  const KINDS=[['enseignants','Enseignants'],['ecoles','Écoles'],['effectifs','Effectifs élèves']];
  const rep=h('div'),file=h('input',{type:'file',accept:'.csv,.xlsx',required:true}),kind=h('select',null,KINDS.map(([v,l])=>h('option',{value:v},l)));
  const form=h('form',{class:'card',onsubmit:async e=>{e.preventDefault();const fl=file.files[0];if(!fl)return;if(fl.size>8e6){rep.replaceChildren(errBox(new Error('Fichier trop volumineux (max 8 Mo).')));return;}
    const btn=e.submitter;btn.disabled=true;rep.replaceChildren(h('p',{class:'mut'},'Import en cours…'));
    try{const r=await api('/api/import',{method:'POST',body:{kind:kind.value,filename:fl.name,content_base64:await b64(fl)}});const rap=r.rapport||[];
      rep.replaceChildren(h('div',{class:r.rejetes?'err':'okm',role:'status'},fm(r.importes)+' ligne(s) importée(s), '+fm(r.rejetes)+' rejetée(s).'),rap.length?tbl([{l:'Ligne',n:1,k:'ligne'},{l:'Champ',k:'champ'},{l:'Erreur',k:'erreur'}],rap.slice(0,200),'Rapport d’erreurs'+(rap.length>200?' (200 premières lignes sur '+rap.length+')':'')):null);
    }catch(x){if(x.status!==401)rep.replaceChildren(errBox(x));}btn.disabled=false;}},
    h('h2',null,'Importer un fichier CSV ou XLSX'),h('p',{class:'mut'},'Le matricule et le nom sont pseudonymisés dès l’import. Toute colonne hors liste blanche (données biométriques notamment) est rejetée.'),
    h('div',{class:'filters'},fld('Type de données',kind),fld('Fichier',file),h('button',{type:'submit',class:'btn'},'Importer')),rep);
  const tpl=h('section',{class:'card'},h('h2',null,'Modèles de fichiers CSV'),h('div',{class:'row'},KINDS.map(([v,l])=>h('button',{type:'button',class:'btn sec',onclick:async()=>{try{const r=await api('/api/import/template/'+v,{raw:true});saveBlob(await r.blob(),'modele_'+v+'.csv');}catch(x){if(x.status!==401)toast(x.message,true);}}},'Modèle '+l))),
    h('p',{class:'mut'},'Enseignants : matricule, nom, date_naissance (AAAA-MM-JJ), date_entree, niveau, matiere, ecole_id, statut. Écoles : nom, sous_division_id, niveau, lat, lon. Effectifs : ecole_id, annee, niveau, nombre.'));
  main.append(h('h1',{tabindex:'-1'},'Données'),form,tpl);
};

V.modeles=async main=>{
  const out=h('div');main.append(h('h1',{tabindex:'-1'},'Fiches de performance des modèles'),h('p',{class:'mut'},'Chaque fiche décrit la méthode, les données, les métriques de rétro-test et les écarts par province et par niveau, comparés à une base de référence simple.'),out);
  load(out,async()=>{const rs=await Promise.all([6,12,24].map(hz=>api('/api/modeles/performance?horizon='+hz)));
    return rs.map((r,i)=>{const hz=[6,12,24][i];const x=Array.isArray(r)?(r.find(y=>+y.horizon===hz)||r[0]):(r.items?r.items[0]:r.fiches?r.fiches[0]:r);if(!x)return h('p',{class:'empty'},'Aucune fiche pour '+hz+' mois.');
      return h('section',{class:'card'},h('h2',null,'Horizon '+(x.horizon||hz)+' mois — '+(x.methode||'')),
        x.avertissement?h('div',{class:'okm',style:'background:var(--amb-l);border-color:#e0b23a;color:#4d3000'},x.avertissement):null,
        h('p',{class:'mut'},'Données : '+kv(x.donnees)+' · Date : '+(x.date||'—')+' · Version : '+(x.version_modele||'—')),
        h('div',{class:'grid two'},h('div',null,h('h3',null,'Métriques'),kv(x.metriques)),h('div',null,h('h3',null,'Base de référence'),kv(x.baseline))),
        h('div',{class:'grid two'},h('div',null,h('h3',null,'Erreur par province'),kv(x.erreur_par_province)),h('div',null,h('h3',null,'Erreur par niveau'),kv(x.erreur_par_niveau))));});});
};

V.alertes=async main=>{
  const out=h('div');
  const prev=h('button',{type:'button',class:'btn',onclick:async e=>{e.target.disabled=true;load(out,async()=>{const d=await api('/api/alertes/preview',{method:'POST',body:{}});const it=arr(d,'items');e.target.disabled=false;
    return [h('div',{class:'row sp'},h('span',{class:'mut'},it.length+' note(s) prête(s). Canal de diffusion à confirmer ; aucun envoi réel dans ce prototype.'),it.length?h('button',{type:'button',class:'btn sec',onclick:()=>modal('Envoi simulé',h('p',null,'Les '+it.length+' notes seront journalisées sans aucun envoi réel (simulation). Continuer ?'),{ok:'Simuler l’envoi',onOk:async()=>{const r=await api('/api/alertes/envoyer',{method:'POST',body:{}});toast('Envoi simulé et journalisé'+(r.envoyes!=null?' ('+r.envoyes+')':'')+'.');}})},'Simuler l’envoi'):null),
      it.length?tbl([{l:'École',f:r=>r.ecole_nom||r.ecole||r.ecole_id},{l:'Note (≤ 320 caractères)',f:r=>r.message||r.note||r.texte||'—'},{l:'Car.',n:1,f:r=>(r.message||r.note||r.texte||'').length}],it):h('p',{class:'empty'},'Aucune alerte à émettre.')];}).then(()=>{e.target.disabled=false;});}},'Générer l’aperçu des alertes');
  main.append(h('h1',{tabindex:'-1'},'Alertes aux écoles'),h('p',{class:'mut'},'Notes courtes de type SMS signalant les départs prévus et les besoins. Fonction optionnelle : envoi simulé uniquement.'),h('div',{class:'card'},prev),out);
};

V.admin=async main=>{
  const TABS=[['users','Utilisateurs'],['audit','Journal d’audit'],['backup','Sauvegarde / restauration'],['rbac','Test RBAC'],['normes','Normes et paramètres']];
  let cur=S.f.adm||'users';const panel=h('div');const tabs=h('div',{class:'tabs',role:'tablist'});
  const show=k=>{cur=S.f.adm=k;tabs.querySelectorAll('button').forEach(b=>b.setAttribute('aria-selected',b.dataset.k===k));panel.replaceChildren();AD[k](panel);};
  TABS.forEach(([k,l])=>tabs.append(h('button',{type:'button',role:'tab','data-k':k,onclick:()=>show(k)},l)));
  main.append(h('h1',{tabindex:'-1'},'Administration'),tabs,panel);show(cur);
};
const AD={
  async users(p){const list=h('div'),note=h('div');const g=await geo().catch(()=>({provinces:[]}));
    const lg=h('input',{required:true,pattern:'[A-Za-z0-9_.\\-]{3,40}'}),rl=sel(Object.entries(ROLES).map(([v,l])=>[v,l]),'gestionnaire',()=>{}),pv=sel([['','—'],...g.provinces.map(x=>[x.id,x.nom])],'',()=>{}),ec=h('input',{type:'number',min:1});
    const refresh=()=>load(list,async()=>{const d=await api('/api/admin/users');return tbl([{l:'Identifiant',k:'login'},{l:'Rôle',f:u=>ROLES[u.role]||u.role},{l:'Province',f:u=>u.province_nom||u.province_id||'—'},{l:'École',f:u=>u.ecole_id||'—'},{l:'Actif',f:u=>u.actif?h('span',{class:'badge b-ok'},'Actif'):h('span',{class:'badge b-bad'},'Désactivé')},
      {l:'Action',f:u=>h('button',{type:'button',class:'btn sec sm','aria-label':(u.actif?'Désactiver ':'Activer ')+u.login,onclick:async()=>{try{await api('/api/admin/users/'+u.id,{method:'PATCH',body:{actif:!u.actif}});refresh();}catch(x){if(x.status!==401)toast(x.message,true);}}},u.actif?'Désactiver':'Activer')}],arr(d,'users'),'Comptes utilisateurs');});
    const form=h('form',{class:'card',onsubmit:async e=>{e.preventDefault();note.replaceChildren();
      try{const b={login:lg.value.trim(),role:rl.value};if(pv.value)b.province_id=+pv.value;if(ec.value)b.ecole_id=+ec.value;const r=await api('/api/admin/users',{method:'POST',body:b});
        const {user,...secrets}=r;note.replaceChildren(h('div',{class:'okm',role:'status'},h('b',null,'Compte créé. À noter maintenant, ces secrets ne seront plus affichés :'),kv(secrets)));lg.value='';refresh();}
      catch(x){if(x.status!==401)note.replaceChildren(errBox(x));}}},h('h2',null,'Créer un compte'),
      h('div',{class:'filters'},fld('Identifiant',lg),fld('Rôle',rl),fld('Province (gestionnaire)',pv),fld('École n° (chef)',ec),h('button',{class:'btn',type:'submit'},'Créer')),note);
    p.append(form,list);refresh();},
  async audit(p){let off=0;const out=h('div');const refresh=()=>load(out,async()=>{const lim=PAGE(),d=await api('/api/admin/audit?limit='+lim+'&offset='+off);const it=arr(d,'items');
    return [h('div',{class:'row sp'},h('span',null,'Chaîne de hachage SHA-256 : ',d.chain_ok===true?h('span',{class:'badge b-ok'},'✓ Intègre'):d.chain_ok===false?h('span',{class:'badge b-bad'},'✗ ROMPUE'):h('span',{class:'badge b-wait'},'Non vérifiée')),pager(d.total||it.length,off,lim,o=>{off=o;refresh();})),
      tbl([{l:'N°',n:1,k:'id'},{l:'Date',k:'date'},{l:'Utilisateur',k:'utilisateur'},{l:'Action',k:'action'},{l:'Objet',k:'objet'},{l:'Empreinte',f:r=>h('code',null,String(r.hash||'').slice(0,12))}],it,'Journal d’audit (append-only)')];});refresh();p.append(out);},
  backup(p){const out=h('div'),fi=h('input',{required:true,placeholder:'ex. planedu_20260101.bak'});
    p.append(h('section',{class:'card'},h('h2',null,'Sauvegarde chiffrée'),h('button',{type:'button',class:'btn',onclick:async e=>{e.target.disabled=true;try{const r=await api('/api/admin/backup',{method:'POST',body:{}});if(r.fichier)fi.value=r.fichier;out.replaceChildren(h('div',{class:'okm',role:'status'},'Sauvegarde réalisée.'),kv(r));}catch(x){if(x.status!==401)out.replaceChildren(errBox(x));}e.target.disabled=false;}},'Créer une sauvegarde'),out),
      h('form',{class:'card',onsubmit:e=>{e.preventDefault();modal('Confirmer la restauration',h('p',null,'La base actuelle sera remplacée par « '+fi.value+' ». Cette opération est journalisée. Continuer ?'),{ok:'Restaurer',cls:'bad',onOk:async()=>{const r=await api('/api/admin/restore',{method:'POST',body:{fichier:fi.value.trim()}});out.replaceChildren(h('div',{class:'okm',role:'status'},'Restauration effectuée.'),kv(r));toast('Restauration effectuée.');}});}},
      h('h2',null,'Restauration'),h('div',{class:'filters'},fld('Fichier de sauvegarde',fi),h('button',{class:'btn sec',type:'submit'},'Restaurer…'))));},
  rbac(p){const out=h('div');p.append(h('section',{class:'card'},h('h2',null,'Test négatif d’accès inter-province'),h('p',{class:'mut'},'Vérifie qu’un gestionnaire ne peut pas accéder aux données d’une autre province (attendu : refus 403).'),h('button',{type:'button',class:'btn',onclick:e=>{e.target.disabled=true;load(out,async()=>{const r=await api('/api/admin/rbac-test');const ok=r.ok??r.passed??r.succes;e.target.disabled=false;return [ok===undefined?null:h('p',null,h('span',{class:'badge '+(ok?'b-ok':'b-bad')},ok?'✓ Test réussi':'✗ Test en échec')),kv(r)];}).then(()=>{e.target.disabled=false;});}},'Exécuter le test'),out));},
  async normes(p){const out=h("div");p.append(out);load(out,async()=>{const [n,pa]=await Promise.all([api('/api/normes'),api('/api/parametres')]);let rows=arr(n,'normes').map(x=>({...x}));
    const inp=(o,k,t)=>h('input',{type:t||'number',value:o[k]??'',min:t?null:0,step:k==='eleves_par_enseignant'?'any':1,'aria-label':k,onchange:e=>{o[k]=t?e.target.value:+e.target.value;}});
    const pm={age_legal_retraite:pa.age_legal_retraite,max_km:pa.max_km,annee_cible:pa.annee_cible};const PL={age_legal_retraite:'Âge légal de retraite',max_km:'Distance maximale de redéploiement (km)',annee_cible:'Année cible'};
    const msg=h('div',{role:'status'});
    return [h('form',{class:'card',onsubmit:async e=>{e.preventDefault();try{await api('/api/parametres',{method:'PUT',body:pm});msg.replaceChildren(h('div',{class:'okm'},'Paramètres enregistrés. Relancez le calcul pour en voir l’effet.'));}catch(x){if(x.status!==401)msg.replaceChildren(errBox(x));}}},h('h2',null,'Paramètres'),h('div',{class:'filters'},Object.keys(PL).map(k=>fld(PL[k],h('input',{type:'number',value:pm[k]??'',required:true,onchange:e=>{pm[k]=+e.target.value;}}))),h('button',{class:'btn',type:'submit'},'Enregistrer')),msg),
      h('form',{class:'card',onsubmit:async e=>{e.preventDefault();try{await api('/api/normes',{method:'PUT',body:rows});msg2.replaceChildren(h('div',{class:'okm'},'Normes enregistrées. Relancez le calcul pour en voir l’effet.'));}catch(x){if(x.status!==401)msg2.replaceChildren(errBox(x));}}},h('h2',null,'Normes élèves par enseignant'),
        tbl([{l:'Niveau',k:'niveau'},{l:'Matière',f:r=>r.matiere||'(toutes)'},{l:'Élèves / enseignant',f:r=>inp(r,'eleves_par_enseignant')},{l:'En vigueur depuis',f:r=>inp(r,'en_vigueur_depuis')}],rows),h('div',{class:'row',style:'margin-top:10px'},h('button',{class:'btn',type:'submit'},'Enregistrer les normes')),msg2=h('div',{role:'status'}))];
    });}
};
let msg2;

/* ---------- routeur ---------- */
async function route(){
  const rid=++S.rid;let name=location.hash.slice(2).split('?')[0];const main=$('#main');
  if(!S.token){if(name!=='login'){location.hash='#/login';return;}S.user=null;}
  else if(!S.user){try{const m=await api('/api/me');S.user=m.user||m;}catch(e){if(e.status!==401){main.replaceChildren(errBox(e));}return;}if(rid!==S.rid)return;}
  if(S.token&&(!name||name==='login')){location.hash='#/dashboard';return;}
  chrome();
  const def=MENU.find(m=>m[0]===name);
  main.replaceChildren();
  if(name!=='login'&&(!def||!can(def[2]))){main.append(h('h1',{tabindex:'-1'},'Page non disponible'),h('div',{class:'err'},def?'Votre rôle ne donne pas accès à cet écran.':'Écran introuvable.'),h('a',{href:'#/dashboard'},'Retour au tableau de bord'));return;}
  try{await V[name](main);}catch(e){if(e.status!==401&&rid===S.rid)main.append(errBox(e));}
  if(rid!==S.rid)return;
  const t=def?def[1]:'Connexion';document.title=t+' — PLANEDU-IA';say(t);const h1=main.querySelector('h1');if(h1)h1.focus({preventScroll:true});
}
window.addEventListener('hashchange',route);
$('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:{}});}catch(_){}endSession('Vous êtes déconnecté.');});
const lite=$('#lite');lite.checked=S.lite;document.body.classList.toggle('light',S.lite);
lite.addEventListener('change',()=>{S.lite=lite.checked;ls.set('pl_lite',S.lite?'1':'0');document.body.classList.toggle('light',S.lite);route();});
route();
})();
