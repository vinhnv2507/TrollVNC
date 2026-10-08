const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'Shopee-Live-ControlIOS.js'),'utf8').replace('AUTO_RUN: true','AUTO_RUN: false');
function engine(overrides={}) {
    const events=[];
    let millis=0;
    const c=vm.createContext({log:s=>events.push(['log',s]),now:()=>millis,
        sleep:s=>{events.push(['sleep',s]);millis+=s*1000},
        tap:(x,y)=>events.push(['tap',x,y]),swipe:(...p)=>events.push(['swipe',...p]),
        killApp:()=>{},openURL:()=>{},findText:()=>null,ocr:()=>'',
        getColor:()=> '885B60',matchColor:()=>false,...overrides});
    new vm.Script(source).runInContext(c);return{c,events,advance:ms=>millis+=ms,time:()=>millis};
}
for(const [headerY,captionY,claimY] of [[.13,.159,.193],[.197,.300,.332]]) {
    const e=engine({findText:(word,x1,y1,x2,y2)=>{
        const p=word==='thuong'?{x:.82,y:headerY}:word==='xem'?{x:.81,y:captionY}:
                word==='nhân'?{x:.89,y:claimY}:null;
        return p&&p.x>=x1&&p.x<=x2&&p.y>=y1&&p.y<=y2?p:null;
    },ocr:(x1,y1,x2,y2)=>y1<captionY&&y2>captionY?'Xem 00:29 de nhân thuong':''});
    assert.equal(e.c.readTimeMMSS(),29);
    const claim=e.c.clickBottomClaim();assert.ok(claim);assert.equal(claim.y,claimY);
    const r=e.c.timerRegion();assert.ok(r[1]<captionY&&r[3]>captionY);
    assert.equal(e.c.readyButton(claim),false,'Disabled reward must never be tapped');
}
const base=engine();
const multiTimer=engine({findText:(word,x1,y1,x2,y2)=>{
    if(word==='phan')return{x:.82,y:.13};
    if(word==='xem')return[.159,.230].map(y=>({x:.81,y})).find(p=>p.y>=y1&&p.y<=y2)||null;
    return null;
},ocr:(x1,y1,x2,y2)=>y1<.230&&y2>.230?'Xem 05:17 de nhan':'Xem 00:29'});
assert.equal(multiTimer.c.readTimeMMSS(),317,'Read the timer for the lowest claim button');
assert.ok(multiTimer.c.claimRegion()[1]>.13,'Claim lookup starts below the reward heading');
for(const [s,value] of [['00:00',0],['Xem 00:29 để nhận',29],['01.02\n02;09',129],
                      ['Xem 00: 29 de RAJA PRIN',29],['Xem 00. 29 de nhan',29],
                      ['Xem 00/29 04 nhJa.tta',29],['Xe= 00 10 25 PNA',10],['44 22',null],
                      ['12:60',null],['123:45',null],['no text',null],['8：15',495]])
    assert.equal(base.c.parseTimeMMSS(s),value);
