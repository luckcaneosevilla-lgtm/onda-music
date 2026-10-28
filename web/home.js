// "Para ti": Discover before any search. The listener's own shelves (continue
// listening, daily mixes, their artists) come from the library already in memory
// and draw instantly; recommendations and the country's chart fill in when they
// arrive. Mixes and the recommendation seed rotate once a day.
const homeItems=new Map();
let homeCharts=null,homeChartsPromise=null,homeChartsFailed=false,homeRecs=null,homeRecsFor=null,homeShelves={};
window.homeLookup=id=>homeItems.get(id);
const today=Math.floor((Date.now()-new Date().getTimezoneOffset()*60000)/86400000);
// Deterministic per day, so the page does not reshuffle on every visit.
function daily(list,salt=0){let seed=today*9301+salt*49297+233280;return list.map(v=>[v,(seed=(seed*9301+49297)%233280)/233280]).sort((a,b)=>a[1]-b[1]).map(x=>x[0])}
const mainArtist=v=>(v.artists?.[0]||String(v.channel||'').split(/,| & | x | feat\.? /i)[0]).trim();

function homeData(){
  const playable=v=>v&&v.available!==false&&v.provider!=='local';
  const saves=onlineData.saves.filter(s=>playable(onlineData.items[s.video_id]));
  const recent=saves.filter(s=>s.last_played).sort((a,b)=>b.last_played-a.last_played).map(s=>onlineData.items[s.video_id]);
  const favorites=saves.filter(s=>s.favorite).map(s=>onlineData.items[s.video_id]);
  // Artist weight: favourites count most, then plays, with a bonus for recent ones.
  const weights=new Map(),songs=new Map(),now=Date.now()/1000;
  for(const s of saves){
    const v=onlineData.items[s.video_id],name=mainArtist(v);if(!name)continue;
    const w=(s.favorite?3:0)+(s.later?1:0)+(s.last_played?1+Math.max(0,2-(now-s.last_played)/604800):0);
    weights.set(name,(weights.get(name)||0)+w);if(!songs.has(name))songs.set(name,[]);songs.get(name).push(v);
  }
  const artists=[...weights].sort((a,b)=>b[1]-a[1]).map(([name])=>({name,songs:songs.get(name)}));
  const mixes=daily(artists.slice(0,6),1).slice(0,4).map(a=>({title:'Mix de '+a.name,subtitle:'Tus favoritas y música parecida',songs:a.songs,covers:[...a.songs,...daily(favorites,2)].map(v=>v.thumbnail).filter(Boolean)}));
  if(favorites.length>=5)mixes.push({title:'Mix de favoritos',subtitle:`${favorites.length} canciones que te gustan`,songs:favorites,covers:daily(favorites,3).map(v=>v.thumbnail).filter(Boolean)});
  const seeds=[...favorites,...recent].filter(v=>v.provider==='piped');
  return {recent:recent.slice(0,12),mixes,artists:artists.slice(0,10),seed:seeds.length?daily(seeds,4)[0]:null};
}

