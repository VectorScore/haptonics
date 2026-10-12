const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {AudioBands,BassTrigger:B,Shepherd:S}=require('../shepherd.js');
const html=fs.readFileSync(require.resolve('../musex.html'),'utf8');for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script[1]);
for(const hz of [20,60,100,101,180,1000,12000]){const data=new Uint8Array(24001);data[hz]=255;const band=AudioBands.analyze(data,48000,48000,-100,-30);assert.equal(band.bass100>0,hz<=100,'Only bins at 20–100 Hz contribute');}
// Compare directly with raVe's reference equations across silence, sustained
// synths, kicks and closely spaced attacks (no former 150 ms cooldown).
B.reset();const history=Array(15).fill(0);
for(const level of [0,.3,.3001,...Array(20).fill(.6),0,.8,0,.7,...Array(20).fill(0)]){
 const wave=new Float32Array([level*.2,-level,0]);const peak=Math.abs(wave[1]);
 history.shift();history.push(peak);
 const expected=peak>.3&&peak>1.3*history.reduce((a,b)=>a+b,0)/15;
 assert.equal(B.step(wave,1/60),expected);assert.equal(B.strength,peak);
}
B.reset();assert.equal(B.step([.3],1/60),false);assert(B.step([-.5],1/60));
B.step([0],1/60);assert(B.step([.6],1/60),'Nearby attacks are not blocked by a cooldown');
B.reset();for(let i=0;i<30;i++)B.step([.6],1/60);assert.equal(B.spike,false,'Sustained synth settles');
B.reset();assert(B.step([.5],1/120));const saved=B.history.slice();assert.equal(B.step([.8],1/120),false);assert.deepEqual(B.history,saved);assert(B.step([.8],1/120));
B.reset();assert.equal(B.strength,0);assert(B.history.every(v=>v===0));
S.reset();S.step({low:1},.01,false,.1,false);assert.equal(S.pulse,0,'No independent LOW detector');S.step({bassPeak:.5},.01,false,.1,true);assert(S.pulse>=.4);const peak=S.pulse;
for(let i=0;i<25;i++)S.step({},.01,false,.1,false);assert(Math.abs(S.pulse-peak*.5)<1e-9);for(let i=0;i<25;i++)S.step({},.01,false,.1,false);assert.equal(S.pulse,0);
S.step({high:1},.01,false,.1,false);assert.equal(S.stars.length,1);assert.equal(S.pulse,0);
const colors={mode:'dynamic',palette:['#000000','#ffffff'],dynamic:'#000000',anyRGB:false,lastBeat:-Infinity};const box={S:{effect:2},effectColors:colors,Math};vm.createContext(box);vm.runInContext(html.slice(html.indexOf('function advanceBeatColor('),html.indexOf('function drawCustomBackground(')),box);
box.advanceBeatColor(1,1);assert.equal(colors.dynamic,'#ffffff');box.advanceBeatColor(1.01,.1);assert.equal(colors.dynamic,'#000000','No secondary strength gate or cooldown');
// One event feeds both consumers, regardless of whether the color mode is enabled.
for(const mode of ['rgb','fixed','dynamic']){B.reset();S.reset();colors.mode=mode;colors.lastBeat=-Infinity;for(let i=0;i<120;i++){const event=B.step([i%30===0?.5:0],1/60);const bands={bassPeak:B.strength,low:.5};if(event)box.advanceBeatColor(i/60,B.strength);S.step(bands,1/60,false,.1,event);if(event)assert.equal(S.pulseAge,0);}}
console.log('PASS: FFT bands, raVe waveform parity, 60 Hz cadence, no custom cooldown, shared event, independent color modes, HIGH emission, 500ms release, syntax');
// Usage counts signal-active intervals, not simply an open audio stream.
let now=1000;const usageBox={S:{playing:true,syncToWatch:true},appSettings:{allowHaptics:true},motion:{rms:0,envelope:0},usageCheckpoint:900,usageLastSaved:1000,usage:{},Date:class extends Date{static now(){return now;}},dateKey:()=> 'today',persistUsage(){},renderUsage(){}};
vm.createContext(usageBox);vm.runInContext(html.slice(html.indexOf('function recordUsage(){'),html.indexOf("window.addEventListener('pagehide',()=>{recordUsage();")),usageBox);
usageBox.recordUsage();assert.deepEqual(Object.keys(usageBox.usage),[]);
now=1100;usageBox.motion.rms=.1;usageBox.motion.envelope=.5;usageBox.recordUsage();assert.equal(usageBox.usage.today.rhythmMs,100);
now=1200;usageBox.appSettings.allowHaptics=false;usageBox.recordUsage();assert.equal(usageBox.usage.today.rhythmMs,100);
now=2200;usageBox.appSettings.allowHaptics=true;usageBox.recordUsage();assert.equal(usageBox.usage.today.rhythmMs,200,'Do not count suspended-frame gaps');
const bgBox={effectColors:{mode:'dynamic',anyRGB:false,palette:['#000000','#ffffff'],backgrounds:['#ffffff','#000000'],dynamic:'#000000'},backgroundSettings:{hex:'#000000',brightness:1,mode:'fixed'},Math};vm.createContext(bgBox);vm.runInContext(html.slice(html.indexOf('function drawCustomBackground('),html.indexOf('function effectColor(')),bgBox);const fills=[];const bgCtx={save(){},restore(){},beginPath(){},arc(){},clip(){},fillRect(){fills.push(this.fillStyle);}};bgBox.drawCustomBackground(bgCtx,100,100,0);assert.equal(fills[0],'#ffffff');fills.length=0;bgBox.effectColors.dynamic='#ffffff';bgBox.drawCustomBackground(bgCtx,100,100,0);assert.equal(fills[0],'#000000');
console.log('PASS: signal-only timing, suspended-gap limit, paired black/white backgrounds');
// Exercise actual custom update wiring with a quiet full-band analyzer and a
// strong filtered waveform: the old RMS/FFT gates must not veto raVe's result.
let filteredPeak=.5,colorEvents=0,petEvent=false;
const integration={BassTrigger:B,AudioBands,Float32Array,Uint8Array,Math,
 S:{playing:true,audioSrc:'system',syncToWatch:false},selectedPet:'none',appSettings:{reduceMotion:false},
 visualSettings:{gain:2,bass:1.25,smoothing:.15,trail:.85},audioCtx:{sampleRate:48000},
 analyser:{fftSize:2048,minDecibels:-100,maxDecibels:-30,getByteFrequencyData(a){a.fill(0);},getByteTimeDomainData(a){a.fill(128);}},
 raveBassAnalyser:{getFloatTimeDomainData(a){a.fill(0);a[10]=-filteredPeak;}},freqData:new Uint8Array(1024),waveData:null,
 Shepherd:{step(bands,dt,reduced,rms,event){petEvent=event;assert.equal(bands.bassPeak,B.strength);}},
 advanceBeatColor(){colorEvents++;},raveRenderer:{reset(){}}};
vm.createContext(integration);vm.runInContext(html.slice(html.indexOf('const motion={'),html.indexOf('// 频段表 UI')),integration);
B.reset();integration.updateBands(0);assert(petEvent);assert.equal(colorEvents,1);
assert.equal(vm.runInContext('customMotion.events.length',integration),1);
filteredPeak=0;integration.updateBands(1/60);assert.equal(petEvent,false);
filteredPeak=.7;integration.updateBands(2/60);assert(petEvent);assert.equal(colorEvents,2);
assert.equal(vm.runInContext('customMotion.events.length',integration),2);
console.log('PASS: filtered audio drives motion, bursts, pet and color without former FFT/RMS gates');