for(const color of ['EA5438','FE5C3A','EE6B4B']) assert.equal(base.c.warmButtonColor(color),true);
for(const color of ['FFFFFF','885B60','B78080','000000','','EA5438oops']) assert.equal(base.c.warmButtonColor(color),false);
const ready=engine({getColor:()=> 'FE5C3A'});assert.equal(ready.c.readyButton({x:.89,y:.193}),true);
let sample=0;const nativeReady=engine({getColor:()=>['DA3410','D93511','D42F0B','F5522F','F6522F'][sample++]});
assert.equal(nativeReady.c.readyButton({x:.886,y:.262}),true,'Actual enabled reward background on device 133');
let whiteSample=0;const glyphOnly=engine({getColor:()=>['FFFFFF','FFFFFF','FFFFFF','FE5C3A','2F4D85'][whiteSample++]});
assert.equal(glyphOnly.c.readyButton({x:.886,y:.262}),false,'One warm sample and white glyph are insufficient');
const popup=engine({getColor:(x,y)=>Math.abs(x-.5)>.1?'FFFFFF':'FE5C3A'});
assert.equal(popup.c.popupWhiteCard({x:.5,y:.71}),true);
assert.equal(popup.c.readyButton({x:.5,y:.71}),true);
const badPopup=engine({getColor:()=> '885B60'});assert.equal(badPopup.c.popupWhiteCard({x:.5,y:.71}),false);
const missing=engine();for(let n=0;n<25;n++)missing.c.handleNoTime();
assert.equal(missing.c.currentState,'OPEN_APP');assert.equal(missing.events.filter(e=>e[0]==='swipe').length,5);
for(const [a,b,expected,ticks] of [[20,20,'SCAN_TIME',0],[603,601,'SCAN_TIME',0],
  [31,29,'CLAIM',29],[63,61,'CLAIM',61],[32,30,'SCAN_TIME',1],[62,60,'SCAN_TIME',31],[2,0,'CLAIM',0]]){
    const e=engine();let reads=[a,b];e.c.readTimeMMSS=()=>reads.shift();e.c.scanTimeStep1();
    assert.equal(e.c.currentState,expected);
    const wait=e.events.filter(x=>x[0]==='sleep').reduce((sum,x)=>sum+x[1],0);
    assert.equal(wait,ticks+(expected==='SCAN_TIME'?7.5:5));
}
const pending=engine({getColor:()=> 'FE5C3A'});pending.c.clickBottomClaim=()=>({x:.89,y:.193});
pending.c.readTimeMMSS=()=>{throw new Error('Must check a ready reward before stale timer')};
pending.c.scanTimeStep1();assert.equal(pending.c.currentState,'CLAIM');
pending.c.clickBottomClaim=()=>{throw new Error('Do not rescan a newly found ready button')};
pending.c.tryClaim();assert.equal(pending.events.filter(e=>e[0]==='tap').length,1);
assert.equal(pending.c.currentState,'SCAN_TIME');
const denied=engine({getColor:()=> '885B60'});denied.c.clickBottomClaim=()=>({x:.89,y:.193});denied.c.tryClaim();
assert.equal(denied.events.some(e=>e[0]==='tap'),false);
const stale=engine({findText:()=>({x:.89,y:.193})});
assert.ok(stale.c.findBottomText(['nhân'],[.78,.16,.995,.43]));
const mixed=engine({findText:(word,x1,y1,x2,y2)=>{
    const p=word==='phan'?{x:.82,y:.13}:word==='nhân'?{x:.89,y:.193}:word==='nhận'?{x:.89,y:.293}:null;
    return p&&p.x>=x1&&p.x<=x2&&p.y>=y1&&p.y<=y2?p:null;
},getColor:(x,y)=>y>.25?'FE5C3A':'885B60'});
assert.equal(mixed.c.clickBottomClaim().y,.293,'Disabled upper reward must not hide a ready lower reward with a different OCR spelling');
const slow=engine();
slow.c.sleep=s=>{slow.events.push(['sleep',s]);slow.advance(s*1000+250)};
slow.c.countdown(10);
assert.equal(slow.time(),10000,'Sleep overhead is accounted for on every iteration');
const ocrDelay=engine();ocrDelay.advance(4200);
ocrDelay.c.countdown(10,0);
assert.equal(ocrDelay.time(),10000,'Deduct OCR time from the captured MM:SS value');
const expired=engine();expired.advance(12000);expired.c.countdown(10,0);
assert.equal(expired.events.some(e=>e[0]==='sleep'),false,'An expired OCR deadline does not add a second wait');
const early=engine();let checks=0;
early.c.cachedReadyClaim=()=>++checks===2?{x:.89,y:.293}:null;
early.c.countdown(555,0,true);
assert.equal(early.time(),5000,'A ready reward interrupts the long timer wait');
assert.ok(early.c.pendingClaim);
const finalReady=engine();
finalReady.c.cachedReadyClaim=()=>finalReady.time()>=3000?{x:.89,y:.293}:null;
finalReady.c.countdown(3,0,true);
assert.equal(finalReady.time(),3000);
assert.ok(finalReady.c.pendingClaim,'Check readiness at the exact deadline before a swipe');
const measured=engine({findText:()=>null});
measured.c.timerRegion=()=>[.63,.2,.995,.232];
measured.c.ocr=()=>{measured.advance(4500);return 'Xem 09:15 để nhận thưởng'};
assert.equal(measured.c.readTimeMMSS(),555);
assert.equal(measured.c.lastTimeSample.sampledAt,0);
measured.c.countdown(measured.c.lastTimeSample.seconds,measured.c.lastTimeSample.sampledAt);
assert.equal(measured.time(),555000,'09:15 produces a 555 second deadline including OCR latency');
let passes=0;const loop=engine();loop.c.currentState='SCAN_TIME';
loop.c.scanTimeStep1=()=>{if(++passes===10000)loop.c.STOP_SCRIPT=true};loop.c.mainLoop();assert.equal(passes,10000);
console.log('Calibrated script: panel layouts, MM:SS, OCR/sleep latency, early ready reward, mixed OCR spellings, no duplicate claim scan, colors, popup, countdown boundaries, recovery and 10000-loop cases passed');