function drawHome(el){
  const data=homeData(),hour=new Date().getHours(),first=String(me?.name||'').split(' ')[0];
  const greeting=(hour<6?'Buenas noches':hour<13?'Buenos días':hour<20?'Buenas tardes':'Buenas noches')+(first?', '+first:'');
  homeShelves={recent:data.recent,recs:homeRecsFor===data.seed?.id?homeRecs||[]:[],charts:homeCharts?.songs||[]};
  for(const list of Object.values(homeShelves))for(const v of list)homeItems.set(v.id,v);
  const card=(v,shelf,i,badge='')=>`<button class="home-card" data-video="${esc(v.id)}" data-home-play="${shelf}:${i}" aria-label="Reproducir ${esc(v.title)} de ${esc(v.channel||'')}"><span class="home-art">${v.thumbnail?`<img src="${esc(v.thumbnail)}" alt="" loading="lazy">`:'<span aria-hidden="true">♫</span>'}${badge}<span class="home-play" aria-hidden="true">▶</span></span><strong>${esc(v.title)}</strong><small>${esc(v.channel||'')}</small></button>`;
  const shelf=(title,body,note='')=>body?`<section class="home-section"><div class="home-head"><h2>${title}</h2>${note}</div><div class="shelf">${body}</div></section>`:'';
  const skeleton=n=>Array.from({length:n},()=>'<span class="home-card skeleton-card" aria-hidden="true"><span class="home-art"></span><strong></strong><small></small></span>').join('');
  const mixCard=(m,i)=>`<button class="home-card mix-card" data-home-mix="${i}" aria-label="Reproducir ${esc(m.title)}"><span class="home-art mix-art">${[0,1,2,3].map(k=>m.covers[k%Math.max(1,m.covers.length)]?`<img src="${esc(m.covers[k%m.covers.length])}" alt="" loading="lazy">`:'<span></span>').join('')}<span class="home-play" aria-hidden="true">▶</span></span><strong>${esc(m.title)}</strong><small>${esc(m.subtitle)}</small></button>`;
  const recs=homeShelves.recs,charts=homeCharts;
  el.innerHTML=`<div class="home"><h2 class="home-greeting">${esc(greeting)}</h2>
    ${shelf('Seguir escuchando',data.recent.map((v,i)=>card(v,'recent',i)).join(''))}
    ${shelf('Tus mixes del día',data.mixes.map(mixCard).join(''),'<small class="muted">Se renuevan cada día</small>')}
    ${data.seed?shelf('Recomendado para ti',recs.length?recs.map((v,i)=>card(v,'recs',i)).join(''):homeRecs===null?skeleton(5):'',`<small class="muted">Porque escuchas ${esc(mainArtist(data.seed))}</small>`):''}
    ${shelf(charts?`Lo más escuchado hoy en ${esc(charts.country)}`:'Lo más escuchado hoy',charts?charts.songs.slice(0,20).map((v,i)=>card(v,'charts',i,`<span class="home-rank">${i+1}</span>`)).join(''):homeChartsFailed?'':skeleton(5),charts?'<button class="text-link" data-home-chart>▶ Reproducir el top</button>':'')}
    ${data.artists.length?shelf('Tus artistas',data.artists.map(a=>`<button class="home-artist" data-home-artist="${esc(a.name)}">${a.songs[0]?.thumbnail?`<img src="${esc(a.songs[0].thumbnail)}" alt="" loading="lazy">`:'<span aria-hidden="true">♫</span>'}<strong>${esc(a.name)}</strong></button>`).join('')):''}
    ${charts?.artists?.length?shelf('Artistas del momento',charts.artists.map(a=>`<a class="home-artist" href="/artist/${esc(a.id)}" data-artist-open>${a.thumbnail?`<img src="${esc(bigArt(a.thumbnail))}" alt="" loading="lazy">`:'<span aria-hidden="true">♫</span>'}<strong>${esc(a.name)}</strong></a>`).join('')):''}
  </div>`;
  el.querySelectorAll('[data-home-play]').forEach(b=>b.onclick=()=>{const [name,i]=b.dataset.homePlay.split(':');const list=homeShelves[name];if(list?.[i])playList(list,Number(i))});
  el.querySelectorAll('[data-home-mix]').forEach(b=>b.onclick=()=>{const m=data.mixes[b.dataset.homeMix];playList(m.songs,0,true);notify(m.title+': cuando terminen tus canciones sigue con música parecida.')});
  el.querySelectorAll('[data-home-artist]').forEach(b=>b.onclick=()=>{const name=b.dataset.homeArtist;$('#online-query').value=name;runSearch(name,false,'ytmusic','songs')});
  el.querySelector('[data-home-chart]')?.addEventListener('click',()=>playList(homeShelves.charts,0));
  el.querySelectorAll('[data-artist-open]').forEach(openLink);
  // Songs on screen are prepared before the tap, like search results.
  el.querySelectorAll('.home-card[data-video]').forEach(c=>visiblePrefetch.observe(c));
  loadHome(data.seed);
}
// The two network shelves; each redraws the home once, only if it is still showing.
function loadHome(seed){
  const redraw=()=>{const el=$('#search-results');if(el&&location.pathname==='/'&&!searchView.query&&el.querySelector('.home'))drawHome(el)};
  if(!homeCharts&&!homeChartsPromise)homeChartsPromise=api('/api/online/charts').then(r=>{homeCharts=r;redraw()},()=>{homeChartsFailed=true}).finally(()=>{homeChartsPromise=null});
  if(seed&&homeRecsFor!==seed.id){homeRecsFor=seed.id;homeRecs=null;api('/api/online/radio/'+seed.id).then(r=>{if(homeRecsFor!==seed.id)return;homeRecs=r.items.filter(v=>v.available!==false).slice(0,12);redraw()},()=>{homeRecs=[];homeRecsFor=null})}
}
